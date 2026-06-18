import os
import time
import logging
import re
import datetime
import requests
import json
from dotenv import load_dotenv
from O365 import Account
import asyncio

# Загружаем настройки
load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
TENANT_ID = os.getenv('TENANT_ID')
TEAMS_WEBHOOK_URL = os.getenv('TEAMS_WEBHOOK_URL')
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
            return json.load(f)
    return {"npr": [], "er": []}

def save_report(data):
    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def extract_report_data(subject):
    # Ищем NPR (Новые сотрудники)
    if "NPR" in subject and "Prepare workstation" in subject:
        match = re.search(r'NPR\s*\([^)]+\)\s*\(([^)]+)\)', subject)
        if match:
            data = load_report()
            name = match.group(1).strip()
            if name not in data["npr"]:
                data["npr"].append(name)
                save_report(data)
                logger.info(f"Добавлен новый сотрудник в отчет: {name}")

    # Ищем ER (Уволенные сотрудники)
    elif "ER" in subject and ("Dismount" in subject or "Exit" in subject):
        match = re.search(r'ER\s*\([^)]+\)\s*\(([^)]+)\)', subject)
        if match:
            data = load_report()
            name = match.group(1).strip()
            if name not in data["er"]:
                data["er"].append(name)
                save_report(data)
                logger.info(f"Добавлен уволенный сотрудник в отчет: {name}")

def send_weekly_report():
    data = load_report()
    npr_list = "\n".join([f"- {name}" for name in data['npr']]) or "- Нет новых сотрудников"
    er_list = "\n".join([f"- {name}" for name in data['er']]) or "- Нет уволенных сотрудников"
    
    report_msg = (
        "📊 **Еженедельный отчет по сотрудникам**\n\n"
        "**🟢 Приняты за неделю (NPR):**\n"
        f"{npr_list}\n\n"
        "**🔴 Уволены за неделю (ER):**\n"
        f"{er_list}"
    )
    send_teams_notification(report_msg)
    # Очищаем отчет после отправки
    save_report({"npr": [], "er": []})
    logger.info("Еженедельный отчет отправлен и очищен.")

def send_teams_notification(text, is_critical=False):
    global tickets_sent
    if not TEAMS_WEBHOOK_URL:
        logger.error("TEAMS_WEBHOOK_URL не настроен!")
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
        response = requests.post(TEAMS_WEBHOOK_URL, json=payload)
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
        
    # Если это SLA уведомление, помечаем его как критическое
    is_sla_alert = False
    if "has reached" in subject.lower() and "sla" in subject.lower():
        is_sla_alert = True
        
    # 3. Ищем номер тикета (INC, RITM или SCTASK)
    ticket_match = re.search(r'(INC\d+|RITM\d+|SCTASK\d+)', subject)
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
                            ticket_match = re.search(r'(INC\d+|RITM\d+|SCTASK\d+|EP\w+\.epam\.com)', notification)
                            if ticket_match:
                                t_id = ticket_match.group(1)
                                if t_id in notified_tickets:
                                    logger.info(f"Дубликат тикета пропущен: {t_id}")
                                    processed_emails.add(message.object_id)
                                    continue
                                notified_tickets.add(t_id)

                            # Проверяем, не является ли это NPR/ER запросом для еженедельного отчета
                            extract_report_data(subject)

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
    main()
