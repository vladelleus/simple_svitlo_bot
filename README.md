# svitlo_e

[![Перевірка відключень](https://github.com/vladelleus/simple_svitlo_bot/actions/workflows/check.yml/badge.svg?event=workflow_dispatch)](https://github.com/vladelleus/simple_svitlo_bot/actions/workflows/check.yml)

Стежить за [графіком відключень Житомиробленерго](https://www.ztoe.com.ua/unhooking-search.php) і пише в Telegram:

- 📅 коли з'являється або змінюється графік для вашої підчерги;
- ⚠️ за `REMIND_MINUTES` хвилин (типово 30) до кожного відключення за графіком («можливе» —
  графік лише план);
- 💡 якщо через 10 хв після початку відключення за графіком фактично світло є;
- 🔴 коли світло за вашою адресою фактично вимкнули (будь-яке діюче відключення з
  [ztoe-poweron.inneti.net](https://ztoe-poweron.inneti.net/): графік, аварійне, планове),
  ⏱ коли змінився орієнтовний час увімкнення і 🟢 коли відключення завершилось.

## Налаштування

1. **Бот.** Напишіть [@BotFather](https://t.me/BotFather) → `/newbot` → збережіть токен.
2. **Chat ID.** Напишіть своєму боту будь-що, потім відкрийте
   `https://api.telegram.org/bot<ТОКЕН>/getUpdates` і знайдіть `"chat":{"id":...}`.
3. **Підчерга.** Знайдіть свою адресу на сайті або подивіться всі підчерги:
   `python svitlo.py --print`.
4. **Адреса для діючих відключень.** На [ztoe-poweron.inneti.net](https://ztoe-poweron.inneti.net/)
   оберіть місто й вулицю, у DevTools → Network знайдіть запит `pw_accidents?...&city.id=…&street.id=…`
   — це `CITY_ID` і `STREET_ID`. `BUILDING` — номер будинку (необов'язково; без нього
   спрацює будь-яке відключення на вулиці).

## Запуск у GitHub Actions (рекомендовано)

Працює в хмарі кожні 5 хв, навіть коли ваш комп'ютер вимкнений. Запускає його
[cron-job.org](https://cron-job.org): вбудований розклад GitHub (`schedule`) часто запізнюється
або пропускає запуски, тому його прибрано.

1. Запуште репозиторій на GitHub (публічний репозиторій — Actions безлімітні; у приватному
   цей розклад з'їсть більше безкоштовних 2000 хв/міс).
2. *Settings → Secrets and variables → Actions*:
   - **Secrets:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - **Variables:** `SUBQUEUE` (типово `2.2`), `CITY_ID`, `STREET_ID`, за бажання `BUILDING`
     і `REMIND_MINUTES`. Адресу тримайте саме у Variables, а не в коді — репозиторій публічний.
3. *Actions → Перевірка відключень → Run workflow* — перший запуск надішле поточний графік.
4. **Запуск кожні 5 хв.** Створіть [fine-grained токен](https://github.com/settings/personal-access-tokens/new)
   лише для цього репозиторію з правом *Actions: Read and write*. На cron-job.org створіть задачу
   «Every 5 minutes»:
   - `POST https://api.github.com/repos/vladelleus/simple_svitlo_bot/actions/workflows/check.yml/dispatches`
   - заголовки `Authorization: Bearer <токен>`, `Accept: application/vnd.github+json`
   - тіло `{"ref":"main"}`

   Відповідь має бути 2xx. Коли токен спливе, запуски зупиняться — увімкніть сповіщення про
   помилки в cron-job.org.

## Локальний запуск

```powershell
pip install -r requirements.txt
$env:TELEGRAM_BOT_TOKEN = "..."; $env:TELEGRAM_CHAT_ID = "..."; $env:SUBQUEUE = "2.2"
$env:CITY_ID = "..."; $env:STREET_ID = "..."; $env:BUILDING = "..."
python svitlo.py --test   # тестове повідомлення
python svitlo.py          # одна перевірка
```

Для регулярних перевірок додайте завдання в Планувальник завдань Windows кожні 5 хв.

## Тести

```powershell
python -m unittest discover -s tests -v
```

Тести працюють на збереженій копії сторінки (`tests/fixtures/`) без мережі. Workflow **CI**
на кожен push запускає тести і перевіряє, що живий сайт досі розбирається — якщо ztoe змінить
розмітку, CI впаде.
