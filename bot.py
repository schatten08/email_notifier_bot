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
TEAMS_MIDDLE_EAST_WEBHOOK_URL = os.getenv('TEAMS_MIDDLE_EAST_WEBHOOK_URL')
TEAMS_TIME_REMINDER_WEBHOOK_URL = os.getenv('TEAMS_TIME_REMINDER_WEBHOOK_URL')
MIDDLE_EAST_EMAILS = [email.strip().lower() for email in os.getenv('MIDDLE_EAST_EMAILS', '').split(',') if email.strip()]
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
REPORT_ME_FILE = "weekly_report_me.json"
CHECKPOINT_FILE = "bot_checkpoint.json"

# Хранилище ID обработанных писем и тикетов
processed_emails = set()
notified_tickets = set()
last_report_date = None
last_time_reminder_date = None

# Ответственные за локации и города (для тегов в Teams)
LOCATION_RESPONSIBLES = {
    "uzbekistan": [
        {"name": "Bogdan Martemyanov", "email": "bogdan_martemyanov@epam.com"},
        {"name": "Rasul Gadjiyev", "email": "rasul_gadjiyev@epam.com"}
    ],
    "tashkent": [
        {"name": "Bogdan Martemyanov", "email": "bogdan_martemyanov@epam.com"},
        {"name": "Rasul Gadjiyev", "email": "rasul_gadjiyev@epam.com"}
    ],
    "kyrgyzstan": [
        {"name": "Andrei Trokol", "email": "andrei_trokol@epam.com"}
    ],
    "bishkek": [
        {"name": "Andrei Trokol", "email": "andrei_trokol@epam.com"}
    ],
    "karaganda": [
        {"name": "Kuanysh Uvaliyev", "email": "kuanysh_uvaliyev@epam.com"}
    ],
    "astana": [
        {"name": "Denis Sribnyy", "email": "denis_sribnyy@epam.com"}
    ],
    "almaty": [
        {"name": "Rustam Baratov", "email": "rustam_baratov@epam.com"},
        {"name": "Dmitriy Akimov", "email": "dmitriy_akimov@epam.com"}
    ],
    "middle_east": [
        {"name": "Pavel Vasilev2", "email": "pavel_vasilev2@epam.com"}
    ],
    "kazakhstan": []
}

def load_checkpoint():
    """Загружает ID обработанных писем и тикетов из файла."""
    global processed_emails, notified_tickets, last_report_date, last_time_reminder_date
    if os.path.exists(CHECKPOINT_FILE):
        try:
            if os.path.getsize(CHECKPOINT_FILE) == 0:
                logger.info("Чекпоинт пуст, инициализация.")
                return
            with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                processed_emails = set(data.get('processed_emails', []))
                notified_tickets = set(data.get('notified_tickets', []))
                
                # Загрузка дат последних отчетов
                lrd = data.get('last_report_date')
                if lrd:
                    last_report_date = datetime.strptime(lrd, '%Y-%m-%d').date()
                
                ltrd = data.get('last_time_reminder_date')
                if ltrd:
                    last_time_reminder_date = datetime.strptime(ltrd, '%Y-%m-%d').date()
                    
                logger.info(f"Чекпоинт загружен: {len(processed_emails)} писем, {len(notified_tickets)} тикетов.")
        except json.JSONDecodeError:
            logger.error(f"Ошибка чтения JSON в {CHECKPOINT_FILE}. Файл будет перезаписан.")
        except Exception as e:
            logger.error(f"Ошибка при загрузке чекпоинта: {e}")

