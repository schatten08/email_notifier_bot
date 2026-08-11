import logging
import re
from datetime import datetime, timedelta, timezone
from bot.config import TEAMS_REPORT_WEBHOOK_URL, TEAMS_WEBHOOK_URL, TEAMS_MIDDLE_EAST_WEBHOOK_URL
from bot.storage import load_report, save_report
from bot.parser import parse_employee_info
from bot.teams import send_teams_notification

logger = logging.getLogger(__name__)

# Реальная граница ТЕКУЩЕГО отчётного окна - начало текущей недели (понедельник
# 00:00 UTC). Ровно та же граница, которую bot/main.py использует, чтобы решить,
# полностью пропустить письмо ("message.received < monday_start") или обработать
# его как письмо текущей недели. Событие, дата которого РАНЬШЕ этой границы,
# гарантированно уже было учтено в ОДНОМ из прошлых, уже отправленных
# еженедельных отчётов (при условии, что бот работал непрерывно) - такую
# запись нужно отбросить, а не задублировать в текущем отчёте.
#
# Раньше здесь использовалась эвристика "если дата события старше даты письма
# больше чем на N дней" - число N подбиралось вручную и не было привязано к
# реальной границе отчётного цикла. У такой эвристики есть слепая зона: событие
# прошлой недели, дочерний тикет по которому пришёл всего через несколько дней
# (меньше подобранного N) - НЕ отфильтровывался бы и задублировался в отчёте
# этой недели. Сравнение с monday_start устраняет эту зону полностью, т.к.
# использует ту же границу, что и реальный цикл сбора отчёта, а не угаданное число.
def _current_report_window_start(now_utc=None):
    """Возвращает начало текущей отчётной недели (понедельник 00:00 UTC)."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    monday_start = now_utc - timedelta(days=now_utc.weekday())
    return monday_start.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_report_date(raw_date):
    """
    Разбирает дату события из info['date'] (форматы: 'YYYY-MM-DD' или
    'DD Mon YYYY', либо 'Unknown'/None). Возвращает naive datetime или None,
    если дату не удалось распознать.
    """
    if not raw_date or raw_date == 'Unknown':
        return None
    raw_date = str(raw_date).strip()
    for fmt in ('%Y-%m-%d', '%d %b %Y'):
        try:
            return datetime.strptime(raw_date, fmt)
        except ValueError:
            continue
    return None


def extract_report_data(full_text, subject, received_date=None, is_middle_east=False, report_window_start=None):
    """
    Вызывается для каждого письма, чтобы наполнить еженедельный отчет.

    report_window_start - начало ТЕКУЩЕГО отчётного окна (понедельник 00:00 UTC).
    Должен передаваться вызывающим кодом (bot/main.py), который уже вычисляет
    эту границу для собственной логики фильтрации писем ("message.received <
    monday_start"), чтобы обе проверки использовали ОДНУ и ту же границу. Если
    не передан (например, в одноразовых/тестовых скриптах), вычисляется
    самостоятельно от текущего момента.
    """
    info = parse_employee_info(full_text, subject)
    if not info:
        return

    d = load_report(is_me=is_middle_east)
    name = info.pop('name')

    # ВАЖНО: info['date'] уже содержит РЕАЛЬНУЮ дату события, извлечённую
    # parse_employee_info() из тела письма. Раньше эта дата безусловно
    # перезатиралась датой ПОЛУЧЕНИЯ письма (received_date), из-за чего отчёт
    # показывал дату письма вместо даты реального увольнения/выхода.
    # received_date теперь используется ТОЛЬКО как fallback, если реальную
    # дату события не удалось извлечь (info['date'] отсутствует/'Unknown').
    event_date = _parse_report_date(info.get('date'))
    if event_date is None and received_date:
        info['date'] = received_date.strftime('%Y-%m-%d')
        event_date = received_date.replace(tzinfo=None) if received_date.tzinfo else received_date

    if event_date is not None:
        window_start = report_window_start if report_window_start is not None else _current_report_window_start()
        window_start_naive = window_start.replace(tzinfo=None) if window_start.tzinfo else window_start
        if event_date < window_start_naive:
            logger.warning(
                f"Событие для {name} датировано {info.get('date')}, это раньше начала "
                f"текущего отчётного окна ({window_start_naive.strftime('%Y-%m-%d')}). "
                f"Уже учтено в одном из прошлых отчётов - пропускаю, чтобы не "
                f"задублировать событие в чужом отчётном окне."
            )
            return

    if name not in d:
        d[name] = info
        save_report(d, is_me=is_middle_east)

def send_weekly_report(is_me=False):
    """Формирует и отправляет еженедельный отчет."""
    data = load_report(is_me=is_me)
    if not data:
        logger.info(f"Отчет {'ME' if is_me else 'CIS'} пуст, отправка отменена.")
        return

    grouped = {}
    for name, info in data.items():
        city = info.get('city', 'Other')
        if city not in grouped:
            grouped[city] = {'NPR': [], 'ER': []}
        
        raw_date = str(info.get('date', 'Unknown'))
        formatted_date = raw_date
        
        if re.match(r'\d{4}-\d{2}-\d{2}', raw_date):
            try:
                dt = datetime.strptime(raw_date, '%Y-%m-%d')
                formatted_date = dt.strftime('%d %b %Y')
            except:
                pass

        ticket_label = info.get('ticket_id', 'ServiceNow')
        entry = f"{name} ({info.get('type', 'NPR')}) | {formatted_date} | [{ticket_label} | ServiceNow]({info.get('link', '#')})"
        grouped[city][info.get('type', 'NPR')].append(entry)

    today = datetime.now()
    week_ago = today - timedelta(days=7)
    date_range = f"({week_ago.strftime('%d %b')} - {today.strftime('%d %b %Y')})"
    prefix = "🌍 **Middle East**" if is_me else "📊 **Weekly**"
    report_msg = f"{prefix} **Employee Report** {date_range}\n"
    
    if is_me:
        CITY_ORDER = ["Dubai", "Abu Dhabi", "Qatar", "Saudi Arabia", "Kuwait", "Oman", "Jordan"]
    else:
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
    
    if is_me:
        report_wh = TEAMS_MIDDLE_EAST_WEBHOOK_URL
    else:
        report_wh = TEAMS_REPORT_WEBHOOK_URL if TEAMS_REPORT_WEBHOOK_URL else TEAMS_WEBHOOK_URL
        
    send_teams_notification(report_msg, webhook_url=report_wh)
    
    save_report({}, is_me=is_me)
    logger.info(f"Еженедельный отчет {'ME' if is_me else 'CIS'} отправлен и очищен.")
