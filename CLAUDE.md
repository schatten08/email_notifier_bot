# CLAUDE.md — Email Notifier Bot

## Что это
Python-бот для мониторинга shared mailbox в Outlook (Microsoft Graph / O365),
фильтрации писем ServiceNow (INC/RITM/SLA) и отправки уведомлений в Microsoft Teams
как Adaptive Card. Регион охвата: CIS (KZ/UZ/KG) + Middle East (UAE/QA/SA/KW/OM/JO).

## Структура
- `bot/main.py` — основной цикл, планировщик задач, `process_message()`
- `bot/parser.py` — парсинг писем, извлечение тикетов и NPR/ER данных
- `bot/teams.py` — отправка в Teams (Adaptive Card + MessageCard)
- `bot/reports.py` — сбор и отправка еженедельного отчёта по NPR/ER
- `bot/storage.py` — состояние бота (BotState, OrderedIdSet, dead-letter)
- `bot/config.py` — конфиг из .env, миграция legacy-файлов
- `data/` — токены, чекпоинты, отчёты, responsibles.json (в git, обновлять через push)

## Развёртывание
- **Основной CI/CD**: GitLab CI + self-hosted runner с тегом `oracle` на проде
- **Fallback**: GitHub Actions (`workflow_dispatch`) через SSH
- **Запуск**: `docker-compose up --build -d`
- **Первый запуск**: требует `data/o365_token.txt` (получается через браузерную авторизацию)

## Ключевые архитектурные решения (почему именно так)

### process_message() выделена отдельно
Раньше логика была инлайн в `while True`. Выделена для unit-тестов без реального O365.

### FIRST_RUN_RECENT_HOURS = 3
При первом запуске письма старше 3ч молча кешируются без отправки (защита от спама бэклогом).
Письма моложе 3ч обрабатываются как обычные. Инцидент: 2026-08-04, RITM0002315801.

### Двухуровневые паттерны имён (_AUTHORITATIVE / _FALLBACK)
"Service Recipient" — ненадёжное поле (может быть менеджер, а не сам сотрудник).
Все авторитетные поля (Employee Name, Trainee:, Title-bracket, Exit Task for X)
пробуются ПОЛНОСТЬЮ до Service Recipient — независимо от порядка в тексте письма.

### Атомарное сохранение через .tmp + os.replace()
Везде: checkpoint, report, dead-letter. Исключает повреждённые файлы при падении.

### extract_report_data() вызывается ДО quick dedup
Один RITM порождает несколько писем (assigned → resolved). Данные о сотруднике
только в финальном письме. Если вызывать после dedup — финальное письмо пропускается.

### Dead-letter (data/dead_letters.json)
Письма, похожие на финальное NPR/ER-событие, но не распарсенные — подозрение на
дрифт формата ServiceNow. Счётчик виден в ежедневном health-check.
GDPR: в dead-letter только reason + ticket_id, без ФИО и subject.

### Table-based парсинг (_extract_table_fields)
Реальные письма ServiceNow — табличная структура HTML. Граница значения задана
тегом, а не regex со списком стоп-слов. Используется для Location и Dismissal Date.
Fallback на regex для нетабличных писем (все синтетические тесты).

## Известные ограничения / технический долг
- Глобальный `state` в storage.py мутируется из teams.py напрямую — хрупко при тестировании
- Планировщик встроен в while-цикл (сравнение по `now_utc.hour >= N`) — нет нормального cron
- Плановый рестарт через `sys.exit(0)` каждые 12ч — костыль вместо обновления токена
- `data/responsibles.json` живёт в git и перезатирается при каждом деплое (git reset --hard)
- `nul` файл и `__pycache__` в репозитории — нужно убрать

## Git remotes
- `github` → `git@github.com:schatten08/email_notifier_bot.git` (основной для правок с Claude)
- `origin` → `git@gitlab.com:schatten08-group/email-notifier-bot.git` (прод, CI/CD)