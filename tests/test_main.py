"""
Unit-тесты для bot/main.py: process_message() - обработка одного письма
(first-run backlog, quick dedup, парсинг тикета, отправка в Teams).

Запуск: pytest tests/test_main.py -v
"""
import sys
import os
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import bot.main as main_module
from bot.main import process_message
from bot.storage import BotState


class _FakeRecipient:
    def __init__(self, address, name=None):
        self.address = address
        self.name = name


class _FakeMessage:
    """Минимальная заглушка объекта O365 Message, достаточная для process_message()."""
    def __init__(self, object_id, subject, body, received, to=None, cc=None, sender=None):
        self.object_id = object_id
        self.subject = subject
        self.body = body
        self.received = received
        self.to = to or []
        self.cc = cc or []
        self.sender = sender or _FakeRecipient("noreply@servicenow.company.com")


_ALMATY_TO = [_FakeRecipient("kz-almaty@company.com", "Kazakhstan Almaty Team")]
_UZ_TO = [_FakeRecipient("uz-team@company.com", "Uzbekistan Team")]

_NOW_UTC = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)  # понедельник
_MONDAY_START = _NOW_UTC.replace(hour=0, minute=0, second=0, microsecond=0)


def _fresh_state(monkeypatch):
    """Заменяет глобальный bot.storage.state на чистый BotState для теста,
    и патчит ссылку на него в bot.main (импортирован как `from bot.storage import state`)."""
    new_state = BotState()
    monkeypatch.setattr(main_module, "state", new_state)
    return new_state


def _ticket_body(location="Almaty", priority="3 - Low", title="Some issue"):
    return f"Title: {title} Priority: {priority} Location: {location} Status: New"


# --- Базовый дедуп по message.object_id ---

def test_process_message_skips_already_processed_email(monkeypatch):
    st = _fresh_state(monkeypatch)
    st.processed_emails.add("msg-1")

    msg = _FakeMessage("msg-1", "INC0001 issue", _ticket_body(), _NOW_UTC, to=_ALMATY_TO)

    monkeypatch.setattr(main_module, "send_ticket_card", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))

    process_message(msg, is_first_run=False, now_utc=_NOW_UTC, monday_start=_MONDAY_START)
    # Не должно упасть - раннее возвращение до любой отправки.


def test_process_message_skips_email_older_than_report_window(monkeypatch):
    """Письма старше начала текущей недели (monday_start) полностью
    игнорируются - помечаются processed без какой-либо обработки."""
    st = _fresh_state(monkeypatch)
    old_date = _MONDAY_START - timedelta(days=5)
    msg = _FakeMessage("msg-old", "INC0002 issue", _ticket_body(), old_date, to=_ALMATY_TO)

    called = {"ticket_card": False}
    monkeypatch.setattr(main_module, "send_ticket_card", lambda *a, **k: called.update(ticket_card=True))

    process_message(msg, is_first_run=False, now_utc=_NOW_UTC, monday_start=_MONDAY_START)

    assert "msg-old" in st.processed_emails
    assert called["ticket_card"] is False


# --- First-run backlog logic ---

def test_process_message_first_run_old_email_cached_without_sending(monkeypatch):
    """При первом запуске старые письма (>3ч) молча добавляются в кэш
    processed/notified БЕЗ отправки в Teams (защита от спама старым бэклогом)."""
    st = _fresh_state(monkeypatch)
    old_but_in_window = _MONDAY_START + timedelta(hours=1)  # старше 3ч от now_utc, но внутри недели
    msg = _FakeMessage("msg-backlog", "INC0003 old ticket", _ticket_body(), old_but_in_window, to=_ALMATY_TO)

    sent = {"called": False}
    monkeypatch.setattr(main_module, "send_ticket_card", lambda *a, **k: sent.update(called=True))
    monkeypatch.setattr(main_module, "extract_report_data", lambda *a, **k: None)

    process_message(msg, is_first_run=True, now_utc=_NOW_UTC, monday_start=_MONDAY_START)

    assert "msg-backlog" in st.processed_emails
    assert "INC0003" in st.notified_tickets
    assert sent["called"] is False


