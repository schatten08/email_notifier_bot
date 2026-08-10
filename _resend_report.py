"""
Одноразовый скрипт для пересчёта и переотправки еженедельного CIS-отчёта
за период 31 Jul - 07 Aug 2026 с исправленным парсером (без ложных NPR
из hardware-тикетов, без потери реальных NPR/ER из-за quick-dedup бага).

Не трогает data/weekly_report.json (там уже копятся данные СЛЕДУЮЩЕЙ недели) -
собирает данные в отдельный временный словарь и строит отчёт вручную, минуя
load_report()/save_report().

Запуск внутри контейнера:
    sudo docker exec email_notifier_bot python3 /app/_resend_report.py
"""
import sys
sys.path.insert(0, '/app')

from datetime import datetime, timezone
import re

from bot.config import TEAMS_REPORT_WEBHOOK_URL, TEAMS_WEBHOOK_URL, validate_required_config
from bot.parser import cleanup_html, is_middle_east_message, parse_employee_info
from bot.teams import send_teams_notification
from bot.main import authenticate_outlook

validate_required_config()

# Период предыдущего (уже отправленного, но некорректного) отчёта.
# ВАЖНО: основной цикл бота (bot/main.py) пропускает письма, полученные ДО
# начала текущей недели (monday_start = понедельник 00:00 UTC текущей недели),
# они никогда не доходят до extract_report_data(). Поэтому реальное окно сбора
# данных для отчёта, отправленного в пятницу 07 Aug 12:00 UTC, - это
# понедельник 03 Aug 00:00 UTC - пятница 07 Aug 12:00 UTC (НЕ 7 календарных
# дней назад, как можно подумать по тексту "(31 Jul - 07 Aug 2026)" в самом
# отчёте - эта строка в reports.py считается независимо и не отражает
# реальное окно сбора).
RANGE_START = datetime(2026, 8, 3, 0, 0, 0, tzinfo=timezone.utc)
RANGE_END = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)  # отчёт был отправлен в пятницу в 12:00 UTC

print(f"--- Пересчёт CIS-отчёта за период {RANGE_START} - {RANGE_END} ---")

account = authenticate_outlook()
mailbox = account.mailbox()

messages = mailbox.get_messages(limit=3000, download_attachments=False)

collected = {}

for message in messages:
    received = getattr(message, 'received', None)
    if not received:
        continue
    if received < RANGE_START or received > RANGE_END:
        continue

    subject = message.subject
    clean_body = cleanup_html(message.body)
    full_text = subject + " " + clean_body

    all_rec_info = []
    try:
        for r in message.to:
            all_rec_info.append(r.address.lower())
            if r.name:
                all_rec_info.append(r.name.lower())
        if hasattr(message, 'cc'):
            for r in message.cc:
                all_rec_info.append(r.address.lower())
                if r.name:
                    all_rec_info.append(r.name.lower())
    except (AttributeError, TypeError):
        pass

    is_me = is_middle_east_message(message, all_rec_info, clean_body)
    if is_me:
        continue  # интересует только CIS-отчёт

    # ВАЖНО: НЕ используем здесь дополнительный префильтр вида
    # 'if "NPR" in full_text or "ER" in full_text or ...' (как в
    # test_report_logic() в bot/main.py) - это более узкий и неточный фильтр,
    # чем финальная логика внутри parse_employee_info(). В РЕАЛЬНОМ основном
    # цикле бота (main.py, обычный режим работы) extract_report_data()
    # вызывается для каждого письма без такого префильтра, вся фильтрация
    # происходит внутри parse_employee_info(). Использование этого префильтра
    # здесь привело бы к повторной потере легитимных записей (например,
    # Exit Request для Assem Dossova не содержит слова "ER" как отдельного
    # слова в тексте письма, но корректно распознаётся как ER через ключевые
    # слова 'Dismount'/'Exit Request' внутри parse_employee_info()).

    info = parse_employee_info(full_text, subject)
    if not info:
        continue

    name = info.pop('name')
    if not name:
        # Известный отдельный баг: длинные несвязанные email-переписки иногда
        # случайно содержат фразу "has been provided" где-то в цитируемом
        # тексте, что триггерит is_final=True в parse_employee_info(), но
        # регэкспы для имени сотрудника не находят совпадений (письмо не о
        # сотруднике вообще). Такие безымянные записи - чистый шум, не
        # относятся к задаче переотправки корректного NPR/ER отчёта, поэтому
        # пропускаем их здесь.
        print(f"  ! Пропущена запись без имени (шум): {subject[:80]}")
        continue
    info['date'] = received.strftime('%Y-%m-%d')

    if name not in collected:
        collected[name] = info
        print(f"  + {name} ({info['type']}, {info['city']}) - {subject[:70]}")

print(f"\nВсего найдено записей: {len(collected)}")

# --- Строим отчёт вручную (та же логика форматирования, что и в send_weekly_report) ---
grouped = {}
for name, info in collected.items():
    city = info.get('city', 'Other')
    if city not in grouped:
        grouped[city] = {'NPR': [], 'ER': []}

    raw_date = str(info.get('date', 'Unknown'))
    formatted_date = raw_date
    if re.match(r'\d{4}-\d{2}-\d{2}', raw_date):
        try:
            dt = datetime.strptime(raw_date, '%Y-%m-%d')
            formatted_date = dt.strftime('%d %b %Y')
        except Exception:
            pass

    ticket_label = info.get('ticket_id', 'ServiceNow')
    entry = f"{name} ({info.get('type', 'NPR')}) | {formatted_date} | [{ticket_label} | ServiceNow]({info.get('link', '#')})"
    grouped[city][info.get('type', 'NPR')].append(entry)

date_range = "(03 Aug - 07 Aug 2026)"
report_msg = f"📊 **Weekly** **Employee Report** {date_range} [ИСПРАВЛЕННЫЙ ПОВТОР]\n"

CITY_ORDER = ["Almaty", "Astana", "Bishkek", "Karaganda", "Tashkent"]
sorted_cities = CITY_ORDER + sorted([c for c in grouped if c not in CITY_ORDER])

report_msg += "Summary:\n"
for city in sorted_cities:
    city_data = grouped.get(city, {'NPR': [], 'ER': []})
    npr_count = len(city_data['NPR'])
    er_count = len(city_data['ER'])
    report_msg += f"• **{city}**: NPR: {npr_count}, ER: {er_count}  \n"

report_msg += "\nDetails:\n"
for city in sorted_cities:
    city_data = grouped.get(city, {'NPR': [], 'ER': []})
    all_people = city_data['NPR'] + city_data['ER']
    if not all_people:
        continue
    report_msg += f"📍 **{city}:**\n"
    for person_line in all_people:
        report_msg += f"• {person_line}  \n"
    report_msg += "\n"

print("\n--- ГОТОВЫЙ ОТЧЁТ ---")
print(report_msg)
print("--- КОНЕЦ ОТЧЁТА ---\n")

report_wh = TEAMS_REPORT_WEBHOOK_URL if TEAMS_REPORT_WEBHOOK_URL else TEAMS_WEBHOOK_URL

confirm = input("Отправить этот отчёт в Teams? (yes/no): ").strip().lower()
if confirm == "yes":
    send_teams_notification(report_msg, webhook_url=report_wh)
    print("Отправлено.")
else:
    print("Отправка отменена.")
