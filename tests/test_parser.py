"""
Unit-тесты для bot/parser.py.

Запуск: pytest tests/test_parser.py -v
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from bot.parser import cleanup_html, parse_ticket, parse_employee_info


# --- cleanup_html ---

def test_cleanup_html_strips_tags_and_collapses_whitespace():
    html = "<p>Hello&nbsp;<b>World</b></p>\n\n<div>Foo</div>"
    result = cleanup_html(html)
    assert "<" not in result
    assert "World" in result
    assert "Foo" in result
    assert "  " not in result  # нет лишних пробелов


def test_cleanup_html_handles_empty_input():
    assert cleanup_html("") == ""
    assert cleanup_html(None) == ""


# --- parse_ticket: базовые сценарии IGNORE ---

def test_parse_ticket_ignores_resolved_subject():
    body = "Title: Something Priority: 1 Location: Almaty Status: New"
    subject = "INC0001 has been resolved"
    assert parse_ticket(subject, body, is_middle_east=False) == 'IGNORE'


def test_parse_ticket_ignores_closed_status_in_body():
    body = "Title: Something Priority: 1 Location: Almaty Status: Closed"
    subject = "INC0002 new incident"
    assert parse_ticket(subject, body, is_middle_east=False) == 'IGNORE'


def test_parse_ticket_ignores_work_order():
    body = "Title: Something Location: Almaty"
    subject = "WO0012345 New Work Order"
    assert parse_ticket(subject, body, is_middle_east=False) == 'IGNORE'


def test_parse_ticket_ignores_zabbix():
    body = "Title: Server down Location: Almaty"
    subject = "ZABBIX: host is down"
    assert parse_ticket(subject, body, is_middle_east=False) == 'IGNORE'


def test_parse_ticket_ignores_alert_with_status():
    body = "Alert: high CPU Status: OK"
    subject = "INC0003 monitoring alert"
    assert parse_ticket(subject, body, is_middle_east=False) == 'IGNORE'


def test_parse_ticket_ignores_unknown_location_for_cis():
    body = "Title: Something Priority: 1 Location: Moscow Status: New"
    subject = "INC0004 new incident"
    assert parse_ticket(subject, body, country_tag="", is_middle_east=False) == 'IGNORE'


def test_parse_ticket_ignores_when_no_ticket_id_and_no_sla():
    body = "Title: Something Priority: 1 Location: Almaty Status: New"
    subject = "Random notification without ticket id"
    assert parse_ticket(subject, body, is_middle_east=False) == 'IGNORE'


# --- parse_ticket: успешный разбор CIS ---

def test_parse_ticket_extracts_fields_for_incident():
    body = (
        "Title: Server is down "
        "Priority: 1 - Critical "
        "Location: Almaty "
        "Description: Something is broken here "
        "Status: New"
    )
    subject = "INC0012345 New incident"
    result = parse_ticket(subject, body, country_tag="", is_middle_east=False)

    assert result != 'IGNORE'
    msg, is_critical, mention_key = result
    assert "INC0012345" in msg
    assert "Server is down" in msg
    assert "Almaty" in msg
    assert "[ALMATY]" in msg
    assert is_critical is True  # Priority 1 => критично
    assert mention_key == "almaty"


def test_parse_ticket_ritm_is_not_critical_by_default():
    body = "Title: New workstation Priority: 3 Location: Astana Description: setup needed Status: New"
    subject = "RITM0099 request"
    result = parse_ticket(subject, body, is_middle_east=False)

    assert result != 'IGNORE'
    msg, is_critical, mention_key = result
    assert "RITM0099" in msg
    assert is_critical is False
    assert mention_key == "astana"


def test_parse_ticket_uses_country_tag_when_no_location_field():
    body = "Title: Something Priority: 3 Description: no location field here Status: New"
    subject = "INC0010 issue"
    result = parse_ticket(subject, body, country_tag="[KZ]", is_middle_east=False)

    assert result != 'IGNORE'
    msg, is_critical, mention_key = result
    assert mention_key == "kazakhstan"
    assert "[KZ]" in msg


def test_parse_ticket_ignores_when_no_location_and_no_country_tag():
    body = "Title: Something Priority: 3 Description: no location field here Status: New"
    subject = "INC0011 issue"
    assert parse_ticket(subject, body, country_tag="", is_middle_east=False) == 'IGNORE'


def test_parse_ticket_sla_alert_marked_critical():
    body = "Title: SLA warning Location: Almaty Status: New"
    subject = "SLA reached 90% for INC0055"
    result = parse_ticket(subject, body, is_middle_east=False)

    assert result != 'IGNORE'
    msg, is_critical, mention_key = result
    assert "SLA Alert" in msg
    assert is_critical is True


# --- parse_ticket: Middle East ---

def test_parse_ticket_middle_east_detects_uae_tag():
    body = "Title: VPN issue Priority: 2 Location: Dubai Description: cannot connect Status: New"
    subject = "RITM0055 request"
    result = parse_ticket(subject, body, is_middle_east=True)

    assert result != 'IGNORE'
    msg, is_critical, mention_key = result
    assert "[UAE]" in msg
    assert mention_key is None  # для ME не используется CIS mention_key


def test_parse_ticket_middle_east_detects_qatar_tag():
    body = "Title: VPN issue Priority: 2 Location: Doha Description: cannot connect Status: New"
    subject = "RITM0056 request"
    _, _, _ = None, None, None
    result = parse_ticket(subject, body, is_middle_east=True)
    msg, _, _ = result
    assert "[QA]" in msg


def test_parse_ticket_middle_east_default_tag_when_unknown_location():
    body = "Title: VPN issue Priority: 2 Location: Unknown Place Description: cannot connect Status: New"
    subject = "RITM0057 request"
    result = parse_ticket(subject, body, is_middle_east=True)
    msg, _, _ = result
    assert "[ME]" in msg


# --- parse_employee_info ---

def test_parse_employee_info_returns_none_when_not_final():
    full_text = "NPR request for John Smith Location: Almaty"
    subject = "New Profile Request (NPR) for John Smith"
    # Не содержит ключевых слов "resolved/closed/exit task/completed/expired"
    assert parse_employee_info(full_text, subject) is None


def test_parse_employee_info_extracts_npr_data():
    subject = "NPR (01 Jul 2026) has been resolved"
    full_text = (
        "NPR (01 Jul 2026) has been resolved. "
        "Employee Name: John Smith "
        "Location: Almaty "
        "Title: NPR"
    )
    info = parse_employee_info(full_text, subject)
    assert info is not None
    assert info['name'] == "John Smith"
    assert info['city'] == "Almaty"
    assert info['type'] == "NPR"


def test_parse_employee_info_extracts_er_data_for_relocation_exit():
    subject = "Relocation Request: Exit Task for Jane Doe has been closed"
    full_text = (
        "Relocation Request: Exit Task for Jane Doe has been closed. "
        "Employee Name: Jane Doe "
        "Location: Tashkent, Uzbekistan "
        "last working day is set"
    )
    info = parse_employee_info(full_text, subject)
    assert info is not None
    assert info['type'] == "ER"
    assert info['city'] == "Tashkent"


def test_parse_employee_info_excludes_students_and_trainees():
    subject = "NPR (01 Jul 2026) has been resolved"
    full_text = (
        "NPR (01 Jul 2026) has been resolved. "
        "Employee Name: John Smith "
        "Location: Almaty "
        "Employee title: Student"
    )
    assert parse_employee_info(full_text, subject) is None


def test_parse_employee_info_returns_none_without_recognizable_city():
    subject = "NPR (01 Jul 2026) has been resolved"
    full_text = (
        "NPR (01 Jul 2026) has been resolved. "
        "Employee Name: John Smith "
        "Location: Unknown City"
    )
    assert parse_employee_info(full_text, subject) is None
