import os
import time
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
import requests
import json
import sys
from dotenv import load_dotenv
from O365 import Account
import asyncio

# Загружаем настройки
load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
TENANT_ID = os.getenv('TENANT_ID')
TEAMS_WEBHOOK_URL = os.getenv('TEAMS_WEBHOOK_URL')
TEAMS_REPORT_WEBHOOK_URL = os.getenv('TEAMS_REPORT_WEBHOOK_URL')
TARGET_EMAIL = os.getenv('TARGET_EMAIL')
UPTIME_KUMA_PUSH_URL = os.getenv('UPTIME_KUMA_PUSH_URL')

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Глобальные переменные для статистики
start_time = datetime.now()
emails_checked = 0
tickets_sent = 0
REPORT_FILE = "weekly_report.json"
CHECKPOINT_FILE = "bot_checkpoint.json"

# Хранилище ID обработанных писем и тикетов
processed_emails = set()
notified_tickets = set()

def load_checkpoint():
    """Загружает ID обработанных писем и тикетов из файла."""
    global processed_emails, notified_tickets
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                processed_emails = set(data.get('processed_emails', []))
                notified_tickets = set(data.get('notified_tickets', []))
                logger.info(f"Чекпоинт загружен: {len(processed_emails)} писем, {len(notified_tickets)} тикетов.")
        except Exception as e:
            logger.error(f"Ошибка при загрузке чекпоинта: {e}")

def save_checkpoint():
    """Сохраняет ID обработанных писем и тикетов в файл."""
    try:
        data = {
            'processed_emails': list(processed_emails),
            'notified_tickets': list(notified_tickets)
        }
        with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Ошибка при сохранении чекпоинта: {e}")

