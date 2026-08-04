import json
import os
import logging
from datetime import datetime
from bot.config import CHECKPOINT_FILE, REPORT_FILE, REPORT_ME_FILE

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
        self.emails_checked = 0
        self.tickets_sent = 0

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
                'last_afternoon_time_reminder_date': self.last_afternoon_time_reminder_date.strftime('%Y-%m-%d') if self.last_afternoon_time_reminder_date else None
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