def test_process_message_first_run_recent_email_sent_normally(monkeypatch):
    """Регрессия (инцидент 2026-08-04, RITM0002315801): письма, полученные
    недавно (< FIRST_RUN_RECENT_HOURS), должны обрабатываться как ОБЫЧНЫЕ
    новые письма с реальной отправкой в Teams, даже если is_first_run=True -
    защита от потери уведомлений из-за "ложного" первого запуска."""
    st = _fresh_state(monkeypatch)
    recent = _NOW_UTC - timedelta(hours=1)  # моложе FIRST_RUN_RECENT_HOURS (3ч)
    msg = _FakeMessage("msg-recent", "RITM0002315801 new request", _ticket_body(), recent, to=_ALMATY_TO)

    sent = {"called": False}

    def fake_send(ticket, mention_key=None, webhook_url=None):
        sent["called"] = True
        return True

    monkeypatch.setattr(main_module, "send_ticket_card", fake_send)
    monkeypatch.setattr(main_module, "extract_report_data", lambda *a, **k: None)

    process_message(msg, is_first_run=True, now_utc=_NOW_UTC, monday_start=_MONDAY_START)

    assert sent["called"] is True
    assert "RITM0002315801" in st.notified_tickets
    assert "msg-recent" in st.processed_emails


# --- Quick dedup по номеру тикета ---

def test_process_message_quick_dedup_skips_already_notified_ticket(monkeypatch):
    st = _fresh_state(monkeypatch)
    st.notified_tickets.add("INC0009")
    msg = _FakeMessage("msg-dup", "INC0009 has been assigned", _ticket_body(), _NOW_UTC, to=_ALMATY_TO)

    monkeypatch.setattr(main_module, "extract_report_data", lambda *a, **k: None)
    sent = {"called": False}
    monkeypatch.setattr(main_module, "send_ticket_card", lambda *a, **k: sent.update(called=True))

    process_message(msg, is_first_run=False, now_utc=_NOW_UTC, monday_start=_MONDAY_START)

    assert sent["called"] is False
    assert "msg-dup" in st.processed_emails


# --- IGNORE branch ---

def test_process_message_ignores_ticket_marked_ignore(monkeypatch):
    st = _fresh_state(monkeypatch)
    # Письмо без ticket_id и без SLA-алерта -> parse_ticket() вернёт 'IGNORE'.
    msg = _FakeMessage("msg-ignore", "just a random subject", "Title: x", _NOW_UTC, to=_ALMATY_TO)

    monkeypatch.setattr(main_module, "extract_report_data", lambda *a, **k: None)
    sent = {"called": False}
    monkeypatch.setattr(main_module, "send_ticket_card", lambda *a, **k: sent.update(called=True))

    process_message(msg, is_first_run=False, now_utc=_NOW_UTC, monday_start=_MONDAY_START)

    assert sent["called"] is False
    assert "msg-ignore" in st.processed_emails


# --- Успешная отправка тикета ---

def test_process_message_sends_ticket_and_marks_notified_on_success(monkeypatch):
    st = _fresh_state(monkeypatch)
    msg = _FakeMessage("msg-ok", "INC0010 real incident", _ticket_body(priority="1 - Critical"), _NOW_UTC, to=_ALMATY_TO)

    monkeypatch.setattr(main_module, "extract_report_data", lambda *a, **k: None)

    captured = {}

    def fake_send(ticket, mention_key=None, webhook_url=None):
        captured["ticket"] = ticket
        captured["mention_key"] = mention_key
        return True

    monkeypatch.setattr(main_module, "send_ticket_card", fake_send)

    process_message(msg, is_first_run=False, now_utc=_NOW_UTC, monday_start=_MONDAY_START)

    assert "INC0010" in st.notified_tickets
    assert "msg-ok" in st.processed_emails
    assert captured["ticket"]["ticket_id"] == "INC0010"
    assert captured["mention_key"] == "almaty"


def test_process_message_middle_east_uses_dedicated_webhook(monkeypatch):
    """Тикеты, определённые как Middle East, должны отправляться на
    TEAMS_MIDDLE_EAST_WEBHOOK_URL с mention_key='middle_east', а не на
    обычный CIS-канал с обычным mention_key локации."""
    _fresh_state(monkeypatch)
    dubai_to = [_FakeRecipient("uae-team@company.com", "UAE Team")]
    body = "Title: Server issue Priority: 2 Location: Dubai Status: New"
    msg = _FakeMessage("msg-me", "INC0011 dubai incident", body, _NOW_UTC, to=dubai_to)

    monkeypatch.setattr(main_module, "extract_report_data", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "is_middle_east_message", lambda *a, **k: True)
    monkeypatch.setattr(main_module, "TEAMS_MIDDLE_EAST_WEBHOOK_URL", "https://fake-me-webhook.example.com")

    captured = {}

    def fake_send(ticket, mention_key=None, webhook_url=None):
        captured["mention_key"] = mention_key
        captured["webhook_url"] = webhook_url
        return True

    monkeypatch.setattr(main_module, "send_ticket_card", fake_send)

    process_message(msg, is_first_run=False, now_utc=_NOW_UTC, monday_start=_MONDAY_START)

    assert captured["mention_key"] == "middle_east"
    assert captured["webhook_url"] == "https://fake-me-webhook.example.com"