def load_report():
    if os.path.exists(REPORT_FILE):
        if os.path.isdir(REPORT_FILE):
            logger.error(f"Критическая ошибка: {REPORT_FILE} является директорией!")
            return {}
        with open(REPORT_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if "npr" in data and isinstance(data["npr"], list):
                    return {}
                return data
            except Exception as e:
                logger.error(f"Ошибка при чтении отчета: {e}")
                return {}
    return {}

def save_report(data):
    try:
        with open(REPORT_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Ошибка при сохранении отчета: {e}")

def parse_employee_info(full_text, subject):
    """
    Вспомогательная функция для извлечения данных о сотруднике (NPR/ER).
    Возвращает словарь с данными или None.
    """
    # 1. Final state detection
    is_final = any(kw in subject.lower() for kw in ['resolved', 'closed', 'exit task', 'completed'])
    if not is_final:
        return None
    
    # 2. Identify types
    is_npr = 'NPR' in full_text and 'Prepare workstation' in full_text
    is_er = 'ER' in full_text and ('Dismount' in full_text or 'Exit' in full_text)
    is_trans = 'Transformation from Trainee' in full_text
    
    if not (is_npr or is_er or is_trans):
        return None
    
    # 3. Skip Students / Trainees (unless it is a Transformation)
    if not is_trans:
        if re.search(r'(Title|Employee title|Employment type):.*(Student|Trainee)', full_text, re.IGNORECASE):
            return None
    
    # 4. Extract Name
    name = None
    if is_trans:
        m = re.search(r'Trainee:\s*(.*?)(?:,| effective)', full_text, re.IGNORECASE)
        if m: name = m.group(1).strip()
    else:
        m = re.search(r'Title:\s*([A-Z][a-z]+ [A-Z][a-z]+)', full_text)
        if not m:
            m = re.search(r'(?:NPR|ER)\s*\([^)]+\)\s*\(([^)]+)\)', full_text)
        if m: name = m.group(1).strip()
        
    if not name:
        return None
    
    # 5. Date extraction
    req_date = "Unknown"
    m_date = re.search(r'(?:effective from|Dismissal Date|Start Date|First Working Day)[:\s]*(\d{4}-\d{2}-\d{2}|\d+\s*[A-Z][a-z]+\s*\d{4})', full_text, re.IGNORECASE)
    if m_date:
        req_date = m_date.group(1).split(' ')[0] if '-' in m_date.group(1) else m_date.group(1)
    else:
        m_title_date = re.search(r'(?:NPR|ER|Transformation)[^()]*\(([^)]+)\)', subject, re.IGNORECASE)
        if m_title_date:
            req_date = m_title_date.group(1)

    # 6. Resolve City (Strict)
    city = None
    # Сначала ищем строго в поле Location
    m_loc = re.search(r'Location:\s*(.*?)(?:\n|Description|Service|Priority|Title|$)', full_text, re.IGNORECASE)
    if m_loc:
        loc_val = m_loc.group(1).lower()
        for c in ['Almaty', 'Astana', 'Bishkek', 'Karaganda', 'Tashkent']:
            if c.lower() in loc_val:
                city = c
                break
    
    if not city:
        # Fallback to body scan
        t = full_text.lower()
        for c in ['Almaty', 'Astana', 'Bishkek', 'Karaganda', 'Tashkent']:
            if c.lower() in t:
                city = c
                break
                
    if not city:
        return None

    # 7. Link / Ticket ID
    link = "#"
    ticket_id = "ServiceNow"
    id_match = re.search(r'(RITM\d+|SCTASK\d+)', full_text)
    if id_match:
        ticket_id = id_match.group(1)

    sn_link_match = re.search(r'(https://[^/]*\.service-now\.com/\S+)', full_text)
    if sn_link_match:
        link = sn_link_match.group(1).rstrip('.')
    else:
        people_link_match = re.search(r'(https://processes\.people\.epam\.com/\S+)', full_text)
        if people_link_match:
            link = people_link_match.group(1).rstrip('.')

    return {
        'name': name,
        'city': city,
        'type': 'NPR' if (is_npr or is_trans) else 'ER',
        'date': req_date,
        'link': link,
        'ticket_id': ticket_id
    }

def extract_report_data(full_text, subject):
    """Вызывается для каждого письма, чтобы наполнить еженедельный отчет."""
    info = parse_employee_info(full_text, subject)
    if not info:
        return

    d = load_report()
    name = info.pop('name')
    if name not in d:
        d[name] = info
        save_report(d)


def send_weekly_report():
    data = load_report()
    if not data:
        logger.info("Отчет пуст, отправка отменена.")
        return

    # Группируем данные
    grouped = {}
    for name, info in data.items():
        city = info.get('city', 'Other')
        if city not in grouped:
            grouped[city] = {'NPR': [], 'ER': []}
        
        # Красивое форматирование даты
        raw_date = str(info.get('date', 'Unknown'))
        formatted_date = raw_date
        
        # Если дата в формате 2026-06-15, конвертируем в 15 Jun 2026
        if re.match(r'\d{4}-\d{2}-\d{2}', raw_date):
            try:
                dt = datetime.strptime(raw_date, '%Y-%m-%d')
                formatted_date = dt.strftime('%d %b %Y')
            except:
                pass

        ticket_label = info.get('ticket_id', 'ServiceNow')
        entry = f"{name} ({info.get('type', 'NPR')}) | {formatted_date} | [{ticket_label} | ServiceNow]({info.get('link', '#')})"
        grouped[city][info.get('type', 'NPR')].append(entry)

    # 1. Заголовок
    report_msg = "📊 **Weekly Employee Report**\n"
    
    # Сортируем города точно как в скрине (Almaty, Astana, Bishkek, Karaganda, Tashkent)
    CITY_ORDER = ["Almaty", "Astana", "Bishkek", "Karaganda", "Tashkent"]
    sorted_cities = [c for c in CITY_ORDER if c in grouped] + sorted([c for c in grouped if c not in CITY_ORDER])
    
    # 2. Summary
    report_msg += "Summary:\n"
    # Сортируем города точно как в скрине (Almaty, Astana, Bishkek, Karaganda, Tashkent)
    CITY_ORDER = ["Almaty", "Astana", "Bishkek", "Karaganda", "Tashkent"]
    
    for city in CITY_ORDER:
        # Берем данные из grouped, если города там нет, ставим нули
        city_data = grouped.get(city, {'NPR': [], 'ER': []})
        npr_count = len(city_data['NPR'])
        er_count = len(city_data['ER'])
        report_msg += f"• **{city}**: NPR: {npr_count}, ER: {er_count}  \n"
    
    # Добавляем города, которых нет в CITY_ORDER, но которые есть в данных
    for city in sorted(grouped.keys()):
        if city not in CITY_ORDER:
            npr_count = len(grouped[city]['NPR'])
            er_count = len(grouped[city]['ER'])
            report_msg += f"• **{city}**: NPR: {npr_count}, ER: {er_count}  \n"
    
    report_msg += "\nDetails:\n"
    
    # 3. Details
    for city in sorted_cities:
        report_msg += f"📍 **{city}:**\n"
        
        # Сначала NPR, потом ER
        all_people = grouped[city]['NPR'] + grouped[city]['ER']
        for person_line in all_people:
            report_msg += f"• {person_line}  \n"
        
        report_msg += "\n"
    
    report_wh = TEAMS_REPORT_WEBHOOK_URL if TEAMS_REPORT_WEBHOOK_URL else TEAMS_WEBHOOK_URL
    send_teams_notification(report_msg, webhook_url=report_wh)
    
    # Очищаем отчет после отправки
    save_report({})
    logger.info("Еженедельный отчет отправлен и очищен.")

def send_teams_notification(text, is_critical=False, webhook_url=None):
    global tickets_sent
    target_url = webhook_url if webhook_url else TEAMS_WEBHOOK_URL
    if not target_url:
        logger.error("URL для вебхука Teams не настроен!")
        return
    
    # Цвет полоски слева от сообщения в Teams (Красный для критических, Синий для остальных)
    color = "E81123" if is_critical else "0078D7"
    
    # Формируем простую карточку (MessageCard)
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "text": text
    }
    
    try:
        response = requests.post(target_url, json=payload)
        response.raise_for_status()
        tickets_sent += 1
    except Exception as e:
        logger.error(f"Ошибка отправки в Teams: {e}\n{response.text if 'response' in locals() else ''}")

def authenticate_outlook():
    credentials = (CLIENT_ID, CLIENT_SECRET)
    # Используем общий эндпоинт или специфичный для тенанта
    account = Account(credentials, tenant_id=TENANT_ID)
    
    if not account.is_authenticated:
        # Это запустится только один раз для получения токена вручную
        account.authenticate(scopes=['basic', 'message_all', 'offline_access'])
        print("Авторизация успешна!")
    
    return account

def cleanup_html(html_str):
    # Удаляем html теги для поиска по чистому тексту
    text = re.sub(r'<[^>]+>', ' ', str(html_str))
    # Убираем лишние пробелы (включая неразрывные)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def parse_ticket(subject, body, country_tag=""):
    clean_body = cleanup_html(body)
    
    # 1. Сначала проверяем на рассылку SolarWinds
    if "Alert:" in clean_body and "Status:" in clean_body:
        alert_match = re.search(r'Alert:\s*(.*?)(?:\s*Location:|\s*IP:|\s*Status:|$)', clean_body)
        loc_match = re.search(r'Location:\s*(.*?)(?:\s*Alert:|\s*IP:|\s*Status:|$)', clean_body)
        ip_match = re.search(r'IP:\s*(.*?)(?:\s*Alert:|\s*Location:|\s*Status:|$)', clean_body)
        status_match = re.search(r'Status:\s*(.*?)(?:\s*Alert:|\s*Location:|\s*IP:|$)', clean_body)
        
        alert_val = alert_match.group(1).strip() if alert_match else "Неизвестно"
        loc_val = loc_match.group(1).strip() if loc_match else "Неизвестно"
        ip_val = ip_match.group(1).strip() if ip_match else "Неизвестно"
        status_val = status_match.group(1).strip() if status_match else "Неизвестно"
        
        # Выбираем эмодзи по статусу
        status_icon = "🔴" if "down" in status_val.lower() else ("🟢" if "up" in status_val.lower() else "⚠️")
        
        msg_sw = f"{status_icon} **Мониторинг SolarWinds** {country_tag}\n\n"
        msg_sw += f"**Оборудование:** {alert_val}\n"
        msg_sw += f"**Локация:** {loc_val}\n"
        msg_sw += f"**IP:** {ip_val}\n"
        msg_sw += f"**Статус:** {status_val}"
        
        # Алерты о падении оборудования считаем критичными
        is_critical = True if "down" in status_val.lower() else False
        
        return msg_sw, is_critical

    # 2. Фильтрация типов и статусов
    # Для Узбекистана отправляем ТОЛЬКО Инциденты (INC) и SLA уведомления
    if country_tag == "[UZ]":
        is_sla = "has reached" in subject.lower() and "sla" in subject.lower()
        is_inc = "INC" in subject
        if not (is_inc or is_sla):
            # Если это запрос (RITM) для Узбекистана - игнорируем
            return 'IGNORE'

    # Игнорируем Work Orders (WO) и Задачи каталога (SCTASK)
    if any(kw in subject for kw in ["WO00", "Work Order", "SCTASK"]):
        return 'IGNORE'
    
    # Игнорируем Zabbix и автоматические уведомления о тонере/оборудовании
    if "ZABBIX" in subject.upper() or "Auto_EPM" in subject:
        return 'IGNORE'
    
    # Игнорируем письма о решении/закрытии (для всех типов, включая INC и RITM)
    if any(kw in subject.lower() for kw in ["has been closed", "has been resolved", "withdrawn"]):
        return 'IGNORE'
        
    # Если это SLA уведомление, это нам нужно
    is_sla_alert = False
    if "has reached" in subject.lower() and "sla" in subject.lower():
        is_sla_alert = True
        
    # 3. Ищем номер тикета (INC или RITM)
    ticket_match = re.search(r'(INC\d+|RITM\d+)', subject)
    
    # Если это не INC, не RITM и не SLA алерт - игнорируем (никаких "обычных" писем)
    if not ticket_match and not is_sla_alert:
        return 'IGNORE'
        
    ticket_id = ticket_match.group(1) if ticket_match else "SLA Alert"
    
    # 4. Ищем подлинную ссылку на тикет в нетронутом HTML-коде письма
    # EPAM обычно делает сам номер тикета (INC...) кликабельной ссылкой
    link_match = re.search(fr'href=["\'](https?://[^"\']+)["\'][^>]*>(?:<[^>]+>)*\s*{ticket_id}', str(body), re.IGNORECASE)
    ticket_url = link_match.group(1).replace('&amp;', '&') if link_match else ""
    
    clean_body = cleanup_html(body)
    
    # 5. Достаем поля с помощью регулярных выражений
    # Используем более строгие правила остановки для обрезания лишнего текста
    stop_words = r'(?:Service\s*:|Description\s*:|Priority\s*:|Service Recipient\s*:|SLA Target Date\s*:|Location\s*:|Request Details|Comments:|Ref:|This is an automatically|$)'
    
    title_match = re.search(r'Title:\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else "Нет заголовка"
    
    desc_match = re.search(r'(?:Description:|Comments:?)\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    desc = desc_match.group(1).strip() if desc_match else ""
    
    priority_match = re.search(r'Priority:\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    priority = priority_match.group(1).strip() if priority_match else ""
    
    loc_match = re.search(r'Location:\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    location = loc_match.group(1).strip() if loc_match else ""
    
    # Дополнительно укорачиваем поля напрямую, чтобы избежать мусора
    if len(title) > 80: title = title[:80] + "..."
    if len(location) > 80: location = location[:80] + "..."
    if len(priority) > 30: priority = priority[:30] + "..."
    
    if ticket_id.startswith("INC"):
        ticket_type = "🔴 Инцидент"
    elif ticket_id.startswith("RITM"):
        ticket_type = "🟢 RITM Запрос"
    elif ticket_id.startswith("SCTASK"):
        ticket_type = "🟡 Задача каталога (SCTASK)"
    else:
        ticket_type = "📝 Тикет"
        
    if is_sla_alert:
        ticket_type = "⏰ ВНИМАНИЕ: Нарушение SLA для"
    
    tag_str = f" {country_tag}" if country_tag else ""
    
    # Формируем итоговое сообщение
    # Формат из скриншота: 🟢 RITM Запрос [UZ]: **RITM0002189104**
    if ticket_url:
        msg = f"{ticket_type}{tag_str}: [**{ticket_id}**]({ticket_url})\n\n"
    else:
        msg = f"{ticket_type}{tag_str}: **{ticket_id}**\n\n"
        
    msg += f"**Тема:** {title}\n"
    
    is_critical = False
    if is_sla_alert:
        is_critical = True
        
    if priority:
        msg += f"**Приоритет:** {priority}\n"
        if "1" in priority or "critical" in priority.lower():
            is_critical = True
            
    if location:
        msg += f"**Локация:** {location}\n"
    if desc:
        # Укорачиваем длинное описание
        short_desc = desc[:250] + "..." if len(desc) > 250 else desc
        msg += f"\n**Описание:**\n*{short_desc}*"
        
    return msg, is_critical

def test_report_logic():
    print("\n--- ТЕСТ ПАРСИНГА ОТЧЕТА (ВКЛЮЧАЯ СТАРЫЕ ПИСЬМА) ---")
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # Берем письма за последние 30 дней для теста
    limit_date = now - timedelta(days=30)
    limit_date = limit_date.replace(hour=0, minute=0, second=0, microsecond=0)
    account = authenticate_outlook()
    mailbox = account.mailbox()
    # Берем 500 писем, чтобы точно охватить всю неделю
    messages = mailbox.get_messages(limit=800, download_attachments=False)
    
    for message in messages:
        # Пропускаем письма, которые старше предела
        if getattr(message, 'received', None) and message.received < limit_date:
            continue
            
        subject = message.subject
        
        clean_body = cleanup_html(message.body)
        full_text = subject + " " + clean_body
        
        # Диагностика каждого письма, где упоминается NPR или ER и нужный город
        if ("NPR" in full_text or "ER" in full_text or "Transformation from Trainee" in full_text):
            logger.info(f"Диагностика письма (ID задачи в процессе...)")
            
            is_npr = "NPR" in full_text and "Prepare workstation" in full_text
            is_er = "ER" in full_text and ("Dismount" in full_text or "Exit" in full_text)
            
            extract_report_data(full_text, subject)
    
    print("\nГенерация отчета...")
    send_weekly_report()
    print("--- КОНЕЦ ТЕСТА ---\n")

def heartbeat_worker():
    if not UPTIME_KUMA_PUSH_URL:
        return
    logger.info(f"Запущен поток Heartbeat для Uptime Kuma: {UPTIME_KUMA_PUSH_URL}")
    while True:
        try:
            requests.get(UPTIME_KUMA_PUSH_URL, timeout=10)
        except Exception as e:
            logger.error(f"Ошибка отправки heartbeat в Uptime Kuma: {e}")
        time.sleep(50)

def main():
    global emails_checked, processed_emails, notified_tickets
    
    # Запуск мониторинга в фоне
    threading.Thread(target=heartbeat_worker, daemon=True).start()
    
    # 1. Проверка токена
    if not os.path.exists("o365_token.txt"):
        logger.error("Критическая ошибка: Файл o365_token.txt не найден! Бот не сможет авторизоваться.")
    
    # 2. Загрузка чекпоинта
    load_checkpoint()
    
    account = authenticate_outlook()
    mailbox = account.mailbox()
    
    is_first_run = True
    last_report_date = None
    last_health_check = datetime.now()
    
    logger.info("Бот запущен. Проверяю почту для Teams...")
    
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            
            # Health Check раз в 24 часа
            if (datetime.now() - last_health_check).total_seconds() > 86400:
                health_msg = f"✅ **Health Check**: Бот работает стабильно.\nПроверено писем с запуска: {emails_checked}\nТикетов в кеше: {len(notified_tickets)}"
                send_teams_notification(health_msg)
                last_health_check = datetime.now()

            # Полночь понедельника ТЕКУЩЕЙ недели
            monday_start = now_utc - timedelta(days=now_utc.weekday())
            monday_start = monday_start.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Проверяем, наступила ли пятница (4 - это пятница)
            # 13:00 UTC = 18:00 по Астане (UTC+5)
            if now_utc.weekday() == 4 and now_utc.hour >= 13:
                if last_report_date != now_utc.date():
                    send_weekly_report()
                    last_report_date = now_utc.date()

            # Получаем последние 10 писем из папки 'Inbox' (увеличили лимит, чтобы не пропускать)
            messages = mailbox.get_messages(limit=10, download_attachments=False)
            
            for message in messages:
                if message.object_id not in processed_emails:
                    # Пропускаем письма старше начала текущей недели для отчета
                    # Используем корректное сравнение aware datetimes
                    if message.received < monday_start:
                        processed_emails.add(message.object_id)
                        continue

                    # Если это первый запуск, просто добавляем ИД письма в базу и не шлем уведомление.
                    # Это предотвратит дублирование старых писем при перезагрузке бота.
                    if is_first_run:
                        processed_emails.add(message.object_id)
                        # Также пытаемся вытащить ID тикета из темы, чтобы заблокировать его
                        ticket_match = re.search(r'(INC\d+|RITM\d+)', message.subject)
                        if ticket_match:
                            notified_tickets.add(ticket_match.group(1))
                        continue

                    emails_checked += 1
                    # Фильтрация по получателю
                    to_addresses = [recipient.address.lower() for recipient in message.to]
                    cc_addresses = [recipient.address.lower() for recipient in message.cc] if hasattr(message, 'cc') else []
                    all_recipients = to_addresses + cc_addresses

                    if TARGET_EMAIL:
                        target_emails = [email.strip().lower() for email in TARGET_EMAIL.split(',') if email.strip()]
                        if not any(target in all_recipients for target in target_emails):
                            processed_emails.add(message.object_id)
                            continue

                    # Если это не первый запуск, отправляем уведомление
                    if not is_first_run: 
                        subject = message.subject
                        
                        # Дополнительная проверка: если мы уже видели это письмо, скипаем
                        if message.object_id in processed_emails:
                            continue

                        # Дополнительная проверка: если мы уже видели этот тикет в этом сеансе, скипаем всё
                        ticket_id_quick = None
                        quick_match = re.search(r'(INC\d+|RITM\d+)', subject)
                        if quick_match:
                            ticket_id_quick = quick_match.group(1)
                            if ticket_id_quick in notified_tickets:
                                processed_emails.add(message.object_id)
                                continue

                        sender = message.sender.address
                        clean_msg_body = cleanup_html(message.body)
                        full_text = subject + " " + clean_msg_body
                        
                        # Собираем данные для отчета ВСЕГДА, даже если письмо потом уйдет в IGNORE (например, это сообщение о закрытии "has been resolved")
                        extract_report_data(clean_msg_body, subject)
                        
                        # Определяем метку страны по получателю
                        country_tag = ""
                        for addr in all_recipients:
                            if 'uzbekistan' in addr: country_tag = "[UZ]"
                            elif 'kazakhstan' in addr: country_tag = "[KZ]"
                            elif 'kyrgyzstan' in addr: country_tag = "[KG]"
                            if country_tag: break

                        # Пытаемся распарсить тикет
                        parsed_result = parse_ticket(subject, message.body, country_tag=country_tag)
                        
                        if parsed_result == 'IGNORE':
                            # Это Work Order, пропускаем (но добавляем в обработанные)
                            processed_emails.add(message.object_id)
                            continue
                            
                        is_critical_ticket = False
                        
                        if parsed_result:
                            # parse_ticket теперь возвращает кортеж (text, is_critical)
                            notification, is_critical_ticket = parsed_result
                            
                            # Извлекаем ID тикета или оборудования, чтобы проверить, не отправляли ли мы его уже
                            ticket_match = re.search(r'(INC\d+|RITM\d+|EP\w+\.epam\.com)', notification)
                            t_id = ticket_match.group(1) if ticket_match else "Unknown ID"

                            if ticket_match:
                                if t_id in notified_tickets:
                                    logger.info(f"Дубликат тикета пропущен: {t_id}")
                                    processed_emails.add(message.object_id)
                                    continue
                                notified_tickets.add(t_id)

                            logger.info(f"Обработан тикет: {t_id}")
                            send_teams_notification(notification, is_critical=is_critical_ticket)
                        
                        # Блок для обычных писем удален, так как нужны только RITM, INC и SLA
                    
                    processed_emails.add(message.object_id)
            
            # После первой проверки всех 5 писем переводим флаг
            if is_first_run:
                is_first_run = False
            
            # Ограничиваем размер кеша обработанных писем (увеличили до 500 для надежности)
            if len(processed_emails) > 500:
                processed_emails = set(list(processed_emails)[-250:])
            if len(notified_tickets) > 200:
                notified_tickets = set(list(notified_tickets)[-100:])
            
            # Сохраняем прогресс каждое прохождение цикла
            save_checkpoint()
                
        except Exception as e:
            logger.exception(f"Критическая ошибка в основном цикле: {e}")
            
        # Ждем 60 секунд перед следующей проверкой
        time.sleep(60)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-report":
        test_report_logic()
    else:
        main()
