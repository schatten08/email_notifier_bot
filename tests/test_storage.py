"""
Unit-тесты для bot/storage.py: OrderedIdSet, BotState (load/save чекпоинта)
и load_report/save_report (еженедельные отчёты).

Запуск: pytest tests/test_storage.py -v
"""
import sys
import os
import json
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import bot.storage as storage_module
from bot.storage import OrderedIdSet, BotState, load_report, save_report, load_dead_letters, append_dead_letter


# --- OrderedIdSet ---

def test_ordered_id_set_starts_empty():
    s = OrderedIdSet()
    assert len(s) == 0
    assert "x" not in s


def test_ordered_id_set_add_and_contains():
    s = OrderedIdSet()
    s.add("a")
    s.add("b")
    assert "a" in s
    assert "b" in s
    assert "c" not in s
    assert len(s) == 2


def test_ordered_id_set_preserves_insertion_order():
    s = OrderedIdSet(["a", "b", "c"])
    assert list(s) == ["a", "b", "c"]
    assert s.to_list() == ["a", "b", "c"]


def test_ordered_id_set_readd_moves_item_to_end():
    """Повторное добавление уже существующего элемента должно переставлять
    его в конец (свежий доступ = свежая позиция), а не создавать дубликат."""
    s = OrderedIdSet(["a", "b", "c"])
    s.add("a")
    assert s.to_list() == ["b", "c", "a"]
    assert len(s) == 3


def test_ordered_id_set_trim_keeps_last_n_when_over_max_size():
    s = OrderedIdSet([str(i) for i in range(10)])
    s.trim(max_size=10, keep_last=5)
    # Не превышает max_size (10) - trim не должен ничего менять.
    assert len(s) == 10

    s2 = OrderedIdSet([str(i) for i in range(1200)])
    s2.trim(max_size=1000, keep_last=500)
    assert len(s2) == 500
    # Должны остаться именно ПОСЛЕДНИЕ по времени добавления элементы.
    assert s2.to_list() == [str(i) for i in range(700, 1200)]


def test_ordered_id_set_trim_noop_when_under_max_size():
    s = OrderedIdSet(["a", "b", "c"])
    s.trim(max_size=1000, keep_last=500)
    assert s.to_list() == ["a", "b", "c"]


# --- BotState.load ---

def test_botstate_load_when_checkpoint_file_missing(tmp_path, monkeypatch):
    checkpoint = tmp_path / "bot_checkpoint.json"
    monkeypatch.setattr(storage_module, "CHECKPOINT_FILE", str(checkpoint))

    state = BotState()
    state.load()  # Файл не существует - должно молча инициализировать пустое состояние.

    assert len(state.processed_emails) == 0
    assert len(state.notified_tickets) == 0
    assert state.last_report_date is None


def test_botstate_load_when_checkpoint_file_empty(tmp_path, monkeypatch):
    checkpoint = tmp_path / "bot_checkpoint.json"
    checkpoint.write_text("", encoding="utf-8")
    monkeypatch.setattr(storage_module, "CHECKPOINT_FILE", str(checkpoint))

    state = BotState()
    state.load()  # Пустой файл (0 байт) - не должно кидать исключение при json.load.

    assert len(state.processed_emails) == 0


def test_botstate_load_when_checkpoint_file_corrupt(tmp_path, monkeypatch):
    """Регрессия: испорченный (не-JSON) чекпоинт не должен крашить бот при
    старте - должен просто залогировать ошибку и продолжить с пустым state."""
    checkpoint = tmp_path / "bot_checkpoint.json"
    checkpoint.write_text("{not valid json!!!", encoding="utf-8")
    monkeypatch.setattr(storage_module, "CHECKPOINT_FILE", str(checkpoint))

    state = BotState()
    state.load()  # Не должно бросить исключение.

    assert len(state.processed_emails) == 0


def test_botstate_load_restores_all_fields_from_valid_checkpoint(tmp_path, monkeypatch):
    checkpoint = tmp_path / "bot_checkpoint.json"
    data = {
        "processed_emails": ["email-1", "email-2"],
        "notified_tickets": ["INC0001", "RITM0002"],
        "last_report_date": "2026-08-07",
        "last_time_reminder_date": "2026-08-07",
        "last_afternoon_time_reminder_date": "2026-08-07",
        "last_evening_thanks_date": "2026-08-06",
    }
    checkpoint.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(storage_module, "CHECKPOINT_FILE", str(checkpoint))

    state = BotState()
    state.load()

    assert "email-1" in state.processed_emails
    assert "email-2" in state.processed_emails
    assert "INC0001" in state.notified_tickets
    assert "RITM0002" in state.notified_tickets
    assert state.last_report_date == date(2026, 8, 7)
    assert state.last_time_reminder_date == date(2026, 8, 7)
    assert state.last_afternoon_time_reminder_date == date(2026, 8, 7)
    assert state.last_evening_thanks_date == date(2026, 8, 6)


