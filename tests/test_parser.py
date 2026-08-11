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
    assert result['ticket_id'] == "INC0012345"
    assert result['title'] == "Server is down"
    assert result['location'] == "Almaty"
    assert result['tag_str'] == "[ALMATY]"
    assert result['is_critical'] is True  # Priority 1 => критично
    assert result['priority_level'] == "critical"
    assert result['mention_key'] == "almaty"


def test_parse_ticket_ritm_is_not_critical_by_default():
    body = "Title: New workstation Priority: 3 Location: Astana Description: setup needed Status: New"
    subject = "RITM0099 request"
    result = parse_ticket(subject, body, is_middle_east=False)

    assert result != 'IGNORE'
    assert result['ticket_id'] == "RITM0099"
    assert result['is_critical'] is False
    assert result['mention_key'] == "astana"


def test_parse_ticket_uses_country_tag_when_no_location_field():
    body = "Title: Something Priority: 3 Description: no location field here Status: New"
    subject = "INC0010 issue"
    result = parse_ticket(subject, body, country_tag="[KZ]", is_middle_east=False)

    assert result != 'IGNORE'
    assert result['mention_key'] == "kazakhstan"
    assert result['tag_str'] == "[KZ]"


def test_parse_ticket_ignores_when_no_location_and_no_country_tag():
    body = "Title: Something Priority: 3 Description: no location field here Status: New"
    subject = "INC0011 issue"
    assert parse_ticket(subject, body, country_tag="", is_middle_east=False) == 'IGNORE'


def test_parse_ticket_sla_alert_marked_critical():
    body = "Title: SLA warning Location: Almaty Status: New"
    subject = "SLA reached 90% for INC0055"
    result = parse_ticket(subject, body, is_middle_east=False)

    assert result != 'IGNORE'
    assert result['is_sla_alert'] is True
    assert result['header_label'] == "WARNING: SLA Alert"
    assert result['ticket_id'] == "INC0055"  # ID найден в теме письма
    assert result['is_critical'] is True
    assert result['sla_percent'] == 90


def test_parse_ticket_sla_alert_without_ticket_id_uses_display_fallback():
    body = "Title: SLA warning Location: Almaty Status: New Alert reached 75%"
    subject = "SLA violation reached"  # без INC/RITM в теме
    result = parse_ticket(subject, body, is_middle_east=False)

    assert result != 'IGNORE'
    assert result['ticket_id'] is None
    assert result['display_id'] == "SLA Alert"
    assert result['sla_percent'] == 75


def test_parse_ticket_sla_percent_none_when_not_present():
    body = "Title: SLA warning Location: Almaty Status: New violation detected"
    subject = "SLA violation reached for INC0056"
    result = parse_ticket(subject, body, is_middle_east=False)

    assert result != 'IGNORE'
    assert result['is_sla_alert'] is True
    assert result['sla_percent'] is None


# --- parse_ticket: Middle East ---

def test_parse_ticket_middle_east_detects_uae_tag():
    body = "Title: VPN issue Priority: 2 Location: Dubai Description: cannot connect Status: New"
    subject = "RITM0055 request"
    result = parse_ticket(subject, body, is_middle_east=True)

    assert result != 'IGNORE'
    assert result['tag_str'] == "[UAE]"
    assert result['mention_key'] is None  # для ME не используется CIS mention_key


def test_parse_ticket_middle_east_detects_qatar_tag():
    body = "Title: VPN issue Priority: 2 Location: Doha Description: cannot connect Status: New"
    subject = "RITM0056 request"
    result = parse_ticket(subject, body, is_middle_east=True)
    assert result['tag_str'] == "[QA]"


def test_parse_ticket_middle_east_default_tag_when_unknown_location():
    body = "Title: VPN issue Priority: 2 Location: Unknown Place Description: cannot connect Status: New"
    subject = "RITM0057 request"
    result = parse_ticket(subject, body, is_middle_east=True)
    assert result['tag_str'] == "[ME]"


