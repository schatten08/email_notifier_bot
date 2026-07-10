import os
import requests
from dotenv import load_dotenv

load_dotenv()

webhook_url = os.getenv('TEAMS_TIME_REMINDER_WEBHOOK_URL')

def test_reminder():
    if not webhook_url:
        print("Error: TEAMS_TIME_REMINDER_WEBHOOK_URL not found in .env")
        return

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
                            "text": "🔔 **Напоминание**: Необходимо заполнить Time по ссылке https://time.epam.com/",
                            "wrap": True
                        }
                    ],
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.0"
                }
            }
        ]
    }
    
    response = requests.post(webhook_url, json=payload)
    if response.status_code in [200, 202]:
        print(f"Success: Reminder sent successfully! (Status: {response.status_code})")
    else:
        print(f"Error: Failed to send reminder. Status code: {response.status_code}, Response: {response.text}")

if __name__ == "__main__":
    test_reminder()
