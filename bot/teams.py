import logging
import requests
from datetime import datetime, timezone
from bot.config import TEAMS_WEBHOOK_URL, get_location_responsibles
from bot.storage import state

logger = logging.getLogger(__name__)

# Пороговая длина, после которой длинное описание показывается в свёрнутом
# виде с кнопкой "Show full description", чтобы оно не превращало карточку
# в сплошную "простыню" текста. Локация теперь ВСЕГДА показывается только
# коротким названием (город/страна), без кнопки раскрытия полного пути -
# полный путь локации (например, "Asia - Central and West/Kazakhstan/Almaty")
# не показывается вообще, только его короткая читаемая метка.
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
        state.failed_sends += 1
        return False


def _build_mention_entities(mention_key):
    """
    Строит текст с упоминаниями (каждое имя на отдельной строке через "\n" -
    Adaptive Card TextBlock рендерит перевод строки как реальный line break при
    wrap: true) и список entities для Adaptive Card на основе списка
    ответственных за данную локацию.
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

    # Перенос строки между тегами - без него имена нескольких ответственных
    # слипаются друг с другом в отрисованном сообщении Teams.
    return "\n".join(at_tags), entities


def _build_all_mentions_entities():
    """
    Собирает уникальный список ВСЕХ ответственных из data/responsibles.json
    (по email, без дублей для тех, кто отвечает за несколько локаций сразу)
    и строит из него настоящие <at> mention-теги + entities.

    Раньше вместо этого использовался один псевдо-тег <at>everyone</at> с
    mentioned.id="everyone" (см. историю send_time_reminder). Но Teams
    резолвит упоминание только по реальному идентификатору пользователя
    (email/UPN, AAD Object ID или Teams user id) - строки "everyone" не
    существует ни в одном справочнике пользователей, поэтому карточка
    просто отображала слово "everyone" как обычный текст без подсветки:
    люди физически не получали пуш/уведомление о упоминании. Теперь тегаем
    каждого настоящего сотрудника по его реальному email, как и в
    _build_mention_entities() для тикетов.
    """
    responsibles_by_location = get_location_responsibles()
    seen_emails = set()
    unique_responsibles = []
    for responsibles in responsibles_by_location.values():
        for resp in responsibles:
            email = (resp.get('email') or "").lower()
            if not email or email in seen_emails:
                continue
            seen_emails.add(email)
            unique_responsibles.append(resp)

    at_tags = []
    entities = []
    for resp in unique_responsibles:
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

    return " ".join(at_tags), entities


def _fact(title, value):
    return {"title": title, "value": value}


def build_ticket_card(ticket, mention_key=None):
    """
    Строит Adaptive Card для структурированных данных тикета (см. bot/parser.py:
    parse_ticket()). Карточка содержит:
    - цветной контейнер-заголовок в зависимости от критичности/приоритета;
    - FactSet с полями (Priority, Location:, SLA %, и т.д.) вместо сплошного текста;
    - кнопку "Open in ServiceNow" (Action.OpenUrl), если есть ссылка на тикет;
    - Location показывает только короткое название (город/страна), без кнопки
      раскрытия полного пути локации;
    - сворачиваемый блок для длинного описания (Action.ToggleVisibility),
      чтобы не растягивать карточку по умолчанию.
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

    body.append({"type": "TextBlock", "text": ticket.get('title') or "No title", "wrap": True, "weight": "bolder"})

    facts = []
    if ticket.get('priority'):
        facts.append(_fact("Priority", ticket['priority']))
    if ticket.get('sla_percent') is not None:
        facts.append(_fact("SLA reached", f"{ticket['sla_percent']}%"))

    location_short = ticket.get('location_short') or ticket.get('location') or ""
    if location_short:
        facts.append(_fact("Location:", location_short))

    if facts:
        body.append({"type": "FactSet", "facts": facts})

    desc = ticket.get('description') or ""
    if desc:
        is_long_desc = len(desc) > _LONG_DESC_THRESHOLD
        short_desc = desc[:_LONG_DESC_THRESHOLD] + "..." if is_long_desc else desc
        body.append({"type": "TextBlock", "text": "Description:", "weight": "bolder", "spacing": "medium"})
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
                    "title": "📄 Show full description",
                    "targetElements": [full_desc_id]
                }]
            })

    actions = []
    if ticket.get('ticket_url'):
        actions.append({
            "type": "Action.OpenUrl",
            "title": "🔗 Open in ServiceNow",
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
        state.failed_sends += 1
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
    Отправляет напоминание про заполнение Time с реальными тегами всех
    ответственных из data/responsibles.json. Используется как для утреннего,
    так и для дневного напоминания (по пятницам).

    message_text может содержать плейсхолдер "{mentions}" - он будет заменён
    на реальные <at>Имя</at> теги. Если плейсхолдера нет, теги добавляются
    отдельной строкой перед текстом (обратная совместимость).
    """
    if not webhook_url:
        return

    mention_text, entities = _build_all_mentions_entities()

    if "{mentions}" in message_text:
        final_text = message_text.format(mentions=mention_text)
    elif mention_text:
        final_text = f"{mention_text} {message_text}"
    else:
        final_text = message_text

    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "body": [{"type": "TextBlock", "text": final_text, "wrap": True}],
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.0",
                "msteams": {"entities": entities}
            }
        }]
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=15)
        resp.raise_for_status()
        logger.info(f"{log_label} отправлено.")
    except Exception as e:
        logger.error(f"Ошибка отправки напоминания ({log_label}): {e}")