# --- parse_ticket: короткая метка локации (для отображения в карточке) ---

def test_parse_ticket_location_short_uses_city_label_for_cis():
    body = (
        "Title: New workstation Priority: 3 "
        "Location: Asia - Central and West/Kazakhstan/Almaty/Almaty "
        "Description: setup needed Status: New"
    )
    subject = "RITM0100 request"
    result = parse_ticket(subject, body, is_middle_east=False)

    assert result != 'IGNORE'
    assert result['location_short'] == "Almaty"
    assert "Asia - Central" in result['location']


# --- parse_employee_info ---

def test_parse_employee_info_returns_none_when_not_final():
    full_text = "NPR request for John Smith Location: Almaty"
    subject = "New Profile Request (NPR) for John Smith"
    # Не содержит ключевых слов "resolved/closed/exit task/completed/expired"
    assert parse_employee_info(full_text, subject) is None


def test_parse_employee_info_ignores_pending_approval_exit_task():
    """subject содержит подстроку 'exit task', но это pending-запрос
    ('requires approval'), а не финальное/согласованное увольнение -
    не должен попадать в отчёт."""
    subject = "[People] [AR] (Kazakhstan: Astana): Exit Request: Exit Task for Artur Muratov requires approval"
    full_text = (
        subject + " Dear colleagues, Exit Request for Artur Muratov has been "
        "submitted. The following exit tasks are pending your approval: "
        "Employee has returned EPAM-owned hardware Please review the Exit "
        "Request and take necessary actions promptly. Request details: "
        "Country Kazakhstan City Astana"
    )
    assert parse_employee_info(full_text, subject) is None


def test_parse_employee_info_ignores_expired_exit_task():
    """subject содержит 'exit task' и 'has expired' - это просроченное
    напоминание о невыполненной exit-задаче, а не успешное завершение
    увольнения. Не должен попадать в отчёт."""
    subject = "[People] (Kazakhstan: Almaty): Exit Request: Exit Task for Nurlykhan Salamatuly has expired"
    full_text = (
        subject + " Dear colleagues, The following Exit Tasks in Exit Request "
        "for Nurlykhan Salamatuly have expired : Employee has returned "
        "EPAM-owned hardware Please, review and provide your resolution for "
        "the tasks to make Exit procedure complete. Request details: "
        "End date 31-Jul-2026 Country Kazakhstan City Almaty"
    )
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


# --- parse_employee_info: обычные заявки на оборудование НЕ должны считаться NPR/ER ---
# Регрессия: "hardware"/"equipment" fallback ошибочно помечал любую резолюцию
# по обычной заявке на выдачу/возврат оборудования действующему сотруднику как NPR,
# из-за чего в еженедельный отчет попадали Nurtai Abdyrazakov и Vitali Danichkin
# (Бишкек), хотя это не были ни новые сотрудники, ни увольнения.

def test_parse_employee_info_ignores_provide_non_standard_hardware():
    subject = "Requested Item (RITM) RITM0002314037 has been resolved"
    full_text = (
        "Requested Item (RITM) RITM0002314037 has been resolved by Andrei Trokol "
        "with the following resolution: Closure code: Successful "
        "Closure comment: The laptop Apple MacBook Pro 14 2024 has been provided. "
        "Title: Provide non-standard hardware "
        "Description: Please check Catalog Item user options "
        "Service Recipient: Nurtai Abdyrazakov "
        "Location: Kyrgyzstan, Bishkek"
    )
    assert parse_employee_info(full_text, subject) is None


