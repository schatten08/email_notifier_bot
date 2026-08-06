"""
Unit-тесты для bot/teams.py: построение Adaptive Card уведомления о тикете.

Запуск: pytest tests/test_teams.py -v
"""
import sys
import os
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.teams import build_ticket_card, _build_mention_entities


def _base_ticket(**overrides):
    ticket = {
        'ticket_id': "RITM0001234",
        'display_id': "RITM0001234",
        'ticket_url': "https://example.service-now.com/RITM0001234",
        'header_icon': "🟢",
        'header_label': "RITM Запрос",
        'tag_str': "[ALMATY]",
        'title': "Provide standard workstation",
        'priority': "3 - Average",
        'priority_level': "normal",
        'location': "Asia - Central and West/Kazakhstan/Almaty/Almaty",
        'location_short': "Almaty",
        'description': "Please check Catalog Item user options",
        'is_critical': False,
        'is_sla_alert': False,
        'sla_percent': None,
        'mention_key': "almaty",
    }
    ticket.update(overrides)
    return ticket


def _find_blocks(body, block_type):
    return [b for b in body if b.get("type") == block_type]


# --- build_ticket_card: базовая структура ---

def test_card_has_valid_adaptive_card_schema():
    card = build_ticket_card(_base_ticket())
    assert card["type"] == "AdaptiveCard"
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"
    assert "body" in card and isinstance(card["body"], list)


def test_card_is_json_serializable():
    card = build_ticket_card(_base_ticket())
    # Должно сериализоваться без ошибок - именно так карточка уходит в payload вебхука.
    json.dumps(card)


# --- FactSet вместо сплошного текста ---

def test_card_includes_factset_with_priority_and_location():
    card = build_ticket_card(_base_ticket())
    factsets = _find_blocks(card["body"], "FactSet")
    assert len(factsets) == 1
    facts = {f["title"]: f["value"] for f in factsets[0]["facts"]}
    assert facts["Приоритет"] == "3 - Average"
    assert facts["Локация"] == "Almaty"  # короткая метка, а не полный путь


def test_card_includes_sla_percent_fact_when_present():
    ticket = _base_ticket(is_sla_alert=True, sla_percent=85, header_label="ВНИМАНИЕ: SLA Alert", header_icon="⏰")
    card = build_ticket_card(ticket)
    factsets = _find_blocks(card["body"], "FactSet")
    facts = {f["title"]: f["value"] for f in factsets[0]["facts"]}
    assert facts["SLA исчерпан"] == "85%"


# --- Кнопка "Открыть в ServiceNow" ---

def test_card_has_open_url_action_when_ticket_url_present():
    card = build_ticket_card(_base_ticket())
    assert "actions" in card
    assert len(card["actions"]) == 1
    action = card["actions"][0]
    assert action["type"] == "Action.OpenUrl"
    assert action["url"] == "https://example.service-now.com/RITM0001234"


def test_card_has_no_actions_when_no_ticket_url():
    ticket = _base_ticket(ticket_url="")
    card = build_ticket_card(ticket)
    assert "actions" not in card


# --- Цветовая индикация критичности ---

def test_card_header_container_is_attention_style_when_critical():
    ticket = _base_ticket(is_critical=True, priority_level="critical")
    card = build_ticket_card(ticket)
    containers = _find_blocks(card["body"], "Container")
    assert containers[0]["style"] == "attention"


def test_card_header_container_is_good_style_for_normal_priority():
    card = build_ticket_card(_base_ticket())
    containers = _find_blocks(card["body"], "Container")
    assert containers[0]["style"] == "good"


def test_card_header_container_is_warning_style_for_high_priority():
    ticket = _base_ticket(priority_level="high", is_critical=False)
    card = build_ticket_card(ticket)
    containers = _find_blocks(card["body"], "Container")
    assert containers[0]["style"] == "warning"


# --- Сворачиваемая длинная локация ---

