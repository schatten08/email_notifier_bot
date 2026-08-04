import logging
import requests
from datetime import datetime
from bot.config import TEAMS_WEBHOOK_URL, get_location_responsibles
from bot.storage import state

logger = logging.getLogger(__name__)

def send_teams_notification(text, is_critical=False, webhook_url=None):
    """Отправляет простое сообщение в Teams."""
    target_url = webhook_url if webhook_url else TEAMS_WEBHOOK_URL
    if not target_url:
        logger.error("URL для вебхука Teams не настроен!")
        return

    if datetime.now().weekday() >= 5:
        logger.info("Выходной день: отправка уведомления (без тегов) отменена.")
        return
    
    color = "E81123" if is_critical else "0078D7"
    
    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "text": text
    }
    
    try:
        response = requests.post(target_url, json=payload, timeout=15)
        response.raise_for_status()
        state.tickets_sent += 1
    except Exception as e:
        logger.error(f"Ошибка отправки в Teams: {e}")

def send_adaptive_card_with_mentions(text, mention_key, is_critical=False, webhook_url=None):
    """Отправляет Adaptive Card с тегами сотрудников на основе ключа локации/города."""
    target_url = webhook_url if webhook_url else TEAMS_WEBHOOK_URL
    if not target_url:
        return
    
    responsibles = get_location_responsibles().get(mention_key.lower(), [])
    
    if datetime.now().weekday() >= 5:
        logger.info("Выходной день: отправка уведомления отменена.")
        return

    if not responsibles:
        send_teams_notification(text, is_critical=is_critical, webhook_url=webhook_url)
        return
    
    mention_text = " "
    entities = []
    
    for resp in responsibles:
        at_text = f"<at>{resp['name']}</at>"
        mention_text += f"{at_text} "
        entities.append({
            "type": "mention",
            "text": at_text,
            "mentioned": {
                "id": resp['email'],
                "name": resp['name']
            }
        })

    full_text = f"{mention_text}\n\n{text}"
    
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
                            "text": full_text,
                            "wrap": True
                        }
                    ],
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.0",
                    "msteams": {
                        "entities": entities
                    }
                }
            }
        ]
    }
    
    try:
        response = requests.post(target_url, json=payload, timeout=15)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Ошибка отправки Adaptive Card: {e}")
