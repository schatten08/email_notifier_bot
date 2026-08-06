import logging
import requests
from datetime import datetime, timezone
from bot.config import TEAMS_WEBHOOK_URL, get_location_responsibles
from bot.storage import state

logger = logging.getLogger(__name__)

# Пороговая длина, после которой поле показывается в свёрнутом виде с кнопкой
# "Показать полностью", чтобы длинные значения (полный путь локации, длинное
# описание) не превращали карточку в сплошную "простыню" текста.
_LONG_LOCATION_THRESHOLD = 40
_LONG_DESC_THRESHOLD = 250

# Цвета контейнера в зависимости от критичности/приоритета тикета.
_CONTAINER_STYLE_BY_PRIORITY = {
    "critical": "attention",  # красный фон
    "high": "warning",        # жёлтый фон
    "normal": "good",         # зелёный фон
}


def send_teams_notification(text, is_critical=False, webhook_url=None):
    """
    Отправляет простое текстовое сообщение в Teams (MessageCard).
    Используется для health-check, отчётов и прочих сообщений без структуры тикета.
    Возвращает True при подтверждённой успешной отправке (или при намеренном
    пропуске в выходной день), False при реальной ошибке отправки.
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


def _build_mention_entities(mention_key):
    """
    Строит текст с упоминаниями (<at>Имя</at> <at>Имя2</at>, через пробел -
    именно так Teams ожидает несколько тегов подряд) и список entities для
    Adaptive Card на основе списка ответственных за данную локацию.
    Возвращает (mention_text, entities); mention_text - пустая строка, если
    ответственных для этого ключа не найдено.
    """
    responsibles = get_location_responsibles().get(mention_key.lower(), [])
    if not responsibles:
        return "", []

    at_tags = []
    entities = []
    for resp in responsibles:
        at_text = f"<at>{resp['name']}</at>"
        at_tags.append(at_text)
        entities.append({
            "type": "mention",
            "text": at_text,
            "mentioned": {
                "id": resp['email'],
                "name": resp['name']
            }
        })

    # Явный пробел между тегами - без него имена нескольких ответственных
    # слипаются друг с другом в отрисованном сообщении Teams.
    return " ".join(at_tags), entities


def _fact(title, value):
    return {"title": title, "value": value}


def build_ticket_card(ticket, mention_key=None):
    """
    Строит Adaptive Card для структурированных данных тикета (см. bot/parser.py:
    parse_ticket()). Карточка содержит:
    - цветной контейнер-заголовок в зависимости от критичности/приоритета;
    - FactSet с полями (Приоритет, Локация, SLA %, и т.д.) вместо сплошного текста;
    - кнопку "Открыть в ServiceNow" (Action.OpenUrl), если есть ссылка на тикет;
    - сворачиваемые блоки для длинной локации и длинного описания
      (Action.ToggleVisibility), чтобы не растягивать карточку по умолчанию.
    """
    body = []

    header_text = f"{ticket['header_icon']} **{ticket['header_label']}**"
    if ticket.get('tag_str'):
        header_text += f" {ticket['tag_str']}"
    header_text += f": **{ticket['display_id']}**"

    style = _CONTAINER_STYLE_BY_PRIORITY.get(ticket.get('priority_level'), "default")
    if ticket.get('is_critical'):
        style = "attention"

    body.append({
        "type": "Container",
        "style": style,
        "bleed": True,
        "items": [
            {"type": "TextBlock", "text": header_text, "wrap": True, "weight": "bolder", "size": "medium"}
        ]
    })

    if mention_key:
        mention_text, _ = _build_mention_entities(mention_key)
        if mention_text:
            body.append({"type": "TextBlock", "text": mention_text, "wrap": True})

    body.append({"type": "TextBlock", "text": ticket.get('title') or "Нет заголовка", "wrap": True, "weight": "bolder"})

    facts = []
    if ticket.get('priority'):
        facts.append(_fact("Приоритет", ticket['priority']))
    if ticket.get('sla_percent') is not None:
        facts.append(_fact("SLA исчерпан", f"{ticket['sla_percent']}%"))

    location = ticket.get('location') or ""
    location_short = ticket.get('location_short') or location
    show_full_location = bool(location) and location != location_short and len(location) > _LONG_LOCATION_THRESHOLD
    if location_short:
        facts.append(_fact("Локация", location_short))

    if facts:
        body.append({"type": "FactSet", "facts": facts})

    if show_full_location:
        full_loc_id = f"fullLocation_{ticket.get('display_id', 'x')}"
        body.append({
            "type": "TextBlock",
            "id": full_loc_id,
            "text": f"Полный путь: {location}",
            "wrap": True,
            "isSubtle": True,
            "size": "small",
            "isVisible": False
        })
        body.append({
            "type": "ActionSet",
            "actions": [{
                "type": "Action.ToggleVisibility",
                "title": "📍 Показать полный путь локации",
                "targetElements": [full_loc_id]
            }]
        })

    desc = ticket.get('description') or ""
    if desc:
        is_long_desc = len(desc) > _LONG_DESC_THRESHOLD
        short_desc = desc[:_LONG_DESC_THRESHOLD] + "..." if is_long_desc else desc
        body.append({"type": "TextBlock", "text": "Описание:", "weight": "bolder", "spacing": "medium"})
        body.append({"type": "TextBlock", "text": short_desc, "wrap": True, "isSubtle": True})

        if is_long_desc:
            full_desc_id = f"fullDesc_{ticket.get('display_id', 'x')}"
            body.append({
                "type": "TextBlock",
                "id": full_desc_id,
                "text": desc,
                "wrap": True,
                "isSubtle": True,
                "isVisible": False
            })
            body.append({
                "type": "ActionSet",
                "actions": [{
                    "type": "Action.ToggleVisibility",
                    "title": "📄 Показать описание полностью",
                    "targetElements": [full_desc_id]
                }]
            })

    actions = []
    if ticket.get('ticket_url'):
        actions.append({
            "type": "Action.OpenUrl",
            "title": "🔗 Открыть в ServiceNow",
            "url": ticket['ticket_url']
        })

    card = {
        "type": "AdaptiveCard",
        "body": body,
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.3",
    }
    if actions:
        card["actions"] = actions

    if mention_key:
        _, entities = _build_mention_entities(mention_key)
        if entities:
            card["msteams"] = {"entities": entities}

    return card


def send_ticket_card(ticket, mention_key=None, webhook_url=None):
    """
    Отправляет структурированное уведомление о тикете (dict от parse_ticket())
    как Adaptive Card с FactSet, кнопкой перехода в ServiceNow, цветовой
    индикацией приоритета и сворачиваемыми блоками для длинных полей.

    Если для указанного mention_key нет настроенных ответственных, тэги
    в сообщении просто не добавляются (карточка всё равно отправляется).

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

    card = build_ticket_card(ticket, mention_key=mention_key)

    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": card
        }]
    }

    try:
        response = requests.post(target_url, json=payload, timeout=15)
        response.raise_for_status()
        state.tickets_sent += 1
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки Adaptive Card тикета: {e}")
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
