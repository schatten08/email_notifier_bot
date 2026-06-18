import os
import time
import logging
import re
import datetime
from dotenv import load_dotenv
from O365 import Account
from telegram import Bot
import asyncio

# Загружаем настройки
load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
TENANT_ID = os.getenv('TENANT_ID')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TARGET_EMAIL = os.getenv('TARGET_EMAIL')

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Глобальные переменные для статистики
start_time = datetime.datetime.now()
emails_checked = 0
tickets_sent = 0

async def send_telegram_notification(text, is_critical=False):
    global tickets_sent
    if not TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_CHAT_ID не настроен!")
        return
        
    bot = Bot(token=TELEGRAM_TOKEN)
    
    # Проверяем текущее время (тихий режим с 22:00 до 08:00)
    current_hour = datetime.datetime.now().hour
    is_quiet_hours = current_hour >= 22 or current_hour < 8
    
    # Отключаем звук, если сейчас тихие часы И тикет не критический
    disable_notification = is_quiet_hours and not is_critical
    
    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID, 
            text=text, 
            parse_mode='HTML',
            disable_notification=disable_notification
        )
        tickets_sent += 1
    except Exception as e:
        logger.error(f"Ошибка отправки в TG: {e}")

async def handle_status_command(bot, offset):
    updates = await bot.get_updates(offset=offset, timeout=1)
    new_offset = offset
    
    for update in updates:
        new_offset = update.update_id + 1
        if update.message and update.message.text == "/status":
            uptime = datetime.datetime.now() - start_time
            days = uptime.days
            hours, remainder = divmod(uptime.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            
            uptime_str = f"{days} дн. {hours} ч. {minutes} мин." if days > 0 else f"{hours} ч. {minutes} мин."
            
            status_msg = (
                "🟢 <b>Бот работает</b>\n\n"
                f"⏱ <b>Аптайм:</b> {uptime_str}\n"
                f"📧 <b>Проверено писем:</b> {emails_checked}\n"
                f"🚀 <b>Отправлено тикетов:</b> {tickets_sent}"
            )
            await bot.send_message(chat_id=update.message.chat_id, text=status_msg, parse_mode='HTML')
            
    return new_offset

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
        
        msg_sw = f"{status_icon} <b>Мониторинг SolarWinds</b>\n\n"
        msg_sw += f"<b>Оборудование:</b> {alert_val}\n"
        msg_sw += f"<b>Локация:</b> {loc_val}\n"
        msg_sw += f"<b>IP:</b> {ip_val}\n"
        msg_sw += f"<b>Статус:</b> {status_val}"
        
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
    
    # Формируем итоговое сообщение
    if ticket_url:
        msg = f"{ticket_type}: <a href='{ticket_url}'><b>{ticket_id}</b></a>\n\n"
    else:
        msg = f"{ticket_type}: <b>{ticket_id}</b>\n\n"
        
    msg += f"<b>Тема:</b> {title}\n"
    
    is_critical = False
    if priority:
        msg += f"<b>Приоритет:</b> {priority}\n"
        if "1" in priority or "critical" in priority.lower():
            is_critical = True
            
    if location:
        msg += f"<b>Локация:</b> {location}\n"
    if desc:
        # Укорачиваем длинное описание
        short_desc = desc[:250] + "..." if len(desc) > 250 else desc
        msg += f"\n<b>Описание:</b>\n<i>{short_desc}</i>"
        
    return msg, is_critical

async def main():
    global emails_checked
    account = authenticate_outlook()
    mailbox = account.mailbox()
    bot = Bot(token=TELEGRAM_TOKEN)
    
    # Хранилище ID обработанных писем, чтобы не слать дубликаты
    processed_emails = set()
    # Хранилище ID отправленных тикетов/алертов, чтобы не слать дубли (INC, RITM и т.д.)
    notified_tickets = set()
    is_first_run = True
    tg_update_offset = None
    
    # При первом запуске помечаем текущие письма как прочитанные для бота
    logger.info("Бот запущен. Проверяю почту...")
    
    while True:
        try:
            # Обработка команд из Телеграма (например, /status)
            tg_update_offset = await handle_status_command(bot, tg_update_offset)
            
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

                            logger.info(f"Распарсен тикет из письма: {subject}")
                        else:
                            # Обычное письмо (не тикет EPAM поддерки), отправляем как раньше
                            body_preview = message.body_preview[:100] + "..."
                            notification = (
                                f"📩 <b>Новое письмо!</b>\n\n"
                                f"<b>От:</b> {sender}\n"
                                f"<b>Тема:</b> {subject}\n"
                                f"<b>Текст:</b> {body_preview}\n"
                            )
                            logger.info(f"Найдено обычное письмо: {subject}")
                            
                        await send_telegram_notification(notification, is_critical=is_critical_ticket)
                    
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
        await asyncio.sleep(60)

if __name__ == "__main__":
    if not TELEGRAM_CHAT_ID:
        print("\n!!! ВНИМАНИЕ !!!")
        print("Вы не указали TELEGRAM_CHAT_ID в файле .env")
        print("Напишите боту что-нибудь в Telegram, и я попробую найти ваш ID.")
        
        async def get_chat_id():
            bot = Bot(token=TELEGRAM_TOKEN)
            updates = await bot.get_updates()
            if updates:
                chat_id = updates[-1].message.chat_id
                print(f"\nВаш Chat ID: {chat_id}")
                print("Скопируйте его в файл .env в поле TELEGRAM_CHAT_ID\n")
            else:
                print("Обновлений нет. Сначала напишите сообщение боту!")
        
        asyncio.run(get_chat_id())
    else:
        asyncio.run(main())