# --- Неудачная отправка: тикет НЕ помечается уведомлённым (retry позже) ---

def test_process_message_does_not_mark_notified_when_send_fails(monkeypatch):
    """Регрессия: раньше тикет помечался notified ДО отправки, из-за чего
    неудачные отправки (сеть недоступна, невалидный webhook) молча теряли
    уведомление навсегда без единого шанса на повтор. Теперь при sent_ok=False
    тикет НЕ должен попадать в notified_tickets, чтобы следующий цикл поллинга
    (через 60с) повторил попытку."""
    st = _fresh_state(monkeypatch)
    msg = _FakeMessage("msg-fail", "INC0012 real incident", _ticket_body(), _NOW_UTC, to=_ALMATY_TO)

    monkeypatch.setattr(main_module, "extract_report_data", lambda *a, **k: None)
    monkeypatch.setattr(main_module, "send_ticket_card", lambda *a, **k: False)

    process_message(msg, is_first_run=False, now_utc=_NOW_UTC, monday_start=_MONDAY_START)

    assert "INC0012" not in st.notified_tickets
    # Письмо НЕ должно быть помечено processed - чтобы следующий poll его снова увидел.
    assert "msg-fail" not in st.processed_emails


# --- Критичные тикеты и "quick dedup" ---

def test_process_message_quick_dedup_applies_even_to_critical_sla_alert(monkeypatch):
    """ВАЖНОЕ НАБЛЮДЕНИЕ (не баг, вносимый этим тестом, а существующее
    поведение кода): "quick dedup" в process_message() ищет ticket_id ПО
    SUBJECT через тот же самый regex `(INC\\d+|RITM\\d+)`, который позже
    использует parse_ticket() для извлечения real_ticket_id. Из-за этого
    quick dedup срабатывает и возвращается из функции ДО того, как
    ticket['is_critical'] вообще вычисляется - т.е. байпас дедупа для
    критичных тикетов (`if t_id and t_id in notified_tickets and not
    ticket['is_critical']` внутри process_message) на практике недостижим
    для обычных писем, у которых номер тикета есть уже в самом subject.
    Этот тест фиксирует ТЕКУЩЕЕ поведение, чтобы будущий рефакторинг не
    сломал его молча в непредвиденную сторону."""
    st = _fresh_state(monkeypatch)
    st.notified_tickets.add("INC0099")
    body = "Title: SLA breach Priority: 1 - Critical Location: Almaty SLA reached 95%"
    msg = _FakeMessage("msg-crit", "INC0099 SLA violation reached 95%", body, _NOW_UTC, to=_ALMATY_TO)

    monkeypatch.setattr(main_module, "extract_report_data", lambda *a, **k: None)
    sent = {"called": False}

    def fake_send(ticket, mention_key=None, webhook_url=None):
        sent["called"] = True
        return True

    monkeypatch.setattr(main_module, "send_ticket_card", fake_send)

    process_message(msg, is_first_run=False, now_utc=_NOW_UTC, monday_start=_MONDAY_START)

    assert sent["called"] is False
    assert "msg-crit" in st.processed_emails


# --- Геотег по получателям, когда явной Location в письме нет ---

def test_process_message_resolves_country_tag_from_recipients_for_uzbekistan(monkeypatch):
    _fresh_state(monkeypatch)
    body = "Title: Some request Priority: 3 Location: Tashkent Status: New"
    msg = _FakeMessage("msg-uz", "RITM0020 request", body, _NOW_UTC, to=_UZ_TO)

    monkeypatch.setattr(main_module, "extract_report_data", lambda *a, **k: None)
    captured = {}

    def fake_send(ticket, mention_key=None, webhook_url=None):
        captured["ticket"] = ticket
        return True

    monkeypatch.setattr(main_module, "send_ticket_card", fake_send)

    process_message(msg, is_first_run=False, now_utc=_NOW_UTC, monday_start=_MONDAY_START)

    assert captured["ticket"]["tag_str"] == "[UZ]"
