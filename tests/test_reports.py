"""
Unit-тесты для bot/reports.py: сбор данных для еженедельного отчёта.

Запуск: pytest tests/test_reports.py -v
"""
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import bot.reports as reports_module
from bot.reports import extract_report_data, _parse_report_date


class _FakeStore:
    """Простая замена load_report/save_report на in-memory словарь, чтобы не
    трогать реальный data/weekly_report.json в тестах."""
    def __init__(self):
        self.data = {}

    def load(self, is_me=False):
        return dict(self.data)

    def save(self, d, is_me=False):
        self.data = dict(d)


def _patch_store(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(reports_module, "load_report", store.load)
    monkeypatch.setattr(reports_module, "save_report", store.save)
    return store


# --- _parse_report_date ---

def test_parse_report_date_handles_iso_format():
    assert _parse_report_date("2026-08-06") == datetime(2026, 8, 6)


def test_parse_report_date_handles_dd_mon_yyyy_format():
    assert _parse_report_date("31 Jul 2026") == datetime(2026, 7, 31)


def test_parse_report_date_returns_none_for_unknown():
    assert _parse_report_date("Unknown") is None
    assert _parse_report_date(None) is None
    assert _parse_report_date("garbage") is None


# --- extract_report_data: реальная дата события вместо даты письма ---

def test_extract_report_data_uses_real_event_date_not_received_date(monkeypatch):
    """Регрессия (Maharramov-класс): раньше info['date'] всегда перезатирался
    датой ПОЛУЧЕНИЯ письма, даже если parse_employee_info() уже извлёк реальную
    дату события (например, Dismissal Date) из тела письма."""
    store = _patch_store(monkeypatch)

    subject = "NPR (05 Aug 2026) has been resolved"
    full_text = (
        "NPR (05 Aug 2026) has been resolved. "
        "Employee Name: John Smith "
        "Location: Almaty "
        "Title: NPR"
    )
    # Письмо получено на 2 дня позже реальной даты события - в пределах порога,
    # запись должна попасть в отчёт с датой события (05 Aug), а не письма (07 Aug).
    received = datetime(2026, 8, 7, 10, 0, 0, tzinfo=timezone.utc)

    extract_report_data(full_text, subject, received_date=received, is_middle_east=False)

    assert "John Smith" in store.data
    assert store.data["John Smith"]["date"] == "05 Aug 2026"


def test_extract_report_data_falls_back_to_received_date_when_event_date_unknown(monkeypatch):
    """Если parse_employee_info() не смог извлечь реальную дату события,
    received_date должен использоваться как раньше (fallback)."""
    store = _patch_store(monkeypatch)

    subject = "Relocation Request: Exit Task for Jane Doe has been closed"
    full_text = (
        "Relocation Request: Exit Task for Jane Doe has been closed. "
        "Employee Name: Jane Doe "
        "Location: Tashkent, Uzbekistan "
        "last working day is set"
    )
    received = datetime(2026, 8, 7, 10, 0, 0, tzinfo=timezone.utc)

    extract_report_data(full_text, subject, received_date=received, is_middle_east=False)

    assert "Jane Doe" in store.data
    assert store.data["Jane Doe"]["date"] == "2026-08-07"


def test_extract_report_data_skips_stale_event_from_late_child_ticket(monkeypatch):
    """Регрессия (Maharramov): административный child-тикет (например, дозакрытие
    возврата оборудования) может прийти спустя много дней после реальной даты
    события, которая уже наверняка учтена в одном из прошлых отчётов. Такая
    запись не должна попадать в ТЕКУЩЕЕ отчётное окно."""
    store = _patch_store(monkeypatch)

    subject = "NPR (01 Jul 2026) has been resolved"
    full_text = (
        "NPR (01 Jul 2026) has been resolved. "
        "Employee Name: Maharramov Test "
        "Location: Almaty "
        "Title: NPR"
    )
    # Письмо пришло на месяц позже реальной даты события - далеко за порогом.
    received = datetime(2026, 8, 7, 10, 0, 0, tzinfo=timezone.utc)

    extract_report_data(full_text, subject, received_date=received, is_middle_east=False)

    assert "Maharramov Test" not in store.data


def test_extract_report_data_keeps_event_within_threshold(monkeypatch):
    """Событие, случившееся в начале текущей отчётной недели (например,
    в понедельник, письмо пришло в пятницу), не должно отфильтровываться -
    расхождение в пределах порога STALE_EVENT_DAYS_THRESHOLD."""
    store = _patch_store(monkeypatch)

    subject = "NPR (03 Aug 2026) has been resolved"
    full_text = (
        "NPR (03 Aug 2026) has been resolved. "
        "Employee Name: Alex Recent "
        "Location: Astana "
        "Title: NPR"
    )
    received = datetime(2026, 8, 7, 10, 0, 0, tzinfo=timezone.utc)  # +4 дня

    extract_report_data(full_text, subject, received_date=received, is_middle_east=False)

    assert "Alex Recent" in store.data
    assert store.data["Alex Recent"]["date"] == "03 Aug 2026"
