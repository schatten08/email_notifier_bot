import os
import json
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
TENANT_ID = os.getenv('TENANT_ID')
TEAMS_WEBHOOK_URL = os.getenv('TEAMS_WEBHOOK_URL')
TEAMS_REPORT_WEBHOOK_URL = os.getenv('TEAMS_REPORT_WEBHOOK_URL')
TEAMS_MIDDLE_EAST_WEBHOOK_URL = os.getenv('TEAMS_MIDDLE_EAST_WEBHOOK_URL')
TEAMS_TIME_REMINDER_WEBHOOK_URL = os.getenv('TEAMS_TIME_REMINDER_WEBHOOK_URL')

_me_emails_env = os.getenv('MIDDLE_EAST_EMAILS', '')
MIDDLE_EAST_EMAILS = [email.strip().lower() for email in _me_emails_env.split(',') if email.strip()]

TARGET_EMAIL = os.getenv('TARGET_EMAIL')
UPTIME_KUMA_PUSH_URL = os.getenv('UPTIME_KUMA_PUSH_URL')

ME_KEYWORDS = [
    'uae', 'dubai', 'qatar', 'doha', 'saudi', 'riyadh', 'oman', 
    'muscat', 'jordan', 'amman', 'israel', 'kuwait', 'bahrain', 'abu dhabi'
]

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
REPORT_FILE = os.path.join(DATA_DIR, "weekly_report.json")
REPORT_ME_FILE = os.path.join(DATA_DIR, "weekly_report_me.json")
CHECKPOINT_FILE = os.path.join(DATA_DIR, "bot_checkpoint.json")
TOKEN_FILE = os.path.join(DATA_DIR, "o365_token.txt")
RESPONSIBLES_FILE = os.path.join(DATA_DIR, "responsibles.json")

def get_location_responsibles():
    """Загружает список ответственных из JSON-файла. Это позволяет менять список без перезагрузки бота."""
    if os.path.exists(RESPONSIBLES_FILE):
        try:
            with open(RESPONSIBLES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Ошибка чтения responsibles.json: {e}")
    return {}
