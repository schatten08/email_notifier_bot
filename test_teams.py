import os
import requests
from dotenv import load_dotenv

# Загружаем настройки из .env
load_dotenv()

TEAMS_WEBHOOK_URL = os.getenv('TEAMS_WEBHOOK_URL')

def test_webhook():
    if not TEAMS_WEBHOOK_URL:
        print("❌ ОШИБКА: TEAMS_WEBHOOK_URL не найден в файле .env")
        return

    print("🚀 Отправляем тестовое сообщение в Teams...")
    
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0078D7",
        "summary": "Тестовое сообщение от бота",
        "sections": [{
            "activityTitle": "✅ Интеграция работает!",
            "activitySubtitle": "Если вы видите это сообщение, значит Teams Webhook настроен правильно.",
            "facts": [
                {"name": "Статус", "value": "Подключено"},
                {"name": "Бот", "value": "EPAM Notifier Bot"}
            ],
            "markdown": True
        }]
    }

    try:
        response = requests.post(TEAMS_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        print("✅ УСПЕХ! Сообщение должно было появиться в вашем чате/канале Teams.")
    except Exception as e:
        print(f"❌ ОШИБКА при отправке: {e}")
        if hasattr(response, 'text'):
            print(f"Текст ошибки: {response.text}")

if __name__ == "__main__":
    test_webhook()