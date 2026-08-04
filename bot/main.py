import os
import sys
import time
import logging
import threading
import re
import requests
import json
from datetime import datetime, timedelta, timezone
from O365 import Account, FileSystemTokenBackend

from bot.config import (
    CLIENT_ID, CLIENT_SECRET, TENANT_ID, DATA_DIR, TOKEN_FILE, 
    TEAMS_TIME_REMINDER_WEBHOOK_URL, UPTIME_KUMA_PUSH_URL, TEAMS_MIDDLE_EAST_WEBHOOK_URL,
    validate_required_config
)
from bot.storage import state
from bot.parser import cleanup_html, is_middle_east_message, parse_ticket
from bot.reports import extract_report_data, send_weekly_report
from bot.teams import send_teams_notification, send_adaptive_card_with_mentions, send_time_reminder

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

start_time = datetime.now()

# Если чекпоинт "первого запуска" пуст (is_first_run=True), письма старше этого
# порога считаются реальным историческим бэклогом и молча добавляются в кэш
# уведомлённых БЕЗ отправки в Teams (чтобы не заспамить канал старыми тикетами
# при настоящем первом деплое). Письма моложе порога ВСЕГДА проходят обычную
# обработку с реальной попыткой отправки, даже если is_first_run=True.
#
# Это защита на случай, если is_first_run оказался True не из-за настоящего
# первого запуска, а из-за случайно потерянного/пустого чекпоинта (например,
# volume примонтирован в пустую директорию, файл не мигрировал при рефакторинге
# и т.п.) - тогда свежие тикеты не потеряются молча, даже если сама причина
# "ложного первого запуска" не будет устранена вовремя.
# См. инцидент 2026-08-04 (потеряно уведомление по RITM0002315801) в CHANGELOG.
FIRST_RUN_RECENT_HOURS = 3

def authenticate_outlook():
    credentials = (CLIENT_ID, CLIENT_SECRET)
    token_backend = FileSystemTokenBackend(token_path=DATA_DIR, token_filename='o365_token.txt')
    account = Account(credentials, tenant_id=TENANT_ID, token_backend=token_backend)
    
    if not account.is_authenticated:
        account.authenticate(scopes=['basic', 'message_all', 'offline_access'])
        print("Авторизация успешна!")
    
    return account

def send_heartbeat():
    if not UPTIME_KUMA_PUSH_URL:
        return
    try:
        res = requests.get(UPTIME_KUMA_PUSH_URL, timeout=10)
        logger.debug(f"Heartbeat sent to Uptime Kuma: {res.status_code}")
    except Exception as e:
        logger.error(f"Ошибка отправки heartbeat в Uptime Kuma: {e}")

def heartbeat_worker():
    logger.info("Поток heartbeat_worker запущен.")
    while True:
        send_heartbeat()
        time.sleep(30)

def test_report_logic(region="cis"):
    validate_required_config()
    print(f"\n--- ТЕСТ ПАРСИНГА ОТЧЕТА ДЛЯ {region.upper()} ---")
    now = datetime.now(timezone.utc)
    limit_date = now - timedelta(days=7)
    limit_date = limit_date.replace(hour=0, minute=0, second=0, microsecond=0)
    
    account = authenticate_outlook()
    mailbox = account.mailbox()
    is_me_region = (region.lower() == "me")
    
    messages = mailbox.get_messages(limit=800, download_attachments=False)
    
    for message in messages:
        try:
            if getattr(message, 'received', None) and message.received < limit_date:
                continue
        except (AttributeError, TypeError) as e:
            logger.debug(f"Не удалось прочитать дату получения письма, пропускаю: {e}")
            continue
            
        subject = message.subject
        clean_body = cleanup_html(message.body)
        full_text = subject + " " + clean_body
        
        all_rec_info = []
        try:
            for r in message.to:
                all_rec_info.append(r.address.lower())
                if r.name: all_rec_info.append(r.name.lower())
            if hasattr(message, 'cc'):
                for r in message.cc:
                    all_rec_info.append(r.address.lower())
                    if r.name: all_rec_info.append(r.name.lower())
        except (AttributeError, TypeError) as e:
            logger.debug(f"Не удалось прочитать получателей письма: {e}")

        is_middle_east_msg = is_middle_east_message(message, all_rec_info, clean_body)

        if is_middle_east_msg == is_me_region:
            if ("NPR" in full_text or "ER" in full_text or "Transformation from Trainee" in full_text or "Relocation Request: Exit Task" in full_text):
                extract_report_data(full_text, subject, received_date=message.received, is_middle_east=is_middle_east_msg)
    
    print("\nОтправляю отчет в Teams...")
    send_weekly_report(is_me=is_me_region)
    print(f"--- КОНЕЦ ТЕСТА ДЛЯ {region.upper()} ---\n")


