import os
import requests
import logging

# Настройка логов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# URL вебхука (подтянется из окружения или вставьте вручную для теста)
TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", "ВАШ_URL_ВЕБХУКА")

def send_test_tag(name, email, city):
    if TEAMS_WEBHOOK_URL == "ВАШ_URL_ВЕБХУКА":
        print("Ошибка: Пожалуйста, установите переменную окружения TEAMS_WEBHOOK_URL или замените 'ВАШ_URL_ВЕБХУКА' в скрипте.")
        return

    at_text = f"<at>{name}</at>"
    
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
                            "text": f"🛠 **Тестовое уведомление ({city})**\n\n{at_text}, это проверка системы автоматических тегов.",
                            "wrap": True
                        }
                    ],
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.0",
                    "msteams": {
                        "entities": [
                            {
                                "type": "mention",
                                "text": at_text,
                                "mentioned": {
                                    "id": email,
                                    "name": name
                                }
                            }
                        ]
                    }
                }
            }
        ]
    }
    
    try:
        response = requests.post(TEAMS_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        print(f"Тестовое сообщение для {name} успешно отправлено!")
    except Exception as e:
        print(f"Ошибка при отправке: {e}")

if __name__ == "__main__":
    # Тестируем Андрея Трокола (Кыргызстан/Бишкек)
    send_test_tag("Andrei Trokol", "andrei_trokol@epam.com", "Bishkek")