def test_card_adds_toggle_for_long_full_location():
    card = build_ticket_card(_base_ticket())
    action_sets = _find_blocks(card["body"], "ActionSet")
    toggle_titles = [
        a["title"] for aset in action_sets for a in aset["actions"]
        if a["type"] == "Action.ToggleVisibility"
    ]
    assert any("полный путь" in t.lower() for t in toggle_titles)


def test_card_skips_location_toggle_when_short_and_full_are_equal():
    ticket = _base_ticket(location="Almaty", location_short="Almaty")
    card = build_ticket_card(ticket)
    action_sets = _find_blocks(card["body"], "ActionSet")
    toggle_titles = [
        a["title"] for aset in action_sets for a in aset["actions"]
        if a["type"] == "Action.ToggleVisibility"
    ]
    assert not any("полный путь" in t.lower() for t in toggle_titles)


# --- Сворачиваемое длинное описание ---

def test_card_adds_toggle_for_long_description():
    long_desc = "A" * 300
    ticket = _base_ticket(description=long_desc)
    card = build_ticket_card(ticket)
    action_sets = _find_blocks(card["body"], "ActionSet")
    toggle_titles = [
        a["title"] for aset in action_sets for a in aset["actions"]
        if a["type"] == "Action.ToggleVisibility"
    ]
    assert any("описание" in t.lower() for t in toggle_titles)


def test_card_skips_description_toggle_for_short_description():
    ticket = _base_ticket(description="Short description")
    card = build_ticket_card(ticket)
    action_sets = _find_blocks(card["body"], "ActionSet")
    toggle_titles = [
        a["title"] for aset in action_sets for a in aset["actions"]
        if a["type"] == "Action.ToggleVisibility"
    ]
    assert not any("описание" in t.lower() for t in toggle_titles)


# --- Разделение упоминаний нескольких ответственных ---

def test_mention_entities_join_multiple_names_with_space_separator(monkeypatch):
    import bot.teams as teams_module

    def fake_responsibles():
        return {
            "almaty": [
                {"name": "Rustam Baratov", "email": "rustam_baratov@epam.com"},
                {"name": "Dmitriy Akimov", "email": "dmitriy_akimov@epam.com"},
            ]
        }

    monkeypatch.setattr(teams_module, "get_location_responsibles", fake_responsibles)

    mention_text, entities = _build_mention_entities("almaty")

    # Раньше теги слипались в одну строку без разделителя ("Rustam Baratov Dmitriy Akimov"
    # читалось как одно длинное имя). Теперь между тегами явный пробел-разделитель,
    # а каждое имя - отдельная <at> сущность.
    assert mention_text == "<at>Rustam Baratov</at> <at>Dmitriy Akimov</at>"
    assert len(entities) == 2
    assert entities[0]["mentioned"]["name"] == "Rustam Baratov"
    assert entities[1]["mentioned"]["name"] == "Dmitriy Akimov"


def test_mention_entities_empty_when_no_responsibles(monkeypatch):
    import bot.teams as teams_module
    monkeypatch.setattr(teams_module, "get_location_responsibles", lambda: {"kazakhstan": []})

    mention_text, entities = _build_mention_entities("kazakhstan")
    assert mention_text == ""
    assert entities == []


def test_card_includes_mentions_in_msteams_entities(monkeypatch):
    import bot.teams as teams_module

    def fake_responsibles():
        return {
            "almaty": [
                {"name": "Rustam Baratov", "email": "rustam_baratov@epam.com"},
                {"name": "Dmitriy Akimov", "email": "dmitriy_akimov@epam.com"},
            ]
        }

    monkeypatch.setattr(teams_module, "get_location_responsibles", fake_responsibles)

    card = build_ticket_card(_base_ticket(), mention_key="almaty")
    assert "msteams" in card
    assert len(card["msteams"]["entities"]) == 2

    text_blocks = _find_blocks(card["body"], "TextBlock")
    mention_block_texts = [b["text"] for b in text_blocks if "<at>" in b.get("text", "")]
    assert len(mention_block_texts) == 1
    assert mention_block_texts[0] == "<at>Rustam Baratov</at> <at>Dmitriy Akimov</at>"
