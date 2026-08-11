"""
Unit-тесты для bot/reports.py: сбор данных для еженедельного отчёта.

Запуск: pytest tests/test_reports.py -v
"""
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import bot.reports as reports_module
from bot.reports import extract_report_data, _parse_report_date, _current_report_window_start


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


# Фиксированная граница отчётного окна для всех тестов ниже (понедельник
# 00:00 UTC), передаётся явно как report_window_start - тесты не должны
# зависеть от реальной текущей даты системы.
_WINDOW_START = datetime(2026, 8, 3, 0, 0, 0, tzinfo=timezone.utc)  # понедельник


# --- _parse_report_date ---

def test_parse_report_date_handles_iso_format():
    assert _parse_report_date("2026-08-06") == datetime(2026, 8, 6)


def test_parse_report_date_handles_dd_mon_yyyy_format():
    assert _parse_report_date("31 Jul 2026") == datetime(2026, 7, 31)


def test_parse_report_date_returns_none_for_unknown():
    assert _parse_report_date("Unknown") is None
    assert _parse_report_date(None) is None
    assert _parse_report_date("garbage") is None


# --- _current_report_window_start ---

def test_current_report_window_start_returns_monday_midnight_utc():
    # Пятница 07 Aug 2026 -> понедельник этой же недели 03 Aug 2026 00:00 UTC.
    friday = datetime(2026, 8, 7, 15, 30, 0, tzinfo=timezone.utc)
    result = _current_report_window_start(friday)
    assert result == datetime(2026, 8, 3, 0, 0, 0, tzinfo=timezone.utc)


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
    # Событие (05 Aug) внутри текущего отчётного окна (началось 03 Aug) -
    # запись должна попасть в отчёт с датой события, а не письма (07 Aug).
    received = datetime(2026, 8, 7, 10, 0, 0, tzinfo=timezone.utc)

    extract_report_data(
        full_text, subject, received_date=received, is_middle_east=False,
        report_window_start=_WINDOW_START,
    )

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

    extract_report_data(
        full_text, subject, received_date=received, is_middle_east=False,
        report_window_start=_WINDOW_START,
    )

    assert "Jane Doe" in store.data
    assert store.data["Jane Doe"]["date"] == "2026-08-07"


def test_extract_report_data_skips_event_before_current_report_window(monkeypatch):
    """Регрессия (Maharramov): административный child-тикет (например, дозакрытие
    возврата оборудования) может прийти спустя много дней после реальной даты
    события. Если эта дата раньше начала ТЕКУЩЕГО отчётного окна, событие
    гарантированно уже было учтено в одном из прошлых отчётов и не должно
    попасть в текущий отчёт."""
    store = _patch_store(monkeypatch)

    subject = "NPR (01 Jul 2026) has been resolved"
    full_text = (
        "NPR (01 Jul 2026) has been resolved. "
        "Employee Name: Maharramov Test "
        "Location: Almaty "
        "Title: NPR"
    )
    # Письмо пришло в текущем окне, но событие (01 Jul) - за месяц ДО начала
    # текущего окна (03 Aug).
    received = datetime(2026, 8, 7, 10, 0, 0, tzinfo=timezone.utc)

    extract_report_data(
        full_text, subject, received_date=received, is_middle_east=False,
        report_window_start=_WINDOW_START,
    )

    assert "Maharramov Test" not in store.data


def test_extract_report_data_skips_event_from_few_days_before_window_start(monkeypatch):
    """Ключевой случай, который старая эвристика ('дней с даты письма')
    пропускала: событие прошлой недели (за 2 дня до начала текущего окна),
    дочерний тикет по которому пришёл всего через несколько дней. Разница
    между письмом и событием МЕНЬШЕ старого порога (9 дней), поэтому старая
    эвристика НЕ отфильтровала бы эту запись - но она уже точно попала в
    прошлый отчёт, т.к. её дата раньше начала текущего окна."""
    store = _patch_store(monkeypatch)

    subject = "NPR (01 Aug 2026) has been resolved"
    full_text = (
        "NPR (01 Aug 2026) has been resolved. "
        "Employee Name: Late Child Ticket "
        "Location: Almaty "
        "Title: NPR"
    )
    # Письмо пришло через 4 дня после события - старый порог (9 дней) НЕ
    # отфильтровал бы это. Но событие (01 Aug) раньше начала текущего окна
    # (03 Aug) - новая логика должна отфильтровать.
    received = datetime(2026, 8, 5, 10, 0, 0, tzinfo=timezone.utc)

    extract_report_data(
        full_text, subject, received_date=received, is_middle_east=False,
        report_window_start=_WINDOW_START,
    )

    assert "Late Child Ticket" not in store.data


def test_extract_report_data_keeps_event_on_first_day_of_window(monkeypatch):
    """Событие, случившееся ровно в начале текущей отчётной недели
    (понедельник), должно попадать в отчёт, а не отфильтровываться."""
    store = _patch_store(monkeypatch)

    subject = "NPR (03 Aug 2026) has been resolved"
    full_text = (
        "NPR (03 Aug 2026) has been resolved. "
        "Employee Name: Alex Recent "
        "Location: Astana "
        "Title: NPR"
    )
    received = datetime(2026, 8, 7, 10, 0, 0, tzinfo=timezone.utc)

    extract_report_data(
        full_text, subject, received_date=received, is_middle_east=False,
        report_window_start=_WINDOW_START,
    )

    assert "Alex Recent" in store.data
    assert store.data["Alex Recent"]["date"] == "03 Aug 2026"


def test_extract_report_data_uses_current_window_when_not_passed(monkeypatch):
    """Если report_window_start не передан вызывающим кодом (например, из
    одноразового скрипта), функция должна вычислить границу самостоятельно
    от реальной текущей даты, а не пропускать проверку вовсе."""
    store = _patch_store(monkeypatch)

    subject = "NPR (01 Jan 2000) has been resolved"
    full_text = (
        "NPR (01 Jan 2000) has been resolved. "
        "Employee Name: Ancient Event "
        "Location: Almaty "
        "Title: NPR"
    )
    received = datetime.now(timezone.utc)

    extract_report_data(full_text, subject, received_date=received, is_middle_east=False)

    assert "Ancient Event" not in store.data