def save_checkpoint():
    """Сохраняет ID обработанных писем и тикетов в файл."""
    try:
        data = {
            'processed_emails': list(processed_emails),
            'notified_tickets': list(notified_tickets),
            'last_report_date': last_report_date.strftime('%Y-%m-%d') if last_report_date else None,
            'last_time_reminder_date': last_time_reminder_date.strftime('%Y-%m-%d') if last_time_reminder_date else None
        }
        with open(CHECKPOINT_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Ошибка при сохранении чекпоинта: {e}")

def load_report(is_me=False):
    target_file = REPORT_ME_FILE if is_me else REPORT_FILE
    if os.path.exists(target_file):
        if os.path.isdir(target_file):
            logger.error(f"Критическая ошибка: {target_file} является директорией!")
            return {}
        with open(target_file, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if not is_me and "npr" in data and isinstance(data["npr"], list):
                    return {}
                return data
            except Exception as e:
                logger.error(f"Ошибка при чтении отчета {target_file}: {e}")
                return {}
    return {}

def save_report(data, is_me=False):
    target_file = REPORT_ME_FILE if is_me else REPORT_FILE
    try:
        with open(target_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"Ошибка при сохранении отчета {target_file}: {e}")

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
    
    # Расширение для Ближнего Востока
    if not city:
        for me_c in ['Dubai', 'Abu Dhabi', 'Qatar', 'Doha', 'Saudi Arabia', 'Riyadh', 'Kuwait', 'Oman', 'Muscat', 'Jordan', 'Amman']:
            if me_c.lower() in full_text.lower():
                city = me_c
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

def extract_report_data(full_text, subject, received_date=None, is_middle_east=False):
    """Вызывается для каждого письма, чтобы наполнить еженедельный отчет."""
    info = parse_employee_info(full_text, subject)
    if not info:
        return

    d = load_report(is_me=is_middle_east)
    name = info.pop('name')
    
    # Используем фактическую дату получения письма (дату закрытия задачи)
    if received_date:
        info['date'] = received_date.strftime('%Y-%m-%d')
        
    if name not in d:
        d[name] = info
        save_report(d, is_me=is_middle_east)


def send_weekly_report(is_me=False):
    data = load_report(is_me=is_me)
    if not data:
        logger.info(f"Отчет {'ME' if is_me else 'CIS'} пуст, отправка отменена.")
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
    today = datetime.now()
    week_ago = today - timedelta(days=7)
    date_range = f"({week_ago.strftime('%d %b')} - {today.strftime('%d %b %Y')})"
    prefix = "🌍 **Middle East**" if is_me else "📊 **Weekly**"
    report_msg = f"{prefix} **Employee Report** {date_range}\n"
    
    # Сортируем города (всегда включаем стандартный список)
    if is_me:
        CITY_ORDER = ["Dubai", "Abu Dhabi", "Qatar", "Saudi Arabia", "Kuwait", "Oman", "Jordan"]
    else:
        CITY_ORDER = ["Almaty", "Astana", "Bishkek", "Karaganda", "Tashkent"]
        
    # Все города из списка + любые новые города, найденные в данных
    sorted_cities = CITY_ORDER + sorted([c for c in grouped if c not in CITY_ORDER])
    
    # 2. Summary
    report_msg += "Summary:\n"
    
    for city in sorted_cities:
        city_data = grouped.get(city, {'NPR': [], 'ER': []})
        npr_count = len(city_data['NPR'])
        er_count = len(city_data['ER'])
        report_msg += f"• **{city}**: NPR: {npr_count}, ER: {er_count}  \n"
    
    report_msg += "\nDetails:\n"
    
    # 3. Details
    for city in sorted_cities:
        city_data = grouped.get(city, {'NPR': [], 'ER': []})
        all_people = city_data['NPR'] + city_data['ER']
        
        if not all_people:
            continue
            
        report_msg += f"📍 **{city}:**\n"
        
        # Сначала NPR, потом ER
        for person_line in all_people:
            report_msg += f"• {person_line}  \n"
        
        report_msg += "\n"
    
    if is_me:
        report_wh = TEAMS_MIDDLE_EAST_WEBHOOK_URL
    else:
        report_wh = TEAMS_REPORT_WEBHOOK_URL if TEAMS_REPORT_WEBHOOK_URL else TEAMS_WEBHOOK_URL
        
    send_teams_notification(report_msg, webhook_url=report_wh)
    
    # Очищаем отчет после отправки
    save_report({}, is_me=is_me)
    logger.info(f"Еженедельный отчет {'ME' if is_me else 'CIS'} отправлен и очищен.")

def send_adaptive_card_with_mentions(text, mention_key, is_critical=False, webhook_url=None):
    """Отправляет Adaptive Card с тегами сотрудников на основе ключа локации/города."""
    target_url = webhook_url if webhook_url else TEAMS_WEBHOOK_URL
    if not target_url:
        return
    
    # Ищем список людей по ключу (город или страна)
    responsibles = LOCATION_RESPONSIBLES.get(mention_key.lower(), [])
    
    # Проверка на выходные (суббота - 5, воскресенье - 6)
    is_weekend = datetime.now().weekday() >= 5

    # Если по ключу никого нет или сейчас выходные, шлем без тегов
    if not responsibles or is_weekend:
        if is_weekend and responsibles:
            logger.info("Выходной день: теги сотрудников пропущены.")
        send_teams_notification(text, is_critical=is_critical, webhook_url=webhook_url)
        return
    
    # Формируем текст упоминаний
    mention_text = " "
    entities = []
    
    for resp in responsibles:
        at_text = f"<at>{resp['name']}</at>"
        mention_text += f"{at_text} "
        entities.append({
            "type": "mention",
            "text": at_text,
            "mentioned": {
                "id": resp['email'],
                "name": resp['name']
            }
        })

    # Сообщение с тегами в начале
    full_text = f"{mention_text}\n\n{text}"
    
    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": full_text,
                            "wrap": True
                        }
                    ],
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.0",
                    "msteams": {
                        "entities": entities
                    }
                }
            }
        ]
    }
    
    try:
        response = requests.post(target_url, json=payload)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Ошибка отправки Adaptive Card: {e}")

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

