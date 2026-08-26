import os
import sys
import time
import logging
import threading
import re
import requests
from datetime import datetime, timedelta, timezone
from O365 import Account, FileSystemTokenBackend

from bot.config import (
    CLIENT_ID, CLIENT_SECRET, TENANT_ID, DATA_DIR, TOKEN_FILE, 
    TEAMS_TIME_REMINDER_WEBHOOK_URL, UPTIME_KUMA_PUSH_URL, TEAMS_MIDDLE_EAST_WEBHOOK_URL,
    validate_required_config
)
from bot.storage import state, load_dead_letters
from bot.parser import cleanup_html, is_middle_east_message, parse_ticket
from bot.reports import extract_report_data, send_weekly_report
from bot.teams import send_teams_notification, send_ticket_card, send_time_reminder, send_plain_message

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
                extract_report_data(full_text, subject, received_date=message.received, is_middle_east=is_middle_east_msg, report_window_start=limit_date, raw_body=message.body)
    
    print("\nОтправляю отчет в Teams...")
    send_weekly_report(is_me=is_me_region)
    print(f"--- КОНЕЦ ТЕСТА ДЛЯ {region.upper()} ---\n")


def _get_recipients_info(message):
    """Собирает адреса и имена получателей (To + Cc) в нижнем регистре в один
    список - используется и для геотегов ([UZ]/[KZ]/[KG]), и для определения
    Middle East сообщений. Вынесено в отдельную функцию, т.к. дублировалось
    трижды в основном цикле (для first-run веток, обычной обработки и т.п.)."""
    info = []
    for recipient in message.to:
        info.append(recipient.address.lower())
        if recipient.name:
            info.append(recipient.name.lower())
    if hasattr(message, 'cc'):
        for recipient in message.cc:
            info.append(recipient.address.lower())
            if recipient.name:
                info.append(recipient.name.lower())
    return info


