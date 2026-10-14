"""Моніторинг графіка відключень Житомиробленерго з повідомленнями в Telegram.

Змінні середовища:
  TELEGRAM_BOT_TOKEN  токен бота від @BotFather
  TELEGRAM_CHAT_ID    id чату, куди слати повідомлення
  SUBQUEUE            ваша підчерга, напр. "4.2"
  REMIND_MINUTES      за скільки хвилин попереджати (за замовчуванням 30)

Запуск:
  python svitlo.py            перевірити і надіслати, якщо є що
  python svitlo.py --print    лише показати розібраний графік
  python svitlo.py --test     надіслати тестове повідомлення
"""

import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

URL = "https://www.ztoe.com.ua/unhooking-search.php"
TZ = ZoneInfo("Europe/Kyiv")
STATE_FILE = Path(__file__).with_name("state.json")
SLOTS_PER_DAY = 48  # півгодинні слоти
ON_COLORS = {"white", "#ffffff", "#fff", ""}


def fetch_page():
    req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (svitlo-bot)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("cp1251", errors="replace")
        except Exception as e:
            if attempt == 2:
                raise
            print(f"Помилка завантаження ({e}), повтор...", file=sys.stderr)
            time.sleep(10)


def cell_text(raw):
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).replace("\xa0", " ").strip()


def parse_schedule(page):
    """Повертає (час оновлення, {дата: {підчерга: [вимкнено? x48]}})."""
    m = re.search(r"Дата оновлення інформації\s*-\s*([^<]+)", page)
    updated = m.group(1).strip() if m else ""

    schedule = {}
    for table in re.findall(r"<table x:str.*?</table>", page, re.S):
        dates = re.findall(r'colspan="48"[^>]*>\s*<b[^>]*>\s*(\d{2}\.\d{2}\.\d{4})', table)
        if not dates:
            continue
        for d in dates:
            schedule.setdefault(d, {})
        for row in re.findall(r"<tr.*?</tr>", table, re.S):
            cells = re.findall(r"<td([^>]*)>(.*?)</td>", row, re.S)
            labels = [cell_text(c) for a, c in cells[:2]]
            sub = next((l for l in reversed(labels) if re.fullmatch(r"\d+\.\d+", l)), None)
            if not sub:
                continue
            slots = []
            for attrs, content in cells:
                bg = re.search(r"background:\s*([^;\"]+)", attrs)
                # Слотові клітинки заповнені купою &nbsp; — так їх і відрізняємо від підписів
                if bg and content.count("&nbsp;") > 3:
                    slots.append(bg.group(1).strip().lower() not in ON_COLORS)
            for i, d in enumerate(dates):
                part = slots[i * SLOTS_PER_DAY:(i + 1) * SLOTS_PER_DAY]
                if len(part) == SLOTS_PER_DAY:
                    schedule[d][sub] = part
    return updated, schedule


def to_intervals(date_str, slots):
    """[вимкнено?] x48 -> [(початок, кінець)] у вигляді datetime."""
    day = datetime.strptime(date_str, "%d.%m.%Y").replace(tzinfo=TZ)
    intervals, start = [], None
    for i, off in enumerate(slots + [False]):
        if off and start is None:
            start = i
        elif not off and start is not None:
            intervals.append((day + timedelta(minutes=30 * start), day + timedelta(minutes=30 * i)))
            start = None
    return intervals


def fmt_interval(a, b):
    end = "24:00" if b.time() == datetime.min.time() and b.date() > a.date() else b.strftime("%H:%M")
    return f"{a.strftime('%H:%M')}–{end}"


def send_telegram(text):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    data = urllib.parse.urlencode({
        "chat_id": os.environ["TELEGRAM_CHAT_ID"],
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30).read()


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"schedule": {}, "reminded": []}


def main():
    args = set(sys.argv[1:])
    if "--test" in args:
        send_telegram("✅ Тест: бот моніторингу світла працює")
        print("Тестове повідомлення надіслано")
        return

    sub = os.environ.get("SUBQUEUE", "").strip()
    remind = timedelta(minutes=int(os.environ.get("REMIND_MINUTES", "30")))
    updated, schedule = parse_schedule(fetch_page())
    if not schedule:
        sys.exit("Не вдалося знайти таблицю графіка на сторінці — можливо, змінилась розмітка сайту")

    if "--print" in args:
        print(f"Оновлено: {updated}")
        for d, subs in schedule.items():
            print(f"\n{d}")
            for s, slots in subs.items():
                ivs = ", ".join(fmt_interval(a, b) for a, b in to_intervals(d, slots)) or "без відключень"
                print(f"  {s:>5}: {ivs}")
        return

    if not sub:
        sys.exit("Вкажіть SUBQUEUE, напр. 4.2 (дивіться python svitlo.py --print)")

    state = load_state()
    old_state = json.dumps(state, sort_keys=True)
    now = datetime.now(TZ)
    messages = []

    # 1. Новий або змінений графік на якусь дату
    for d, subs in schedule.items():
        if sub not in subs:
            continue
        intervals = to_intervals(d, subs[sub])
        as_text = [fmt_interval(a, b) for a, b in intervals]
        if state["schedule"].get(d) == as_text:
            continue
        verb = "Змінився графік" if d in state["schedule"] else "Графік"
        body = "\n".join(f"🔴 {t}" for t in as_text) or "🟢 Відключень не заплановано"
        messages.append(f"📅 <b>{verb} на {d}</b> (підчерга {sub})\n{body}\n\n<i>Оновлено: {updated}</i>")
        state["schedule"][d] = as_text

    # 2. Нагадування перед відключенням
    for d, subs in schedule.items():
        for a, b in to_intervals(d, subs.get(sub, [])):
            key = a.isoformat()
            if a - remind <= now < b and key not in state["reminded"]:
                mins = int((a - now).total_seconds() // 60)
                when = f"через {mins} хв" if mins > 0 else "вже зараз"
                messages.append(f"⚠️ <b>Відключення {when}</b>: {fmt_interval(a, b)} (підчерга {sub})")
                state["reminded"].append(key)

    # Прибираємо старе, щоб state.json не ріс
    cutoff = now - timedelta(days=2)
    state["reminded"] = [k for k in state["reminded"] if datetime.fromisoformat(k) > cutoff]
    state["schedule"] = {
        d: v for d, v in state["schedule"].items()
        if datetime.strptime(d, "%d.%m.%Y").replace(tzinfo=TZ) > cutoff
    }

    for msg in messages:
        send_telegram(msg)
        print(msg, "\n")
    if json.dumps(state, sort_keys=True) != old_state:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    if not messages:
        print("Нічого нового")


if __name__ == "__main__":
    main()