def parse_ticket(subject, body, country_tag="", is_middle_east=False):
    clean_body = cleanup_html(body)
    
    # --- ГЛОБАЛЬНЫЙ ФИЛЬТР ПО ЛОКАЦИИ ---
    # Если это Ближний Восток, мы не фильтруем по локациям СНГ
    if not is_middle_east:
        # Мы обрабатываем только Казахстан, Узбекистан и Кыргызстан
        allowed_countries = ["kazakhstan", "uzbekistan", "kyrgyzstan", "казахстан", "узбекистан", "кыргызстан"]
        
        # 1. Ищем локацию в теле письма
        # Регулярка для быстрого поиска локации (избегаем захвата всего текста до конца письма)
        loc_search = re.search(r'Location:\s*(.*?)(?:\s{2,}|Title:|Alert:|IP:|Status:|\n|$)', clean_body, re.IGNORECASE)
        found_location = loc_search.group(1).lower() if loc_search else ""
        
        # 2. Проверяем: если локация найдена, должна быть из нашего списка
        mention_key = None
        if found_location:
            # Проверяем города в первую очередь для точного теганья
            for city in ["almaty", "astana", "karaganda", "tashkent", "bishkek"]:
                if city in found_location:
                    mention_key = city
                    break
            
            # Если город не нашли, проверяем страны
            if not mention_key:
                for country in ["kazakhstan", "uzbekistan", "kyrgyzstan", "казахстан", "узбекистан", "кыргызстан"]:
                    if country in found_location:
                        # Мапим названия на английские ключи
                        if "казах" in country or "kazakh" in country: mention_key = "kazakhstan"
                        elif "узбек" in country or "uzbek" in country: mention_key = "uzbekistan"
                        elif "кыргыз" in country or "kyrgyz" in country: mention_key = "kyrgyzstan"
                        else: mention_key = country
                        break
            
            if not mention_key:
                # Локация есть, но она чужая (например, Saudi Arabia или Russia)
                return 'IGNORE'
        else:
            # 3. Если локации в тексте нет, проверяем по тегу страны (из email получателей)
            if country_tag:
                mapping = {"[KZ]": "kazakhstan", "[UZ]": "uzbekistan", "[KG]": "kyrgyzstan"}
                mention_key = mapping.get(country_tag)
            
            if not mention_key:
                return 'IGNORE'
    else:
        # Для Ближнего Востока mention_key не используем
        mention_key = None
    # ------------------------------------

    # 1. Сначала проверяем на рассылку SolarWinds
    if "Alert:" in clean_body and "Status:" in clean_body:
        return 'IGNORE'

    # 2. Фильтрация типов и статусов
    # Для Узбекистана отправляем ТОЛЬКО Инциденты (INC) и SLA уведомления
    if mention_key in ["uzbekistan", "tashkent"]:
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
    
    # Игнорируем письма о решении/закрытии/обновлении (для всех типов, включая INC и RITM)
    ignore_keywords = [
        "has been closed", "has been resolved", "resolved", "closed", "withdrawn", 
        "has been suspended", "has been updated", "has a new comment",
        "comment has been added", "has been put on hold",
        "has been removed from hold", "no longer on hold", "has been resumed",
        "is back at work", "back at work",
        "снят с удержания", "возобновлен", "снято с удержания",
        "new profile request", # Игнорируем родительский запрос NPR, так как придет "Prepare workstation"
        "incident has been resolved", "request has been resolved"
    ]
    if any(kw in subject.lower() for kw in ignore_keywords):
        return 'IGNORE'
    
    # Проверка статуса в теле письма
    if re.search(r'Status:\s*(Resolved|Closed|Completed)', clean_body, re.IGNORECASE):
        return 'IGNORE'
        
    # Если это SLA уведомление, это нам нужно
    is_sla_alert = False
    lower_subject = subject.lower()
    if "sla" in lower_subject and ("reached" in lower_subject or "%" in lower_subject or "violation" in lower_subject):
        is_sla_alert = True
    elif "sla" in lower_subject and "has reached" in clean_body.lower():
        is_sla_alert = True
        
    # 3. Ищем номер тикета (INC или RITM)
    ticket_match = re.search(r'(INC\d+|RITM\d+)', subject)
    
    # Если это не INC, не RITM и не SLA алерт - игнорируем
    if not ticket_match and not is_sla_alert:
        return 'IGNORE'
        
    ticket_id = ticket_match.group(1) if ticket_match else "SLA Alert"
    
    # 4. Ищем подлинную ссылку на тикет в нетронутом HTML-коде письма
    link_match = re.search(fr'href=["\'](https?://[^"\']+)["\'][^>]*>(?:<[^>]+>)*\s*{ticket_id}', str(body), re.IGNORECASE)
    ticket_url = link_match.group(1).replace('&amp;', '&') if link_match else ""
    
    clean_body = cleanup_html(body)
    
    # 5. Достаем поля
    stop_words = r'(?:Service\s*:|Status\s*:|Description\s*:|Priority\s*:|Service Recipient\s*:|SLA Target Date\s*:|Location\s*:|Request Details|Comments:|Ref:|This is an automatically|$)'
    
    title_match = re.search(r'Title:\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else "Нет заголовка"
    
    desc_match = re.search(r'(?:Description:|Comments:?)\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    desc = desc_match.group(1).strip() if desc_match else ""
    
    priority_match = re.search(r'Priority:\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    priority = priority_match.group(1).strip() if priority_match else ""
    
    loc_match = re.search(r'Location:\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    location = loc_match.group(1).strip() if loc_match else ""
    
    # Пытаемся уточнить город из поля location для теганья
    current_mention = mention_key
    if location:
        for city in ["almaty", "astana", "karaganda", "tashkent", "bishkek"]:
            if city in location.lower():
                current_mention = city
                break

    # Дополнительно укорачиваем поля
    if len(title) > 80: title = title[:80] + "..."
    if len(location) > 80: location = location[:80] + "..."
    if len(priority) > 30: priority = priority[:30] + "..."
    
    if ticket_id.startswith("INC"):
        ticket_type = "🔴 Инцидент"
    elif ticket_id.startswith("RITM"):
        ticket_type = "🟢 RITM Запрос"
    else:
        ticket_type = "📝 Тикет"
        
    if is_sla_alert:
        ticket_type = "⏰ **ВНИМАНИЕ: SLA Alert**"
    
    if is_middle_east:
        # Пытаемся определить страну для Ближнего Востока из локации
        me_tag = "[ME]"
        low_loc = location.lower()
        if "uae" in low_loc or "dubai" in low_loc or "abu dhabi" in low_loc: me_tag = "[UAE]"
        elif "qatar" in low_loc or "doha" in low_loc: me_tag = "[QA]"
        elif "saudi" in low_loc or "riyadh" in low_loc: me_tag = "[SA]"
        elif "kuwait" in low_loc: me_tag = "[KW]"
        elif "oman" in low_loc or "muscat" in low_loc: me_tag = "[OM]"
        elif "jordan" in low_loc or "amman" in low_loc: me_tag = "[JO]"
        tag_str = f" {me_tag}"
    else:
        tag_str = f" {country_tag}" if country_tag else (f" [{mention_key.upper()}]" if mention_key else "")
    
    # Формируем итоговое сообщение
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
        short_desc = desc[:250] + "..." if len(desc) > 250 else desc
        msg += f"\n**Описание:**\n*{short_desc}*"
        
    return msg, is_critical, current_mention

def test_report_logic(region="cis"):
    print(f"\n--- ТЕСТ ПАРСИНГА ОТЧЕТА ДЛЯ {region.upper()} ---")
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # Берем письма за последние 30 дней для теста
    limit_date = now - timedelta(days=30)
    limit_date = limit_date.replace(hour=0, minute=0, second=0, microsecond=0)
    account = authenticate_outlook()
    mailbox = account.mailbox()
    
    is_me_region = (region.lower() == "me")
    
    # Берем 800 писем, чтобы точно охватить всю неделю
    messages = mailbox.get_messages(limit=800, download_attachments=False)
    
    for message in messages:
        # Пропускаем письма, которые старше предела
        if getattr(message, 'received', None) and message.received < limit_date:
            continue
            
        subject = message.subject
        clean_body = cleanup_html(message.body)
        full_text = subject + " " + clean_body
        
        # Проверяем, не относится ли письмо к Ближнему Востоку
        is_middle_east_msg = False
        me_keywords = ['uae', 'dubai', 'qatar', 'saudi', 'oman', 'jordan', 'israel', 'kuwait', 'bahrain', 'abu dhabi']
        
        # Проверяем отправителя и получателей
        try:
            sender_addr = message.sender.address.lower()
            all_recipients_info = [r.address.lower() for r in message.to]
            if hasattr(message, 'cc'): all_recipients_info += [r.address.lower() for r in message.cc]
            
            if any(me_email in sender_addr for me_email in MIDDLE_EAST_EMAILS):
                is_middle_east_msg = True
            else:
                for info in all_recipients_info:
                    if any(me_email in info for me_email in MIDDLE_EAST_EMAILS) or any(kw in info for kw in me_keywords):
                        is_middle_east_msg = True
                        break
        except:
            continue
        
        if not is_middle_east_msg:
            if any(kw in subject.lower() for kw in me_keywords) or any(kw in clean_body.lower() for kw in me_keywords):
                is_middle_east_msg = True

        # Сюда попадут только письма нужного региона
        if is_middle_east_msg == is_me_region:
            if ("NPR" in full_text or "ER" in full_text or "Transformation from Trainee" in full_text):
                extract_report_data(full_text, subject, received_date=message.received, is_middle_east=is_middle_east_msg)
    
    print("\nРезультаты сбора (JSON):")
    data = load_report(is_me=is_me_region)
    print(json.dumps(data, indent=4, ensure_ascii=False))
    
    if data:
        print("\nОтправляю тестовый отчет в Teams...")
        send_weekly_report(is_me=is_me_region)
    
    print(f"--- КОНЕЦ ТЕСТА ДЛЯ {region.upper()} ---\n")
    while True:
        try:
            requests.get(UPTIME_KUMA_PUSH_URL, timeout=10)
        except Exception as e:
            logger.error(f"Ошибка отправки heartbeat в Uptime Kuma: {e}")
        time.sleep(50)

def send_heartbeat():
    if not UPTIME_KUMA_PUSH_URL:
        return
    try:
        requests.get(UPTIME_KUMA_PUSH_URL, timeout=10)
    except Exception as e:
        logger.error(f"Ошибка отправки heartbeat в Uptime Kuma: {e}")

def main():
    global emails_checked, processed_emails, notified_tickets, last_report_date, last_time_reminder_date
    
    # 1. Проверка токена
    if not os.path.exists("o365_token.txt"):
        logger.error("Критическая ошибка: Файл o365_token.txt не найден! Бот не сможет авторизоваться.")
    
    # 2. Загрузка чекпоинта
    load_checkpoint()
    
    account = authenticate_outlook()
    mailbox = account.mailbox()
    
    # Помечаем запуск как первый ТОЛЬКО если база полностью пуста
    is_first_run = (len(processed_emails) == 0)
    
    last_health_check = datetime.now()
    
    logger.info(f"Бот запущен. Состояние: {'Первый запуск' if is_first_run else 'Продолжение работы'}. Проверяю почту...")
    
    while True:
        try:
            # Плановый перезапуск раз в 12 часов для профилактики "залипания" библиотек
            if (datetime.now() - start_time).total_seconds() > 43200:
                logger.info("Плановая перезагрузка бота для обновления соединений...")
                sys.exit(0)

            # Отправка heartbeat в Uptime Kuma (только если цикл жив)
            send_heartbeat()
            
            now_utc = datetime.now(timezone.utc)
            
            # Напоминание про Time (Пятница 11:00 Киргизия = 05:00 UTC)
            if now_utc.weekday() == 4 and now_utc.hour >= 5:
                if last_time_reminder_date != now_utc.date():
                    if TEAMS_TIME_REMINDER_WEBHOOK_URL:
                        reminder_msg = "🔔 **Напоминание**: Необходимо заполнить Time по ссылке https://time.epam.com/"
                        
                        # Используем AdaptiveCard, так как он подтвержден пользователем
                        payload = {
                            "type": "message",
                            "attachments": [
                                {
                                    "contentType": "application/vnd.microsoft.card.adaptive",
                                    "content": {
                                        "type": "AdaptiveCard",
                                        "body": [
                                            {
                                                "type": "TextBlock",
                                                "text": reminder_msg,
                                                "wrap": True
                                            }
                                        ],
                                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                                        "version": "1.0"
                                    }
                                }
                            ]
                        }
                        try:
                            resp = requests.post(TEAMS_TIME_REMINDER_WEBHOOK_URL, json=payload)
                            resp.raise_for_status()
                            logger.info("Напоминание про Time отправлено.")
                        except Exception as e:
                            logger.error(f"Ошибка отправки напоминания: {e}")
                            
                    last_time_reminder_date = now_utc.date()
                    save_checkpoint()

            # Health Check раз в 24 часа
            if (datetime.now() - last_health_check).total_seconds() > 86400:
                health_msg = f"✅ **Health Check**: Бот работает стабильно.\nПроверено писем с запуска: {emails_checked}\nТикетов в кеше: {len(notified_tickets)}"
                send_teams_notification(health_msg)
                last_health_check = datetime.now()

            # Полночь понедельника ТЕКУЩЕЙ недели
            monday_start = now_utc - timedelta(days=now_utc.weekday())
            monday_start = monday_start.replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Проверяем, наступила ли пятница (4 - это пятница)
            # 12:00 UTC = 18:00 по Бишкеку (UTC+6)
            if now_utc.weekday() == 4 and now_utc.hour >= 12:
                if last_report_date != now_utc.date():
                    send_weekly_report(is_me=False)
                    send_weekly_report(is_me=True)
                    last_report_date = now_utc.date()
                    save_checkpoint()

            # Получаем последние 100 писем из папки 'Inbox' (увеличили лимит, чтобы не пропускать)
            messages = mailbox.get_messages(limit=100, download_attachments=False)
            
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
                    all_recipients_info = []
                    for recipient in message.to:
                        all_recipients_info.append(recipient.address.lower())
                        if recipient.name:
                            all_recipients_info.append(recipient.name.lower())
                    if hasattr(message, 'cc'):
                        for recipient in message.cc:
                            all_recipients_info.append(recipient.address.lower())
                            if recipient.name:
                                all_recipients_info.append(recipient.name.lower())

                    # Если это первый запуск, просто помечаем как обработанные
                    if is_first_run:
                        processed_emails.add(message.object_id)
                        # Пытаемся быстро найти ID тикета в теме, чтобы тоже добавить в игнор
                        quick_match = re.search(r'(INC\d+|RITM\d+)', message.subject)
                        if quick_match:
                            notified_tickets.add(quick_match.group(1))
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
                        
                        # Собираем данные для отчета
                        is_middle_east = False
                        me_keywords = ['uae', 'dubai', 'qatar', 'saudi', 'oman', 'jordan', 'israel', 'kuwait', 'bahrain', 'abu dhabi']
                        
                        # 1. По списку email-адресов
                        if any(me_email in sender.lower() for me_email in MIDDLE_EAST_EMAILS):
                            is_middle_east = True
                        else:
                            # Проверяем получателей (адреса и имена)
                            for info in all_recipients_info:
                                if any(me_email in info for me_email in MIDDLE_EAST_EMAILS) or \
                                   any(kw in info for kw in me_keywords):
                                    is_middle_east = True
                                    break
                        
                        # 2. По теме и всему телу письма (не только поле Location)
                        if not is_middle_east:
                            if any(kw in subject.lower() for kw in me_keywords) or \
                               any(kw in clean_msg_body.lower() for kw in me_keywords):
                                is_middle_east = True
                        
                        # Собираем данные для отчета
                        extract_report_data(full_text, subject, received_date=message.received, is_middle_east=is_middle_east)
                        
                        # Определяем метку страны по получателю (только адреса)
                        country_tag = ""
                        for addr_info in all_recipients_info:
                            if 'uzbekistan' in addr_info: country_tag = "[UZ]"
                            elif 'kazakhstan' in addr_info: country_tag = "[KZ]"
                            elif 'kyrgyzstan' in addr_info: country_tag = "[KG]"
                            if country_tag: break

                        # Пытаемся распарсить тикет
                        parsed_result = parse_ticket(subject, message.body, country_tag=country_tag, is_middle_east=is_middle_east)
                        
                        if parsed_result == 'IGNORE':
                            # Это Work Order, пропускаем (но добавляем в обработанные)
                            processed_emails.add(message.object_id)
                            continue
                            
                        is_critical_ticket = False
                        mention_key = None
                        
                        if parsed_result:
                            # parse_ticket теперь возвращает кортеж (text, is_critical, mention_key)
                            notification, is_critical_ticket, mention_key = parsed_result

                            # Извлекаем ID тикета
                            ticket_match = re.search(r'(INC\d+|RITM\d+|EP\w+\.epam\.com)', notification)
                            t_id = ticket_match.group(1) if ticket_match else "Unknown ID"

                            if ticket_match:
                                # Разрешаем дубликаты для критических уведомлений (SLA, Priority 1)
                                if t_id in notified_tickets and not is_critical_ticket:
                                    logger.info(f"Дубликат тикета пропущен: {t_id}")
                                    processed_emails.add(message.object_id)
                                    continue
                                notified_tickets.add(t_id)

                            logger.info(f"Обработан тикет: {t_id}")
                            
                            # Определяем целевой вебхук
                            current_webhook = TEAMS_MIDDLE_EAST_WEBHOOK_URL if is_middle_east else None

                            # Если есть ключ для упоминаний (город или страна), шлем Adaptive Card с тегами
                            if is_middle_east:
                                send_adaptive_card_with_mentions(notification, "middle_east", is_critical=is_critical_ticket, webhook_url=current_webhook)
                            elif mention_key:
                                send_adaptive_card_with_mentions(notification, mention_key, is_critical=is_critical_ticket, webhook_url=current_webhook)
                            else:
                                send_teams_notification(notification, is_critical=is_critical_ticket, webhook_url=current_webhook)
                        else:
                            logger.info(f"Не удалось распарсить письмо: {subject}")
                        
                        # Блок для обычных писем удален, так как нужны только RITM, INC и SLA
                    
                    processed_emails.add(message.object_id)
            
            # После первой проверки всех 5 писем переводим флаг
            if is_first_run:
                is_first_run = False
            
            # Ограничиваем размер кеша обработанных писем
            if len(processed_emails) > 1000:
                processed_emails = set(list(processed_emails)[-500:])
            if len(notified_tickets) > 1000:
                notified_tickets = set(list(notified_tickets)[-500:])
            
            # Сохраняем прогресс каждое прохождение цикла
            save_checkpoint()
                
        except Exception as e:
            logger.exception(f"Критическая ошибка в основном цикле: {e}")
            
        # Ждем 60 секунд перед следующей проверкой
        time.sleep(60)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-report":
        region = sys.argv[2] if len(sys.argv) > 2 else "cis"
        test_report_logic(region=region)
    else:
        main()
