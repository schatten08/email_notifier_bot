import sys
sys.path.insert(0, '/app')

from bot.main import authenticate_outlook
from bot.parser import cleanup_html, is_middle_east_message, parse_ticket
from bot.teams import send_ticket_card
from bot.config import TEAMS_MIDDLE_EAST_WEBHOOK_URL

TARGET_ID = "RITM0002315801"

account = authenticate_outlook()
mailbox = account.mailbox()

messages = mailbox.get_messages(limit=800, download_attachments=False)

found = None
for message in messages:
    if TARGET_ID in (message.subject or ""):
        found = message
        break

if not found:
    print(f"ПИСЬМО С {TARGET_ID} НЕ НАЙДЕНО в первых 800 письмах.")
    sys.exit(1)

message = found
subject = message.subject
print("Найдено письмо. Subject:", subject)
print("Received:", message.received)

clean_msg_body = cleanup_html(message.body)

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

is_middle_east = is_middle_east_message(message, all_recipients_info, clean_msg_body)

country_tag = ""
for addr_info in all_recipients_info:
    if 'uzbekistan' in addr_info: country_tag = "[UZ]"
    elif 'kazakhstan' in addr_info: country_tag = "[KZ]"
    elif 'kyrgyzstan' in addr_info: country_tag = "[KG]"
    if country_tag: break

parsed_result = parse_ticket(subject, message.body, country_tag=country_tag, is_middle_east=is_middle_east)

print("is_middle_east:", is_middle_east)
print("country_tag:", country_tag)
print("parsed_result:", parsed_result)

if parsed_result == 'IGNORE' or not parsed_result:
    print("Тикет был бы проигнорирован парсером. Уведомление НЕ отправлено.")
    sys.exit(1)

ticket = parsed_result
print("mention_key:", ticket['mention_key'])
print("--- TICKET DATA ---")
print(ticket)
print("--- END ---")

current_webhook = TEAMS_MIDDLE_EAST_WEBHOOK_URL if is_middle_east else None
effective_mention_key = "middle_east" if is_middle_east else ticket['mention_key']

send_ticket_card(ticket, mention_key=effective_mention_key, webhook_url=current_webhook)

print("Отправлено (или залогирована ошибка выше).")