def process_message(message, is_first_run, now_utc, monday_start):
    """
    Обрабатывает одно письмо: first-run backlog logic, сбор данных для
    еженедельного отчёта, quick dedup по номеру тикета, парсинг и отправка
    Adaptive Card в Teams. Мутирует глобальный bot.storage.state (processed_emails,
    notified_tickets, emails_checked) - идентично поведению, которое раньше было
    инлайн внутри тела `for message in messages:` в main(). Выделено в отдельную
    функцию, чтобы её можно было покрыть unit-тестами без необходимости гонять
    весь бесконечный while-цикл main() и реальную аутентификацию O365.

    Ничего не возвращает - вызывающий код (main()) просто переходит к следующему
    сообщению после вызова (аналог `continue` в старом инлайн-варианте стал `return`).
    """
    if message.object_id in state.processed_emails:
        return

    if message.received < monday_start:
        state.processed_emails.add(message.object_id)
        return

    is_recent_first_run_email = False
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
            all_rec_info = _get_recipients_info(message)

            is_me = is_middle_east_message(message, all_rec_info, clean_msg_body)
            extract_report_data(full_text, message.subject, received_date=message.received, is_middle_east=is_me, report_window_start=monday_start, raw_body=message.body)
            return
        else:
            logger.warning(
                f"[Первый запуск] Письмо получено недавно (< {FIRST_RUN_RECENT_HOURS}ч назад): "
                f"'{message.subject}' - обрабатывается как обычное новое письмо "
                f"(с реальной отправкой в Teams), а не как исторический бэклог."
            )

    state.emails_checked += 1
    all_recipients_info = _get_recipients_info(message)

    if not is_first_run or is_recent_first_run_email:
        subject = message.subject

        if message.object_id in state.processed_emails:
            return

        # ВАЖНО: сбор данных для еженедельного отчета (extract_report_data)
        # должен выполняться ДО и НЕЗАВИСИМО от "quick dedup" по номеру
        # тикета. Один и тот же RITM/INC обычно порождает несколько писем
        # (например, "assigned" -> "resolved"), и именно в финальном письме
        # "resolved"/"closed" содержатся данные о сотруднике для отчета.
        # Раньше extract_report_data вызывался ПОСЛЕ quick dedup, поэтому
        # если тикет уже был уведомлён в Teams по первому письму (например,
        # "assigned"), финальное письмо "resolved" с данными о новом
        # сотруднике полностью пропускалось через continue - отчет молча
        # терял запись (инцидент: NPR Malika Razieva не попала в отчет по
        # Кыргызстану, хотя письмо пришло вовремя).
        clean_msg_body = cleanup_html(message.body)
        full_text = subject + " " + clean_msg_body

        is_middle_east = is_middle_east_message(message, all_recipients_info, clean_msg_body)
        extract_report_data(full_text, subject, received_date=message.received, is_middle_east=is_middle_east, report_window_start=monday_start, raw_body=message.body)

        ticket_id_quick = None
        quick_match = re.search(r'(INC\d+|RITM\d+)', subject)
        if quick_match:
            ticket_id_quick = quick_match.group(1)
            if ticket_id_quick in state.notified_tickets:
                logger.info(f"[Quick dedup] Тикет {ticket_id_quick} уже уведомлён ранее, письмо пропущено: {subject}")
                state.processed_emails.add(message.object_id)
                return

        country_tag = ""
        for addr_info in all_recipients_info:
            if 'uzbekistan' in addr_info: country_tag = "[UZ]"
            elif 'kazakhstan' in addr_info: country_tag = "[KZ]"
            elif 'kyrgyzstan' in addr_info: country_tag = "[KG]"
            if country_tag: break

        ticket = parse_ticket(subject, message.body, country_tag=country_tag, is_middle_east=is_middle_east)

        if ticket == 'IGNORE':
            state.processed_emails.add(message.object_id)
            return

        if ticket:
            t_id = ticket['ticket_id']  # None для "голых" SLA-алертов без INC/RITM
            mention_key = ticket['mention_key']

            if t_id and t_id in state.notified_tickets and not ticket['is_critical']:
                logger.info(f"Дубликат тикета пропущен: {t_id}")
                state.processed_emails.add(message.object_id)
                return

            logger.info(f"Обработан тикет: {t_id or ticket['display_id']}")
            current_webhook = TEAMS_MIDDLE_EAST_WEBHOOK_URL if is_middle_east else None
            effective_mention_key = "middle_east" if is_middle_east else mention_key

            sent_ok = send_ticket_card(ticket, mention_key=effective_mention_key, webhook_url=current_webhook)

            # Помечаем тикет как уведомлённый ТОЛЬКО после подтверждённой успешной
            # отправки (или намеренного пропуска в выходной день). Если отправка
            # реально провалилась (сеть, невалидный webhook и т.п.), тикет НЕ
            # попадает в кэш - бот повторит попытку на следующей итерации (через 60с).
            # Раньше тикет помечался ДО отправки, из-за чего неудачные отправки
            # молча "терялись навсегда" без единого шанса на повтор.
            if t_id and sent_ok:
                state.notified_tickets.add(t_id)
            elif t_id and not sent_ok:
                logger.warning(f"Отправка уведомления по тикету {t_id} не удалась, попытка будет повторена позже.")
                return
        else:
            logger.info(f"Не удалось распарсить письмо: {subject}")

    state.processed_emails.add(message.object_id)


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
                        "{mentions} 🔔 **Напоминание**: Необходимо заполнить Time по ссылке https://time.epam.com/",
                        "Утреннее напоминание про Time"
                    )
                    state.last_time_reminder_date = now_utc.date()
                    state.save()

            if now_utc.weekday() == 4 and now_utc.hour >= 9:
                if state.last_afternoon_time_reminder_date != now_utc.date():
                    send_time_reminder(
                        TEAMS_TIME_REMINDER_WEBHOOK_URL,
                        "{mentions} ⏰ **Повторное напоминание**: Пожалуйста, не забудьте заполнить Time до конца дня: https://time.epam.com/",
                        "Дневное повторное напоминание про Time"
                    )
                    state.last_afternoon_time_reminder_date = now_utc.date()
                    state.save()

            # Вечернее пожелание хорошего вечера и благодарность за работу.
            # Отправляется в тот же чат, что и напоминания про Time, каждый
            # будний день (Пн-Пт) в 19:00 по Бишкеку (UTC+6 -> 13:00 UTC).
            # По пятницам (weekday() == 4) текст другой - вместо "хорошего
            # вечера" желаем хороших ВЫХОДНЫХ, так как впереди два дня отдыха.
            if now_utc.weekday() <= 4 and now_utc.hour >= 13:
                if state.last_evening_thanks_date != now_utc.date():
                    if now_utc.weekday() == 4:
                        evening_message = "🎉 Спасибо за отличную работу на этой неделе! Хороших выходных и отличного отдыха! 🙌 Пора домой:)"
                    else:
                        evening_message = "🌇 Спасибо за отличную работу сегодня! Хорошего вечера и приятного отдыха! 🙌 Пора домой:)"
                    send_plain_message(
                        TEAMS_TIME_REMINDER_WEBHOOK_URL,
                        evening_message,
                        "Вечернее пожелание"
                    )
                    state.last_evening_thanks_date = now_utc.date()
                    state.save()

            if (datetime.now() - last_health_check).total_seconds() > 86400:
                uptime = datetime.now() - start_time
                uptime_hours = round(uptime.total_seconds() / 3600, 1)
                total_sends = state.tickets_sent + state.failed_sends
                success_rate = (
                    round(state.tickets_sent / total_sends * 100, 1) if total_sends else 100.0
                )
                last_poll_str = (
                    state.last_poll_at.strftime('%Y-%m-%d %H:%M:%S UTC')
                    if state.last_poll_at else "N/A"
                )
                # Счётчик dead-letter записей (см. bot/reports.py::extract_report_data
                # и bot/storage.py::append_dead_letter) - письма, ПОХОЖИЕ на финальное
                # NPR/ER-событие конкретного сотрудника, но которые не удалось
                # распарсить (подозрение на дрифт формата письма ServiceNow). Выводим
                # в health-check, чтобы дрифт обнаруживал сам бот, а не пользователь
                # постфактум по факту пропавшей записи в еженедельном отчёте.
                dead_letter_count = len(load_dead_letters())
                dead_letter_line = (
                    f"⚠️ Нераспарсенных NPR/ER писем (dead-letter): {dead_letter_count}\n"
                    if dead_letter_count else ""
                )
                health_msg = (
                    "✅ **Health Check**: Бот работает стабильно.\n"
                    f"Аптайм: {uptime_hours}ч\n"
                    f"Циклов опроса почты: {state.poll_count}\n"
                    f"Последний опрос: {last_poll_str}\n"
                    f"Проверено писем с запуска: {state.emails_checked}\n"
                    f"Отправлено в Teams: {state.tickets_sent}\n"
                    f"Ошибок отправки: {state.failed_sends}\n"
                    f"Успешность отправки: {success_rate}%\n"
                    f"Тикетов в кеше: {len(state.notified_tickets)}\n"
                    f"Писем в кеше: {len(state.processed_emails)}\n"
                    f"{dead_letter_line}"
                ).rstrip()
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
            state.poll_count += 1
            state.last_poll_at = datetime.now(timezone.utc)

            for message in messages:
                process_message(message, is_first_run, now_utc, monday_start)
            
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
