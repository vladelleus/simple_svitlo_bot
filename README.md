# svitlo_e

Стежить за [графіком відключень Житомиробленерго](https://www.ztoe.com.ua/unhooking-search.php) і пише в Telegram:

- 📅 коли з'являється або змінюється графік для вашої підчерги;
- ⚠️ за `REMIND_MINUTES` хвилин (типово 30) до кожного відключення.

## Налаштування

1. **Бот.** Напишіть [@BotFather](https://t.me/BotFather) → `/newbot` → збережіть токен.
2. **Chat ID.** Напишіть своєму боту будь-що, потім відкрийте
   `https://api.telegram.org/bot<ТОКЕН>/getUpdates` і знайдіть `"chat":{"id":...}`.
3. **Підчерга.** Знайдіть свою адресу на сайті або подивіться всі підчерги:
   `python svitlo.py --print`.

## Запуск у GitHub Actions (рекомендовано)

Працює в хмарі кожні 10 хв, навіть коли ваш комп'ютер вимкнений.

1. Запуште репозиторій на GitHub (публічний репозиторій — Actions безлімітні; у приватному
   цей розклад з'їсть більше безкоштовних 2000 хв/міс).
2. *Settings → Secrets and variables → Actions*:
   - **Secrets:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - **Variables:** `SUBQUEUE` (типово `2.2`), за бажання `REMIND_MINUTES`
3. *Actions → Перевірка відключень → Run workflow* — перший запуск надішле поточний графік.

## Локальний запуск

```powershell
pip install -r requirements.txt
$env:TELEGRAM_BOT_TOKEN = "..."; $env:TELEGRAM_CHAT_ID = "..."; $env:SUBQUEUE = "2.2"
python svitlo.py --test   # тестове повідомлення
python svitlo.py          # одна перевірка
```

Для регулярних перевірок додайте завдання в Планувальник завдань Windows кожні 10 хв.

## Тести

```powershell
python -m unittest discover -s tests -v
```

Тести працюють на збереженій копії сторінки (`tests/fixtures/`) без мережі. Workflow **CI**
на кожен push запускає тести і перевіряє, що живий сайт досі розбирається — якщо ztoe змінить
розмітку, CI впаде.
