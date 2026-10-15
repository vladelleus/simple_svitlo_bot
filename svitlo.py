"""Monitors the Zhytomyroblenergo power outage schedule and notifies via Telegram.

Environment variables:
  TELEGRAM_BOT_TOKEN  bot token from @BotFather
  TELEGRAM_CHAT_ID    chat/channel id to send messages to
  SUBQUEUE            your subqueue, e.g. "2.2"
  REMIND_MINUTES      how many minutes before an outage to warn (default 30)
  CITY_ID, STREET_ID  address ids on ztoe-poweron.inneti.net for live outage checks
                      (see the request URL in browser dev tools); check skipped if unset
  BUILDING            optional house number, e.g. "17а"; without it any outage
                      on the street counts

Usage:
  python svitlo.py            check and send notifications if anything is new
  python svitlo.py --print    only print the parsed schedule
  python svitlo.py --test     send a test message
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
API_BASE = "https://api-ztoe-poweron.inneti.net"
TZ = ZoneInfo("Europe/Kyiv")
STATE_FILE = Path(__file__).with_name("state.json")
SLOTS_PER_DAY = 48  # half-hour slots
ON_COLORS = {"white", "#ffffff", "#fff", ""}
# How long into a scheduled outage to wait for it to show up in the live API
VERIFY_DELAY = timedelta(minutes=10)


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


def fetch_accidents(city_id, street_id):
    """All currently active outages for the street (follows hydra pagination)."""
    query = urllib.parse.urlencode({"city.id": city_id, "street.id": street_id})
    path, members = f"/api/pw_accidents?{query}", []
    for _ in range(20):
        req = urllib.request.Request(
            API_BASE + path,
            headers={"Accept": "application/ld+json", "User-Agent": "Mozilla/5.0 (svitlo-bot)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        members += data.get("hydra:member", [])
        path = data.get("hydra:view", {}).get("hydra:next")
        if not path:
            break
    return members


def parse_api_time(value):
    # The API labels Kyiv local time as "+00:00" (the site renders it with timeZone: "UTC")
    return datetime.fromisoformat(value[:19]).replace(tzinfo=TZ) if value else None


def accident_kind(acc):
    """Same labels the ztoe-poweron site shows."""
    by_reason = {1: "Графік аварійних відключень", 2: "Графік погодинних відключень",
                 3: "Спеціальний графік аварійних відключень"}
    by_type = {0: "Планове відключення", 1: "Аварійне відключення", 2: "Оперативні перемикання"}
    reason = acc.get("switchSubReason") or 0
    return by_reason.get(reason) if reason > 0 else by_type.get(acc.get("typeOff"), "Відключення")


def affects_building(acc, building):
    if not building:
        return True
    houses = {h.strip().lower() for h in (acc.get("buildingNames") or "").split(",")}
    return building.strip().lower() in houses


def cell_text(raw):
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).replace("\xa0", " ").strip()


def parse_schedule(page):
    """Returns (update time, {date: {subqueue: [is_off] x48}})."""
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
                # Slot cells are padded with lots of &nbsp; — that is how we tell them from labels
                if bg and content.count("&nbsp;") > 3:
                    slots.append(bg.group(1).strip().lower() not in ON_COLORS)
            for i, d in enumerate(dates):
                part = slots[i * SLOTS_PER_DAY:(i + 1) * SLOTS_PER_DAY]
                if len(part) == SLOTS_PER_DAY:
                    schedule[d][sub] = part
    return updated, schedule


def to_intervals(date_str, slots):
    """[is_off] x48 -> [(start, end)] as datetimes."""
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
    state = {"schedule": {}, "reminded": [], "verified": [], "accidents": {}}
    if STATE_FILE.exists():
        state.update(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return state


def print_schedule(updated, schedule):
    print(f"Оновлено: {updated}")
    for d, subs in schedule.items():
        print(f"\n{d}")
        for s, slots in subs.items():
            ivs = ", ".join(fmt_interval(a, b) for a, b in to_intervals(d, slots)) or "без відключень"
            print(f"  {s:>5}: {ivs}")


def check_schedule(state, now, messages):
    sub = os.environ.get("SUBQUEUE", "").strip()
    if not sub:
        # "::warning::" shows up as a yellow annotation on the GitHub Actions run page
        print("::warning::SUBQUEUE не задано — графік не перевіряю")
        return None
    remind = timedelta(minutes=int(os.environ.get("REMIND_MINUTES", "30")))
    updated, schedule = parse_schedule(fetch_page())
    if not schedule:
        raise RuntimeError("Не вдалося знайти таблицю графіка на сторінці — можливо, змінилась розмітка сайту")

    # 1. New or changed schedule for some date
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

    # 2. Reminders before an outage. The schedule is only a plan, hence "можливе"
    intervals = [iv for d, subs in schedule.items() for iv in to_intervals(d, subs.get(sub, []))]
    for a, b in intervals:
        key = a.isoformat()
        if a - remind <= now < b and key not in state["reminded"]:
            mins = int((a - now).total_seconds() // 60)
            when = f"через {mins} хв" if mins > 0 else "вже зараз"
            messages.append(
                f"⚠️ <b>За графіком можливе відключення {when}</b>: {fmt_interval(a, b)} (підчерга {sub})"
            )
            state["reminded"].append(key)
    return intervals


def check_accidents(state, now, messages):
    """Live outages at the address: notify when power goes off, back on, or ETA changes.

    Returns the active outages, or None when the address is not configured.
    """
    city, street = os.environ.get("CITY_ID", "").strip(), os.environ.get("STREET_ID", "").strip()
    if not (city and street):
        print("::warning::CITY_ID/STREET_ID не задано — фактичний стан світла не перевіряю")
        return None
    building = os.environ.get("BUILDING", "").strip()

    active = {}
    for acc in fetch_accidents(city, street):
        if affects_building(acc, building):
            active[str(acc["id"])] = acc

    # Only ids and times go to state.json: the repo is public, the address must not leak
    known = state["accidents"]
    for acc_id, acc in active.items():
        start, eta = parse_api_time(acc.get("dateEvent")), parse_api_time(acc.get("datePlanIn"))
        eta_text = eta.strftime("%H:%M %d.%m") if eta else "невідомо"
        note = f"\n<i>{html.escape(acc['koment'])}</i>" if acc.get("koment") else ""
        if acc_id not in known:
            since = start.strftime("%H:%M %d.%m") if start else "невідомо"
            messages.append(
                f"🔴 <b>Світло вимкнули</b> — {accident_kind(acc)}\n"
                f"З {since}, орієнтовно до {eta_text}{note}"
            )
        elif known[acc_id].get("eta") != acc.get("datePlanIn"):
            messages.append(f"⏱ <b>Змінився час увімкнення</b>: орієнтовно до {eta_text}{note}")
        known[acc_id] = {"eta": acc.get("datePlanIn")}

    ended = [acc_id for acc_id in known if acc_id not in active]
    for acc_id in ended:
        del known[acc_id]
    if ended and not active:
        messages.append("🟢 <b>Відключення завершилось</b> — світло мало з'явитися")
    return active


def compare_with_actual(state, now, messages, intervals, active):
    """Some time into a scheduled outage, tell whether the power is actually still on."""
    for a, b in intervals:
        key = a.isoformat()
        if a + VERIFY_DELAY <= now < b and key not in state["verified"]:
            state["verified"].append(key)
            # If it is off, the 🔴 from check_accidents already said so
            if not active:
                messages.append(
                    f"💡 <b>Світло є</b>, хоча за графіком зараз відключення {fmt_interval(a, b)}"
                )


def main():
    args = set(sys.argv[1:])
    if "--test" in args:
        send_telegram("✅ Тест: бот моніторингу світла працює")
        print("Тестове повідомлення надіслано")
        return
    if "--print" in args:
        print_schedule(*parse_schedule(fetch_page()))
        return

    state = load_state()
    old_state = json.dumps(state, sort_keys=True)
    now = datetime.now(TZ)
    messages, failed, results = [], False, {}

    # Each source is checked independently so one site being down doesn't block the other
    for check in (check_schedule, check_accidents):
        try:
            results[check] = check(state, now, messages)
        except Exception as e:
            failed = True
            print(f"Помилка в {check.__name__}: {e!r}", file=sys.stderr)

    intervals, active = results.get(check_schedule), results.get(check_accidents)
    if intervals is not None and active is not None:
        compare_with_actual(state, now, messages, intervals, active)

    # Drop old entries so state.json does not grow
    cutoff = now - timedelta(days=2)
    for key in ("reminded", "verified"):
        state[key] = [k for k in state[key] if datetime.fromisoformat(k) > cutoff]
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
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
