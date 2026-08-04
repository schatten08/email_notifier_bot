import logging
import requests
from datetime import datetime, timezone
from bot.config import TEAMS_WEBHOOK_URL, get_location_responsibles
from bot.storage import state

logger = logging.getLogger(__name__)

def send_teams_notification(text, is_critical=False, webhook_url=None):
    """
    Отправляет простое сообщение в Teams.
    Возвращает True при подтверждённой успешной отправке (или при намеренном
    пропуске в выходной день), False при реальной ошибке отправки.
    Вызывающий код должен использовать это значение, чтобы решить,
    можно ли помечать тикет/письмо как обработанное.
    """
    target_url = webhook_url if webhook_url else TEAMS_WEBHOOK_URL
    if not target_url:
        logger.error("URL для вебхука Teams не настроен!")
        return False

    if datetime.now(timezone.utc).weekday() >= 5:
        logger.info("Выходной день (UTC): отправка уведомления (без тегов) отменена.")
        return True

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
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки в Teams: {e}")
        return False

def send_adaptive_card_with_mentions(text, mention_key, is_critical=False, webhook_url=None):
    """
    Отправляет Adaptive Card с тегами сотрудников на основе ключа локации/города.
    Возвращает True при подтверждённой успешной отправке (или при намеренном
    пропуске в выходной день), False при реальной ошибке отправки.
    """
    target_url = webhook_url if webhook_url else TEAMS_WEBHOOK_URL
    if not target_url:
        logger.error("URL для вебхука Teams не настроен!")
        return False

    if datetime.now(timezone.utc).weekday() >= 5:
        logger.info("Выходной день (UTC): отправка уведомления отменена.")
        return True

    responsibles = get_location_responsibles().get(mention_key.lower(), [])

    if not responsibles:
        return send_teams_notification(text, is_critical=is_critical, webhook_url=webhook_url)
    
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
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки Adaptive Card: {e}")
        return False

def send_plain_message(webhook_url, message_text, log_label):
    """
    Отправляет простое текстовое сообщение (MessageCard, без тегов/упоминаний)
    на заданный webhook. Используется, например, для вечернего пожелания
    хорошего вечера в тот же чат, куда приходят напоминания про Time.
    """
    if not webhook_url:
        return

    payload = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": "0078D7",
        "text": message_text
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info(f"{log_label} отправлено.")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения ({log_label}): {e}")

def send_time_reminder(webhook_url, message_text, log_label):
    """
    Отправляет напоминание про заполнение Time всем через <at>everyone</at> Adaptive Card.
    Используется как для утреннего, так и для дневного напоминания (по пятницам).
    """
    if not webhook_url:
        return

    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "body": [{"type": "TextBlock", "text": message_text, "wrap": True}],
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.0",
                "msteams": {"entities": [{"type": "mention", "text": "<at>everyone</at>", "mentioned": {"id": "everyone", "name": "everyone"}}]}
            }
        }]
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info(f"{log_label} отправлено.")
    except Exception as e:
        logger.error(f"Ошибка отправки напоминания ({log_label}): {e}")