def test_parse_employee_info_ignores_providing_hardware_manual_task():
    subject = "Catalog Task SCTASK002490595 has been resolved"
    full_text = (
        "Catalog Task SCTASK002490595 has been resolved by Andrei Trokol "
        "with the following resolution: Closure code: Successful "
        "Closure comment: The laptop Apple MacBook Pro 14 2024 has been provided. "
        "Title: Providing non-standard hardware manual task "
        "Description: Please process request manually "
        "Service Recipient: Vitali Danichkin "
        "Location: Kyrgyzstan, Bishkek"
    )
    assert parse_employee_info(full_text, subject) is None


def test_parse_employee_info_ignores_returning_hardware_to_stock():
    subject = "Catalog Task SCTASK002489903 has been resolved"
    full_text = (
        "Catalog Task SCTASK002489903 has been resolved. "
        "Title: Returning EPAM-owned hardware to stock "
        "Description: Please check Catalog Item user options "
        "Service Recipient: Nurtai Abdyrazakov "
        "Location: Kyrgyzstan, Bishkek"
    )
    assert parse_employee_info(full_text, subject) is None


def test_parse_employee_info_still_detects_real_transformation_npr():
    """Настоящий NPR через Transformation from Trainee должен продолжать распознаваться,
    даже если рядом упоминается 'workstation'/оборудование."""
    subject = "Requested Item (RITM) RITM0002313719 has been resolved"
    full_text = (
        "Requested Item (RITM) RITM0002313719 has been resolved by Andrei Trokol "
        "with the following resolution: Closure code: Successful "
        "Closure comment: The laptop HP EliteBook 8 G1i 16 has been provided. "
        "Title: Transformation from Trainee to Employee or Contractor. "
        "Trainee: Malika Razieva, effective from 06 Aug 2026 "
        "Description: Please provide a standard workstation for a Trainee "
        "transitioning to an Employee or Contractor "
        "Service Recipient: Malika Razieva "
        "Location: Kyrgyzstan, Bishkek"
    )
    info = parse_employee_info(full_text, subject)
    assert info is not None
    assert info['name'] == "Malika Razieva"
    assert info['type'] == "NPR"
    assert info['city'] == "Bishkek"


def test_parse_employee_info_child_task_uses_trainee_not_manager_as_recipient():
    """Дочерняя задача (получение оборудования) той же Transformation-заявки:
    Service Recipient здесь - менеджер, забравший оборудование от имени
    трансформируемого сотрудника, а не сам сотрудник. Имя должно браться
    из поля 'Trainee:', а не из 'Service Recipient:'."""
    subject = "Catalog Task SCTASK002489481 has been resolved"
    full_text = (
        "Catalog Task SCTASK002489481 has been resolved by Andrei Trokol "
        "with the following resolution: Closure code Successful Closure "
        "comment Dear colleagues, The laptop HP EliteBook 8 G1i 16 "
        "has been provided. "
        "Title: Transformation from Trainee to Employee or Contractor. "
        "Trainee: Malika Razieva, effective from 06 Aug 2026 "
        "Description: Please provide a standard workstation for a Trainee "
        "transitioning to an Employee or Contractor "
        "Service Recipient: Sabina Klimovich "
        "Location: Kyrgyzstan, Bishkek"
    )
    info = parse_employee_info(full_text, subject)
    assert info is not None
    assert info['name'] == "Malika Razieva"
    assert info['type'] == "NPR"
    assert info['city'] == "Bishkek"


def test_parse_employee_info_npr_title_bracket_name_beats_service_recipient():
    """NPR-тикет: настоящее имя нового сотрудника указано в Title в скобках
    'NPR (date) (Name)', а Service Recipient - это тот, кто забрал
    оборудование от его имени (не сам новый сотрудник). Имя должно браться
    из Title, а не из Service Recipient."""
    subject = "Catalog Task SCTASK002464399 has been closed"
    full_text = (
        "Catalog Task SCTASK002464399 has been resolved. Closure code "
        "Successful Closure comment Dear colleagues, The laptop HP "
        "EliteBook has been provided. Details Catalog Task: "
        "SCTASK002464399 Title: NPR (03 Aug 2026) (Aruzhan Zhumagazykyzy) "
        "Prepare workstation for new employee "
        "Description: Please check Catalog Item user options "
        "Service Recipient: Yuliya Sergeeva "
        "Location: Asia - Central and West/Kazakhstan/Almaty"
    )
    info = parse_employee_info(full_text, subject)
    assert info is not None
    assert info['name'] == "Aruzhan Zhumagazykyzy"
    assert info['type'] == "NPR"
    assert info['city'] == "Almaty"