def main():
    validate_required_config()

    if not os.path.exists(TOKEN_FILE):
        logger.error(f"Критическая ошибка: Файл o365_token.txt не найден в {DATA_DIR}! Бот не сможет авторизоваться.")
    
    state.load()
    account = authenticate_outlook()
    mailbox = account.mailbox()
    
    is_first_run = (len(state.processed_emails) == 0)
    last_health_check = datetime.now()
    
    t = threading.Thread(target=heartbeat_worker, name="heartbeat_worker", daemon=True)
    t.start()

    logger.info(f"Бот запущен. Состояние: {'Первый запуск' if is_first_run else 'Продолжение работы'}. Проверяю почту...")
    
    while True:
        try:
            if (datetime.now() - start_time).total_seconds() > 43200:
                logger.info("Плановая перезагрузка бота для обновления соединений...")
                sys.exit(0)
            
            now_utc = datetime.now(timezone.utc)
            
            if now_utc.weekday() == 4 and now_utc.hour >= 5:
                if state.last_time_reminder_date != now_utc.date():
                    send_time_reminder(
                        TEAMS_TIME_REMINDER_WEBHOOK_URL,
                        "<at>everyone</at> 🔔 **Напоминание**: Необходимо заполнить Time по ссылке https://time.epam.com/",
                        "Утреннее напоминание про Time"
                    )
                    state.last_time_reminder_date = now_utc.date()
                    state.save()

            if now_utc.weekday() == 4 and now_utc.hour >= 9:
                if state.last_afternoon_time_reminder_date != now_utc.date():
                    send_time_reminder(
                        TEAMS_TIME_REMINDER_WEBHOOK_URL,
                        "<at>everyone</at> ⏰ **Повторное напоминание**: Пожалуйста, не забудьте заполнить Time до конца дня: https://time.epam.com/",
                        "Дневное повторное напоминание про Time"
                    )
                    state.last_afternoon_time_reminder_date = now_utc.date()
                    state.save()

            if (datetime.now() - last_health_check).total_seconds() > 86400:
                health_msg = f"✅ **Health Check**: Бот работает стабильно.\nПроверено писем с запуска: {state.emails_checked}\nТикетов в кеше: {len(state.notified_tickets)}"
                send_teams_notification(health_msg)
                last_health_check = datetime.now()

            monday_start = now_utc - timedelta(days=now_utc.weekday())
            monday_start = monday_start.replace(hour=0, minute=0, second=0, microsecond=0)
            
            if now_utc.weekday() == 4 and now_utc.hour >= 12:
                if state.last_report_date != now_utc.date():
                    send_weekly_report(is_me=False)
                    send_weekly_report(is_me=True)
                    state.last_report_date = now_utc.date()
                    state.save()

            messages = mailbox.get_messages(limit=100, download_attachments=False)
            
            for message in messages:
                if message.object_id not in state.processed_emails:
                    if message.received < monday_start:
                        state.processed_emails.add(message.object_id)
                        continue

                    if is_first_run:
                        try:
                            is_recent_first_run_email = message.received is not None and (
                                now_utc - message.received
                            ).total_seconds() < FIRST_RUN_RECENT_HOURS * 3600
                        except (TypeError, AttributeError):
                            is_recent_first_run_email = False

                        if not is_recent_first_run_email:
                            state.processed_emails.add(message.object_id)
                            ticket_match = re.search(r'(INC\d+|RITM\d+)', message.subject)
                            if ticket_match:
                                logger.info(
                                    f"[Первый запуск] Тикет {ticket_match.group(1)} (получен {message.received}) "
                                    f"добавлен в кэш уведомлённых БЕЗ отправки в Teams (это исторический бэклог)."
                                )
                                state.notified_tickets.add(ticket_match.group(1))

                            clean_msg_body = cleanup_html(message.body)
                            full_text = message.subject + " " + clean_msg_body

                            all_rec_info = []
                            for r in message.to:
                                all_rec_info.append(r.address.lower())
                                if r.name: all_rec_info.append(r.name.lower())
                            if hasattr(message, 'cc'):
                                for r in message.cc:
                                    all_rec_info.append(r.address.lower())
                                    if r.name: all_rec_info.append(r.name.lower())

                            is_me = is_middle_east_message(message, all_rec_info, clean_msg_body)
                            extract_report_data(full_text, message.subject, received_date=message.received, is_middle_east=is_me)
                            continue
                        else:
                            logger.warning(
                                f"[Первый запуск] Письмо получено недавно (< {FIRST_RUN_RECENT_HOURS}ч назад): "
                                f"'{message.subject}' - обрабатывается как обычное новое письмо "
                                f"(с реальной отправкой в Teams), а не как исторический бэклог."
                            )

                    state.emails_checked += 1
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

                    if not is_first_run or is_recent_first_run_email:
                        subject = message.subject
                        
                        if message.object_id in state.processed_emails:
                            continue

                        ticket_id_quick = None
                        quick_match = re.search(r'(INC\d+|RITM\d+)', subject)
                        if quick_match:
                            ticket_id_quick = quick_match.group(1)
                            if ticket_id_quick in state.notified_tickets:
                                logger.info(f"[Quick dedup] Тикет {ticket_id_quick} уже уведомлён ранее, письмо пропущено: {subject}")
                                state.processed_emails.add(message.object_id)
                                continue

                        clean_msg_body = cleanup_html(message.body)
                        full_text = subject + " " + clean_msg_body
                        
                        is_middle_east = is_middle_east_message(message, all_recipients_info, clean_msg_body)
                        extract_report_data(full_text, subject, received_date=message.received, is_middle_east=is_middle_east)
                        
                        country_tag = ""
                        for addr_info in all_recipients_info:
                            if 'uzbekistan' in addr_info: country_tag = "[UZ]"
                            elif 'kazakhstan' in addr_info: country_tag = "[KZ]"
                            elif 'kyrgyzstan' in addr_info: country_tag = "[KG]"
                            if country_tag: break

                        parsed_result = parse_ticket(subject, message.body, country_tag=country_tag, is_middle_east=is_middle_east)
                        
                        if parsed_result == 'IGNORE':
                            state.processed_emails.add(message.object_id)
                            continue
                            
                        is_critical_ticket = False
                        mention_key = None
                        
                        if parsed_result:
                            notification, is_critical_ticket, mention_key = parsed_result

                            ticket_match = re.search(r'(INC\d+|RITM\d+|EP\w+\.epam\.com)', notification)
                            t_id = ticket_match.group(1) if ticket_match else "Unknown ID"

                            if ticket_match and t_id in state.notified_tickets and not is_critical_ticket:
                                logger.info(f"Дубликат тикета пропущен: {t_id}")
                                state.processed_emails.add(message.object_id)
                                continue

                            logger.info(f"Обработан тикет: {t_id}")
                            current_webhook = TEAMS_MIDDLE_EAST_WEBHOOK_URL if is_middle_east else None

                            if is_middle_east:
                                sent_ok = send_adaptive_card_with_mentions(notification, "middle_east", is_critical=is_critical_ticket, webhook_url=current_webhook)
                            elif mention_key:
                                sent_ok = send_adaptive_card_with_mentions(notification, mention_key, is_critical=is_critical_ticket, webhook_url=current_webhook)
                            else:
                                sent_ok = send_teams_notification(notification, is_critical=is_critical_ticket, webhook_url=current_webhook)

                            # Помечаем тикет как уведомлённый ТОЛЬКО после подтверждённой успешной
                            # отправки (или намеренного пропуска в выходной день). Если отправка
                            # реально провалилась (сеть, невалидный webhook и т.п.), тикет НЕ
                            # попадает в кэш - бот повторит попытку на следующей итерации (через 60с).
                            # Раньше тикет помечался ДО отправки, из-за чего неудачные отправки
                            # молча "терялись навсегда" без единого шанса на повтор.
                            if ticket_match and sent_ok:
                                state.notified_tickets.add(t_id)
                            elif ticket_match and not sent_ok:
                                logger.warning(f"Отправка уведомления по тикету {t_id} не удалась, попытка будет повторена позже.")
                                continue
                        else:
                            logger.info(f"Не удалось распарсить письмо: {subject}")
                    
                    state.processed_emails.add(message.object_id)
            
            if is_first_run:
                is_first_run = False
            
            if len(state.processed_emails) > 1000:
                state.processed_emails.trim(max_size=1000, keep_last=500)
            if len(state.notified_tickets) > 1000:
                state.notified_tickets.trim(max_size=1000, keep_last=500)
            
            state.save()
                
        except Exception as e:
            logger.exception(f"Критическая ошибка в основном цикле: {e}")
            
        time.sleep(60)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--test-report":
        region = sys.argv[2] if len(sys.argv) > 2 else "cis"
        test_report_logic(region=region)
    else:
        main()
