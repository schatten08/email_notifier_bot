import os
import time
import logging
import re
import datetime
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

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Глобальные переменные для статистики
start_time = datetime.datetime.now()
emails_checked = 0
tickets_sent = 0
REPORT_FILE = "weekly_report.json"

def load_report():
    if os.path.exists(REPORT_FILE):
        with open(REPORT_FILE, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                # Проверка на то, старый ли это формат
                if "npr" in data and isinstance(data["npr"], list):
                    return {}
                return data
            except:
                return {}
    return {}

def save_report(data):
    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

import re, os, json
from datetime import datetime, timezone

import re, os, json
from datetime import datetime, timezone

def extract_report_data(full_text, subject):
    # 1. Skip SCTASK (duplicates)
    if 'SCTASK' in subject: return
    
    # 2. Final state only
    is_final = any(kw in subject.lower() for kw in ['resolved', 'exit task', 'closed'])
    # Also allow People Portal / Exit Requests which might not have 'resolved' in subject but are terminal
    if not is_final and not ('Exit Request' in full_text or 'Transformation' in full_text): return
    
    # 3. Skip Students
    if re.search(r'(Title|Employee title):.*Student', full_text, re.IGNORECASE): return
    
    # 4. Identify types
    is_npr = 'NPR' in full_text and 'Prepare workstation' in full_text
    is_er = 'ER' in full_text and ('Dismount' in full_text or 'Exit' in full_text)
    is_trans = 'Transformation from Trainee' in full_text
    
    if not (is_npr or is_er or is_trans): return
    
    # 5. Extract Name
    name = None
    if is_trans:
        m = re.search(r'Trainee:\s*(.*?)(?:,| effective)', full_text, re.IGNORECASE)
        if m: name = m.group(1).strip()
    else:
        # Priority 1: Title field
        m = re.search(r'Title:\s*([A-Z][a-z]+ [A-Z][a-z]+)', full_text)
        if not m:
            # Priority 2: Request subject pattern
            m = re.search(r'(?:NPR|ER)\s*\([^)]+\)\s*\(([^)]+)\)', full_text)
        if m: name = m.group(1).strip()
        
    if not name: return
    
    # 6. Resolve City (Strict)
    city = None
    m_loc = re.search(r'Location:\s*(.*?)(?:\n|Description|Service|Priority|Title|$)', full_text, re.IGNORECASE)
    loc_val = m_loc.group(1).lower() if m_loc else ''
    
    if 'almaty' in loc_val or '??????' in loc_val: city = '??????'
    elif 'astana' in l_val or '??????' in loc_val: city = '??????'
    elif 'bishkek' in loc_val or '??????' in loc_val: city = '??????'
    elif 'karaganda' in loc_val or '?????????' in loc_val: city = '?????????'
    elif 'tashkent' in loc_val or '???????' in loc_val: city = '???????'
    
    if not city:
        # Fallback to body scan
        t = full_text.lower()
        if 'almaty' in t: city = '??????'
        elif 'astana' in t: city = '??????'
        elif 'bishkek' in t: city = '??????'
        elif 'karaganda' in t: city = '?????????'
        elif 'tashkent' in t: city = '???????'
        
    if not city: return
    
    # 7. Add to unique report
    d = {}
    if os.path.exists('weekly_report.json'):
        try:
            with open('weekly_report.json','r',encoding='utf-8') as fj: d = json.load(fj)
        except: d = {}
        
    if name not in d:
        d[name] = {
            'city': city,
            'type': 'NPR' if (is_npr or is_trans) else 'ER',
            'date': datetime.now(timezone.utc).strftime('%Y-%m-%d')
        }
        with open('weekly_report.json','w',encoding='utf-8') as fj:
            json.dump(d, fj, ensure_ascii=False, indent=4)

def send_weekly_report():
    data = load_report()
    
    KNOWN_CITIES = ["Бишкек", "Караганда", "Астана", "Алматы", "Ташкент"]
    all_cities = list(KNOWN_CITIES)
    
    for city in data.keys():
        if city not in all_cities and city != "Другое":
            all_cities.append(city)
            
    # Если есть "Другое", добавим его в самый конец
    if "Другое" in data.keys() and "Другое" not in all_cities:
        all_cities.append("Другое")
            
    lines = ["📊 **Еженедельный отчет по сотрудникам**\n"]
    has_any_data = False
    
    for city in all_cities:
        city_data = data.get(city, {"npr": [], "er": []})
        npr_count = len(city_data["npr"])
        er_count = len(city_data["er"])
        
        if npr_count > 0 or er_count > 0:
            has_any_data = True
            
        lines.append(f"**{city}**: NPR - {npr_count}, ER - {er_count}")
        
    if not has_any_data:
        lines.append("\n*За эту неделю не было увольнений или приема на работу.*")
    
    report_msg = "\n".join(lines)
    
    # Отправляем в отдельный канал, если он задан. Иначе - в основной.
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

def parse_ticket(subject, body):
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
        
        msg_sw = f"{status_icon} **Мониторинг SolarWinds**\n\n"
        msg_sw += f"**Оборудование:** {alert_val}\n"
        msg_sw += f"**Локация:** {loc_val}\n"
        msg_sw += f"**IP:** {ip_val}\n"
        msg_sw += f"**Статус:** {status_val}"
        
        # Алерты о падении оборудования считаем критичными
        is_critical = True if "down" in status_val.lower() else False
        
        return msg_sw, is_critical

    # 2. Игнорируем ненужные типы писем (Work Orders и Закрытые тикеты)
    # Если это Work Order
    if "WO00" in subject or "Work Order" in subject:
        return 'IGNORE'
    
    # Если это письмо о закрытии/решении тикета
    if "has been closed" in subject.lower() or "has been resolved" in subject.lower():
        return 'IGNORE'
        
    # Игнорируем SCTASK, так как они дублируют RITM
    if "SCTASK" in subject or "RITM" in subject:
        return 'IGNORE'
        
    # Если это SLA уведомление, помечаем его как критическое
    is_sla_alert = False
    if "has reached" in subject.lower() and "sla" in subject.lower():
        is_sla_alert = True
        
    # 3. Ищем номер тикета (INC, RITM или SCTASK)
    ticket_match = re.search(r'(INC\d+|SCTASK\d+)', subject)
    if not ticket_match:
        return None # Это не тикет и не алерт, вернем None (чтобы отправить обычное превью)
        
    ticket_id = ticket_match.group(1)
    
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
    
    # Формируем итоговое сообщение
    # Teams поддерживает синтаксис маркдауна: [текст](ссылка)
    if ticket_url:
        msg = f"{ticket_type}: [{ticket_id}]({ticket_url})\n\n"
    else:
        msg = f"{ticket_type}: **{ticket_id}**\n\n"
        
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
    print("\n--- ТЕСТ ПАРСИНГА ОТЧЕТА ---")
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    # Определяем полночь понедельника текущей недели
    limit_date = now - timedelta(days=now.weekday())
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
        
        # Полностью игнорируем SCTASK, берем только RITM и др.
        if "SCTASK" in subject or "RITM" in subject:
            continue
            
        clean_body = cleanup_html(message.body)
        full_text = subject + " " + clean_body
        
        # Диагностика каждого письма, где упоминается NPR или ER и нужный город
        if ("NPR" in full_text or "ER" in full_text or "Transformation from Trainee" in full_text):
            print(f"\n[ДИАГНОСТИКА] Найдено письмо: {subject}")
            
            is_npr = "NPR" in full_text and "Prepare workstation" in full_text
            is_er = "ER" in full_text and ("Dismount" in full_text or "Exit" in full_text)
            
            print(f"  - is_npr: {is_npr} ('Prepare workstation' найдено: {'Prepare workstation' in full_text})")
            print(f"  - is_er: {is_er} ('Dismount' или 'Exit' найдено: {'Dismount' in full_text or 'Exit' in full_text})")
            
            match = re.search(r'(?:NPR|ER)\s*\([^)]+\)\s*\(([^)]+)\)', full_text)
            print(f"  - Регулярка на имя (NPR/ER (id) (Name)): {'СОВПАЛО: ' + match.group(1) if match else 'НЕ СОВПАЛО'}")
            
            loc_match = re.search(r'Location:\s*(.*?)(?:Description|Title|Service|Request Details|Comments|$)', full_text, re.IGNORECASE)
            print(f"  - Поиск Location: {'СОВПАЛО: ' + loc_match.group(1).strip() if loc_match else 'НЕ СОВПАЛО'}")
            
            extract_report_data(full_text, subject)
    
    print("\nГенерация отчета...")
    send_weekly_report()
    print("--- КОНЕЦ ТЕСТА ---\n")

def main():
    global emails_checked
    account = authenticate_outlook()
    mailbox = account.mailbox()
    
    # Хранилище ID обработанных писем, чтобы не слать дубликаты
    processed_emails = set()
    # Хранилище ID отправленных тикетов/алертов, чтобы не слать дубли (INC, RITM и т.д.)
    notified_tickets = set()
    is_first_run = True
    last_report_date = None
    
    # При первом запуске помечаем текущие письма как прочитанные для бота
    logger.info("Бот запущен. Проверяю почту для Teams...")
    
    while True:
        try:
            now = datetime.datetime.now()
            # Проверяем, наступила ли пятница (4 - это пятница) и время после 15:00
            if now.weekday() == 4 and now.hour >= 15:
                if last_report_date != now.date():
                    send_weekly_report()
                    last_report_date = now.date()

            # Получаем последние 5 писем из папки 'Inbox'
            messages = mailbox.get_messages(limit=5, download_attachments=False)
            
            for message in messages:
                if message.object_id not in processed_emails:
                    if "SCTASK" in message.subject or "RITM" in message.subject:
                        processed_emails.add(message.object_id)
                        continue
                    emails_checked += 1
                    # Фильтрация по получателю, если задан TARGET_EMAIL (может быть несколько через запятую)
                    if TARGET_EMAIL:
                        target_emails = [email.strip().lower() for email in TARGET_EMAIL.split(',') if email.strip()]
                        to_addresses = [recipient.address.lower() for recipient in message.to]
                        cc_addresses = [recipient.address.lower() for recipient in message.cc] if hasattr(message, 'cc') else []
                        all_recipients = to_addresses + cc_addresses
                        
                        if not any(target in all_recipients for target in target_emails):
                            processed_emails.add(message.object_id)
                            continue

                    # Если это не первый запуск, отправляем уведомление
                    if not is_first_run: 
                        subject = message.subject
                        sender = message.sender.address
                        clean_msg_body = cleanup_html(message.body)
                        full_text = subject + " " + clean_msg_body
                        
                        # Собираем данные для отчета ВСЕГДА, даже если письмо потом уйдет в IGNORE (например, это сообщение о закрытии "has been resolved")
                        extract_report_data(full_text, subject)
                        
                        # Пытаемся распарсить тикет
                        parsed_result = parse_ticket(subject, message.body)
                        
                        if parsed_result == 'IGNORE':
                            # Это Work Order, пропускаем (но добавляем в обработанные)
                            processed_emails.add(message.object_id)
                            continue
                            
                        is_critical_ticket = False
                        
                        if parsed_result:
                            # parse_ticket теперь возвращает кортеж (text, is_critical)
                            notification, is_critical_ticket = parsed_result
                            
                            # Извлекаем ID тикета или оборудования, чтобы проверить, не отправляли ли мы его уже
                            ticket_match = re.search(r'(INC\d+|SCTASK\d+|EP\w+\.epam\.com)', notification)
                            if ticket_match:
                                t_id = ticket_match.group(1)
                                if t_id in notified_tickets:
                                    logger.info(f"Дубликат тикета пропущен: {t_id}")
                                    processed_emails.add(message.object_id)
                                    continue
                                notified_tickets.add(t_id)

                            logger.info(f"Распарсен тикет из письма: {subject}")
                        else:
                            # Обычное письмо (не тикет EPAM поддерки), отправляем как раньше
                            body_preview = message.body_preview[:100] + "..."
                            notification = (
                                f"📩 **Новое письмо!**\n\n"
                                f"**От:** {sender}\n"
                                f"**Тема:** {subject}\n"
                                f"**Текст:** {body_preview}\n"
                            )
                            logger.info(f"Найдено обычное письмо: {subject}")
                            
                        send_teams_notification(notification, is_critical=is_critical_ticket)
                    
                    processed_emails.add(message.object_id)
            
            # После первой проверки всех 5 писем переводим флаг
            if is_first_run:
                is_first_run = False
            
            # Ограничиваем размер кеша обработанных писем
            if len(processed_emails) > 100:
                processed_emails = set(list(processed_emails)[-50:])
            if len(notified_tickets) > 50:
                notified_tickets = set(list(notified_tickets)[-25:])
                
        except Exception as e:
            logger.error(f"Ошибка при проверке почты: {e}")
            # В случае ошибки авторизации может потребоваться переподключение
            
        # Ждем 60 секунд перед следующей проверкой
        time.sleep(60)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-report":
        test_report_logic()
    else:
        main()