def test_parse_employee_info_er_title_bracket_name_beats_service_recipient():
    """ER child-тикет (Dismount workstation): настоящее имя увольняющегося
    сотрудника указано в Title в скобках 'ER (date) (Name)', а Service
    Recipient - тот, кто принял оборудование (не сам увольняющийся)."""
    subject = "Requested Item RITM0002288350 resolved"
    full_text = (
        "Requested Item RITM0002288350 resolved. Title: ER (05 Aug 2026) "
        "(Daniil Orlov) Dismount user's workstation [Child RITM for Exit "
        "request] Description: Please check Catalog Item user options "
        "Service Recipient: Aidar Dauylbay "
        "Location: Asia - Central and West/Kazakhstan/Almaty"
    )
    info = parse_employee_info(full_text, subject)
    assert info is not None
    assert info['name'] == "Daniil Orlov"
    assert info['type'] == "ER"
    assert info['city'] == "Almaty"


# --- Инвариант приоритета имени: авторитетные поля ВСЕГДА выше Service
# Recipient, независимо от НОВОЙ, ранее не встречавшейся комбинации ---
# Регрессия архитектурного класса: раньше "Service Recipient" стоял в общем
# плоском списке паттернов и был приоритетнее паттернов "(Name) Dismount"/
# "(Name) Create"/"Exit Task for X", которые находятся в списке НИЖЕ него.
# Если бы такая комбинация встретилась в реальном письме (Service Recipient +
# один из этих паттернов одновременно), баг повторился бы в новой форме,
# несмотря на уже сделанные точечные фиксы для Trainee/Title-bracket.
# Двухуровневая система (_AUTHORITATIVE_NAME_PATTERNS пробуются ПОЛНОСТЬЮ
# раньше _FALLBACK_NAME_PATTERNS) гарантирует, что это в принципе невозможно.

def test_parse_employee_info_exit_task_for_beats_service_recipient_even_when_recipient_comes_first():
    """'Exit Task for X' - авторитетный паттерн, находившийся НИЖЕ Service
    Recipient в старом плоском списке. Даже если Service Recipient упоминается
    в письме РАНЬШЕ 'Exit Task for X' по тексту, должно победить авторитетное
    поле, а не порядок появления в письме."""
    subject = "Exit Task for Assem Dossova has been closed"
    full_text = (
        "Service Recipient: Manager Proxy "
        "Exit Task for Assem Dossova has been closed. "
        "Dismount user's workstation "
        "Location: Kazakhstan, Almaty"
    )
    info = parse_employee_info(full_text, subject)
    assert info is not None
    assert info['name'] == "Assem Dossova"


def test_parse_employee_info_dismount_bracket_beats_service_recipient_even_when_recipient_comes_first():
    """'(Name) Dismount' - авторитетный паттерн, находившийся НИЖЕ Service
    Recipient в старом плоском списке. Новая, ранее не встречавшаяся
    комбинация (Service Recipient идёт раньше по тексту) не должна давать
    неверное имя."""
    subject = "Requested Item RITM0009999999 resolved"
    full_text = (
        "Requested Item RITM0009999999 resolved. "
        "Service Recipient: Office Proxy "
        "(Adilet Bekov) Dismount user's workstation "
        "Location: Kazakhstan, Astana"
    )
    info = parse_employee_info(full_text, subject)
    assert info is not None
    assert info['name'] == "Adilet Bekov"