# --- BotState.save ---

def test_botstate_save_creates_file_atomically(tmp_path, monkeypatch):
    """save() должен писать во временный .tmp файл и затем атомарно
    переименовывать его в целевой файл (os.replace), а не писать напрямую -
    защита от порчи файла при краше посередине записи."""
    checkpoint = tmp_path / "bot_checkpoint.json"
    monkeypatch.setattr(storage_module, "CHECKPOINT_FILE", str(checkpoint))

    state = BotState()
    state.processed_emails.add("email-1")
    state.notified_tickets.add("INC0001")
    state.last_report_date = date(2026, 8, 7)
    state.save()

    assert checkpoint.exists()
    assert not (tmp_path / "bot_checkpoint.json.tmp").exists()  # tmp-файл не должен остаться

    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["processed_emails"] == ["email-1"]
    assert saved["notified_tickets"] == ["INC0001"]
    assert saved["last_report_date"] == "2026-08-07"


def test_botstate_save_then_load_roundtrip(tmp_path, monkeypatch):
    checkpoint = tmp_path / "bot_checkpoint.json"
    monkeypatch.setattr(storage_module, "CHECKPOINT_FILE", str(checkpoint))

    original = BotState()
    original.processed_emails.add("email-1")
    original.processed_emails.add("email-2")
    original.notified_tickets.add("RITM0009")
    original.last_evening_thanks_date = date(2026, 8, 6)
    original.save()

    restored = BotState()
    restored.load()

    assert restored.processed_emails.to_list() == ["email-1", "email-2"]
    assert "RITM0009" in restored.notified_tickets
    assert restored.last_evening_thanks_date == date(2026, 8, 6)


def test_botstate_save_does_not_persist_runtime_metrics(tmp_path, monkeypatch):
    """emails_checked/tickets_sent/failed_sends/poll_count/last_poll_at -
    это метрики за текущий жизненный цикл процесса (для health-check),
    они намеренно НЕ сохраняются в чекпоинт и не восстанавливаются при load()."""
    checkpoint = tmp_path / "bot_checkpoint.json"
    monkeypatch.setattr(storage_module, "CHECKPOINT_FILE", str(checkpoint))

    state = BotState()
    state.emails_checked = 42
    state.tickets_sent = 10
    state.failed_sends = 2
    state.poll_count = 100
    state.save()

    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert "emails_checked" not in saved
    assert "tickets_sent" not in saved
    assert "failed_sends" not in saved
    assert "poll_count" not in saved


# --- load_report / save_report ---

def test_load_report_returns_empty_dict_when_file_missing(tmp_path, monkeypatch):
    report_file = tmp_path / "weekly_report.json"
    monkeypatch.setattr(storage_module, "REPORT_FILE", str(report_file))

    assert load_report(is_me=False) == {}


def test_save_report_then_load_report_roundtrip(tmp_path, monkeypatch):
    report_file = tmp_path / "weekly_report.json"
    monkeypatch.setattr(storage_module, "REPORT_FILE", str(report_file))

    data = {"John Smith": {"city": "Almaty", "type": "NPR", "date": "2026-08-07"}}
    save_report(data, is_me=False)

    assert report_file.exists()
    assert load_report(is_me=False) == data


def test_save_report_uses_separate_file_for_middle_east(tmp_path, monkeypatch):
    report_file = tmp_path / "weekly_report.json"
    report_me_file = tmp_path / "weekly_report_me.json"
    monkeypatch.setattr(storage_module, "REPORT_FILE", str(report_file))
    monkeypatch.setattr(storage_module, "REPORT_ME_FILE", str(report_me_file))

    save_report({"CIS Person": {"city": "Almaty"}}, is_me=False)
    save_report({"ME Person": {"city": "Dubai"}}, is_me=True)

    assert load_report(is_me=False) == {"CIS Person": {"city": "Almaty"}}
    assert load_report(is_me=True) == {"ME Person": {"city": "Dubai"}}


def test_load_report_returns_empty_dict_on_corrupt_json(tmp_path, monkeypatch):
    report_file = tmp_path / "weekly_report.json"
    report_file.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(storage_module, "REPORT_FILE", str(report_file))

    assert load_report(is_me=False) == {}


def test_load_report_returns_empty_dict_when_target_is_directory(tmp_path, monkeypatch):
    report_dir = tmp_path / "weekly_report.json"
    report_dir.mkdir()
    monkeypatch.setattr(storage_module, "REPORT_FILE", str(report_dir))

    assert load_report(is_me=False) == {}


