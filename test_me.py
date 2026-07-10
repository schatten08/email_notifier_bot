from dotenv import load_dotenv
import os

load_dotenv()
TEAMS_MIDDLE_EAST_WEBHOOK_URL = os.getenv('TEAMS_MIDDLE_EAST_WEBHOOK_URL')
MIDDLE_EAST_EMAILS = [email.strip().lower() for email in os.getenv('MIDDLE_EAST_EMAILS', '').split(',') if email.strip()]

print(TEAMS_MIDDLE_EAST_WEBHOOK_URL)
print(MIDDLE_EAST_EMAILS)
