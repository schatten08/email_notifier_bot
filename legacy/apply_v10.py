import os, re

with open('bot.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Fix datetime issues globally
text = text.replace('datetime.datetime.now()', 'datetime.now()')

# 2. Add country tag logic
ct_logic = \"\"\"
def get_country_tag(text):
    text_lower = text.lower()
    if 'uzbekistan' in text_lower or 'tashkent' in text_lower: return 'UZ'
    if 'kazakhstan' in text_lower or 'almaty' in text_lower or 'astana' in text_lower or 'karaganda' in text_lower: return 'KZ'
    if 'kyrgyzstan' in text_lower or 'bishkek' in text_lower: return 'KG'
    return '???'
\"\"\"
text = text.replace('def parse_ticket', ct_logic + '\\ndef parse_ticket')

# 3. Prevent duplicate notifications more strictly in main()
old_loop_start = \"\"\"            for message in messages:
                if message.object_id not in processed_emails:
                    if \"SCTASK\" in message.subject or \"RITM\" in message.subject:
                        processed_emails.add(message.object_id)
                        continue\"\"\"

new_loop_start = \"\"\"            for message in messages:
                if message.object_id in processed_emails:
                    continue
                processed_emails.add(message.object_id)
                
                # Identify region
                current_tag = get_country_tag(message.subject + ' ' + cleanup_html(message.body))
\"\"\"
text = text.replace(old_loop_start, new_loop_start)

# 4. Update the plain email notification header
old_notif = 'notification = (\\n                                f\"?? **????? ??????!**\\\\n\\\\n\"'
new_notif = 'notification = (\\n                                f\"?? **????? ?????? [{current_tag}]!**\\\\n\\\\n\"'
# Fallback to a simpler replacement if the above doesn't match exactly
text = text.replace('?? **????? ??????!**', '?? **????? ?????? [{current_tag}]!**')

with open('bot.py', 'w', encoding='utf-8') as f:
    f.write(text)