def test_load_report_ignores_legacy_npr_list_format_for_cis(tmp_path, monkeypatch):
    """Старый (legacy) формат отчёта CIS хранил данные как {'npr': [...]} -
    список, а не словарь по имени. load_report должен распознать такой
    устаревший формат и вернуть {} вместо словаря со списком внутри."""
    report_file = tmp_path / "weekly_report.json"
    report_file.write_text(json.dumps({"npr": ["John", "Jane"]}), encoding="utf-8")
    monkeypatch.setattr(storage_module, "REPORT_FILE", str(report_file))

    assert load_report(is_me=False) == {}


def test_save_report_creates_file_atomically(tmp_path, monkeypatch):
    report_file = tmp_path / "weekly_report.json"
    monkeypatch.setattr(storage_module, "REPORT_FILE", str(report_file))

    save_report({"John Smith": {"city": "Almaty"}}, is_me=False)

    assert report_file.exists()
    assert not (tmp_path / "weekly_report.json.tmp").exists()


# --- load_dead_letters / append_dead_letter ---
# См. bot/parser.py::parse_employee_info_with_reason и
# bot/reports.py::extract_report_data - dead-letter журнал для обнаружения
# дрифта формата письма ServiceNow (письмо ПОХОЖЕ на финальное NPR/ER-событие,
# но извлечь имя/город не удалось).

def test_load_dead_letters_returns_empty_list_when_file_missing(tmp_path, monkeypatch):
    dl_file = tmp_path / "dead_letters.json"
    monkeypatch.setattr(storage_module, "DEAD_LETTER_FILE", str(dl_file))

    assert load_dead_letters() == []


def test_load_dead_letters_returns_empty_list_on_corrupt_json(tmp_path, monkeypatch):
    dl_file = tmp_path / "dead_letters.json"
    dl_file.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(storage_module, "DEAD_LETTER_FILE", str(dl_file))

    assert load_dead_letters() == []


def test_append_dead_letter_then_load_roundtrip(tmp_path, monkeypatch):
    dl_file = tmp_path / "dead_letters.json"
    monkeypatch.setattr(storage_module, "DEAD_LETTER_FILE", str(dl_file))

    append_dead_letter("name_not_found", ticket_id="RITM0001")

    entries = load_dead_letters()
    assert len(entries) == 1
    assert entries[0]["reason"] == "name_not_found"
    assert entries[0]["ticket_id"] == "RITM0001"
    assert "timestamp" in entries[0]


def test_append_dead_letter_never_stores_pii_fields(tmp_path, monkeypatch):
    """GDPR-guard: dead-letter запись должна содержать ТОЛЬКО технические
    метаданные (timestamp/reason/ticket_id) - никакого текста письма, subject
    или имени сотрудника, независимо от того, что вызывающий код мог бы (по
    ошибке) попытаться туда передать в будущем."""
    dl_file = tmp_path / "dead_letters.json"
    monkeypatch.setattr(storage_module, "DEAD_LETTER_FILE", str(dl_file))

    append_dead_letter("city_not_found", ticket_id="RITM0002")

    entries = load_dead_letters()
    assert set(entries[0].keys()) == {"timestamp", "reason", "ticket_id"}


def test_append_dead_letter_accumulates_multiple_entries(tmp_path, monkeypatch):
    dl_file = tmp_path / "dead_letters.json"
    monkeypatch.setattr(storage_module, "DEAD_LETTER_FILE", str(dl_file))

    append_dead_letter("name_not_found", ticket_id="RITM0001")
    append_dead_letter("city_not_found", ticket_id="RITM0002")

    entries = load_dead_letters()
    assert len(entries) == 2
    assert entries[0]["ticket_id"] == "RITM0001"
    assert entries[1]["ticket_id"] == "RITM0002"


def test_append_dead_letter_caps_at_max_size(tmp_path, monkeypatch):
    """Файл не должен расти бесконечно, если один и тот же дрифт формата
    продолжает генерировать записи при каждом опросе почты - должны
    оставаться только последние DEAD_LETTER_MAX_SIZE записей."""
    dl_file = tmp_path / "dead_letters.json"
    monkeypatch.setattr(storage_module, "DEAD_LETTER_FILE", str(dl_file))
    monkeypatch.setattr(storage_module, "DEAD_LETTER_MAX_SIZE", 5)

    for i in range(8):
        append_dead_letter("name_not_found", ticket_id=f"RITM{i:04d}")

    entries = load_dead_letters()
    assert len(entries) == 5
    assert entries[0]["ticket_id"] == "RITM0003"
    assert entries[-1]["ticket_id"] == "RITM0007"


def test_append_dead_letter_creates_file_atomically(tmp_path, monkeypatch):
    dl_file = tmp_path / "dead_letters.json"
    monkeypatch.setattr(storage_module, "DEAD_LETTER_FILE", str(dl_file))

    append_dead_letter("name_not_found", ticket_id="RITM0001")

    assert dl_file.exists()
    assert not (tmp_path / "dead_letters.json.tmp").exists()
