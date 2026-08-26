import json
import os
import logging
from datetime import datetime
from bot.config import CHECKPOINT_FILE, REPORT_FILE, REPORT_ME_FILE, DEAD_LETTER_FILE

logger = logging.getLogger(__name__)

class OrderedIdSet:
    """
    Множество с сохранением порядка вставки (на основе dict, который в Python 3.7+
    гарантирует порядок ключей). В отличие от обычного set, позволяет корректно
    обрезать кэш, оставляя именно последние по времени добавленные записи,
    а не случайный набор (что было проблемой при использовании list(set())[-N:]).
    """
    def __init__(self, items=None):
        self._data = dict.fromkeys(items or [])

    def add(self, item):
        # Переставляем элемент в конец, если он уже был (свежий доступ = свежая позиция)
        self._data.pop(item, None)
        self._data[item] = None

    def __contains__(self, item):
        return item in self._data

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def trim(self, max_size, keep_last):
        """Если размер превышает max_size, оставляет только keep_last последних (по времени добавления) элементов."""
        if len(self._data) > max_size:
            keys = list(self._data.keys())[-keep_last:]
            self._data = dict.fromkeys(keys)

    def to_list(self):
        return list(self._data.keys())


class BotState:
    def __init__(self):
        self.processed_emails = OrderedIdSet()
        self.notified_tickets = OrderedIdSet()
        self.last_report_date = None
        self.last_time_reminder_date = None
        self.last_afternoon_time_reminder_date = None
        self.last_evening_thanks_date = None
        self.emails_checked = 0
        self.tickets_sent = 0
        # Метрики для health-check сообщения (см. bot/main.py). Не persist-ятся
        # в чекпоинт намеренно - это счётчики за текущий "жизненный цикл"
        # процесса (сбрасываются при плановом рестарте каждые 12ч), а не
        # накопительная во времени статистика.
        self.failed_sends = 0
        self.poll_count = 0
        self.last_poll_at = None

    def load(self):
        if os.path.exists(CHECKPOINT_FILE):
            try:
                if os.path.getsize(CHECKPOINT_FILE) == 0:
                    logger.info("Чекпоинт пуст, инициализация.")
                    return
                with open(CHECKPOINT_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.processed_emails = OrderedIdSet(data.get('processed_emails', []))
                    self.notified_tickets = OrderedIdSet(data.get('notified_tickets', []))
                    
                    lrd = data.get('last_report_date')
                    if lrd:
                        self.last_report_date = datetime.strptime(lrd, '%Y-%m-%d').date()
                    
                    ltrd = data.get('last_time_reminder_date')
                    if ltrd:
                        self.last_time_reminder_date = datetime.strptime(ltrd, '%Y-%m-%d').date()

                    latrd = data.get('last_afternoon_time_reminder_date')
                    if latrd:
                        self.last_afternoon_time_reminder_date = datetime.strptime(latrd, '%Y-%m-%d').date()

                    letd = data.get('last_evening_thanks_date')
                    if letd:
                        self.last_evening_thanks_date = datetime.strptime(letd, '%Y-%m-%d').date()

                    logger.info(f"Чекпоинт загружен: {len(self.processed_emails)} писем, {len(self.notified_tickets)} тикетов.")
            except json.JSONDecodeError:
                logger.error(f"Ошибка чтения JSON в {CHECKPOINT_FILE}. Файл будет перезаписан.")
            except Exception as e:
                logger.error(f"Ошибка при загрузке чекпоинта: {e}")

    def save(self):
        try:
            data = {
                'processed_emails': self.processed_emails.to_list(),
                'notified_tickets': self.notified_tickets.to_list(),
                'last_report_date': self.last_report_date.strftime('%Y-%m-%d') if self.last_report_date else None,
                'last_time_reminder_date': self.last_time_reminder_date.strftime('%Y-%m-%d') if self.last_time_reminder_date else None,
                'last_afternoon_time_reminder_date': self.last_afternoon_time_reminder_date.strftime('%Y-%m-%d') if self.last_afternoon_time_reminder_date else None,
                'last_evening_thanks_date': self.last_evening_thanks_date.strftime('%Y-%m-%d') if self.last_evening_thanks_date else None
            }
            tmp_file = CHECKPOINT_FILE + '.tmp'
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            os.replace(tmp_file, CHECKPOINT_FILE)
        except Exception as e:
            logger.error(f"Ошибка при сохранении чекпоинта: {e}")

# Глобальный объект стейта
state = BotState()

def load_report(is_me=False):
    target_file = REPORT_ME_FILE if is_me else REPORT_FILE
    if os.path.exists(target_file):
        if os.path.isdir(target_file):
            logger.error(f"Критическая ошибка: {target_file} является директорией!")
            return {}
        with open(target_file, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if not is_me and "npr" in data and isinstance(data["npr"], list):
                    return {}
                return data
            except Exception as e:
                logger.error(f"Ошибка при чтении отчета {target_file}: {e}")
                return {}
    return {}

def save_report(data, is_me=False):
    target_file = REPORT_ME_FILE if is_me else REPORT_FILE
    try:
        tmp_file = target_file + '.tmp'
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_file, target_file)
    except Exception as e:
        logger.error(f"Ошибка при сохранении отчета {target_file}: {e}")


# Максимум записей в dead-letter файле. Это диагностический журнал для
# обнаружения дрифта формата письма (см. bot/parser.py::parse_employee_info_with_reason),
# а не полноценный архив - если формат "сломался" один раз, письма будут
# продолжать сыпаться с той же reason при каждом опросе почты, пока фикс не
# задеплоят, поэтому cap нужен, чтобы файл не рос бесконечно.
DEAD_LETTER_MAX_SIZE = 200


def load_dead_letters():
    """
    Загружает список dead-letter записей (см. append_dead_letter). Возвращает
    [], если файл не существует, пуст или повреждён - отсутствие/порча этого
    диагностического файла не должна мешать основной работе бота.
    """
    if not os.path.exists(DEAD_LETTER_FILE):
        return []
    try:
        if os.path.getsize(DEAD_LETTER_FILE) == 0:
            return []
        with open(DEAD_LETTER_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Ошибка при чтении dead-letter файла {DEAD_LETTER_FILE}: {e}")
        return []


def append_dead_letter(reason, ticket_id=None):
    """
    Добавляет одну dead-letter запись - письмо, которое ПОХОЖЕ на финальное
    NPR/ER-событие (прошло классификацию is_final + NPR/ER), но
    parse_employee_info() всё равно вернул None (подозрение на дрифт формата
    письма ServiceNow, см. bot/parser.py::_parse_employee_info_impl).

    ВАЖНО (GDPR): в запись НЕ попадает ни текст/subject письма, ни
    извлечённое имя сотрудника, ни любой другой потенциально персональный
    фрагмент. Subject писем ServiceNow часто содержит полное имя сотрудника
    НАПРЯМУЮ (см. паттерн 'Exit Task for {Name}' в
    _AUTHORITATIVE_NAME_PATTERNS, который матчится именно по subject) - т.е.
    просто сохранить subject "как есть" означало бы гарантированно писать
    ФИО в JSON-файл на диске и потенциально светить его в Teams-канале через
    health-check (см. bot/main.py). Поэтому в запись попадает только:
      - reason        - техническая причина ('name_not_found'/'city_not_found');
      - ticket_id      - номер тикета ServiceNow (RITM.../INC...), если он
                          был найден в письме - сам по себе не персональные
                          данные, но достаточен, чтобы найти письмо руками
                          в почтовом ящике или в ServiceNow для расследования.
    """
    entries = load_dead_letters()
    entries.append({
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'reason': reason,
        'ticket_id': ticket_id,
    })
    if len(entries) > DEAD_LETTER_MAX_SIZE:
        entries = entries[-DEAD_LETTER_MAX_SIZE:]
    try:
        tmp_file = DEAD_LETTER_FILE + '.tmp'
        with open(tmp_file, 'w', encoding='utf-8') as f:
            json.dump(entries, f, ensure_ascii=False, indent=4)
        os.replace(tmp_file, DEAD_LETTER_FILE)
    except Exception as e:
        logger.error(f"Ошибка при сохранении dead-letter файла {DEAD_LETTER_FILE}: {e}")
