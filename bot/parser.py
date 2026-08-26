import re
from bs4 import BeautifulSoup
from bot.config import MIDDLE_EAST_EMAILS, ME_KEYWORDS

def cleanup_html(html_str):
    """Очищает текст письма от HTML тегов с помощью BeautifulSoup."""
    if not html_str:
        return ""
    # Используем html.parser, так как он встроен в Python и не требует lxml
    soup = BeautifulSoup(str(html_str), "html.parser")
    # Извлекаем текст, заменяя теги на пробелы, чтобы слова не склеивались
    text = soup.get_text(separator=" ")
    # Убираем лишние пробелы и неразрывные пробелы (\xa0)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def _extract_table_fields(body):
    """
    Извлекает пары label->value из HTML-таблиц письма ServiceNow вида
    <tr><td class="table-label...">Label:</td><td class="table-value...">Value</td></tr>
    (а также table-bordered-label/inner-table-label - те же паттерны в других
    блоках шаблона). Возвращает dict {normalized_label: value}, где label -
    текст ячейки в нижнем регистре, без конечного ':' и лишних пробелов.

    АРХИТЕКТУРНАЯ ПРИЧИНА: письма ServiceNow (подтверждено реальным письмом,
    см. tests/fixtures/servicenow_er_child_ritm.html) устроены как регулярная
    табличная структура - каждая строка формы - это ОТДЕЛЬНАЯ пара <td>
    (label/value), а не плоский текст. Граница значения задана HTML-тегом,
    а не угадана regex-ом со списком "стоп-слов" (см. _STOP_WORDS), который
    приходилось вручную дополнять каждый раз, когда рядом с нужным полем
    появлялось новое соседнее поле, не входившее в список. Использование
    этой структуры полностью устраняет класс багов "значение поля утекло в
    соседнее поле/оборвалось раньше времени".

    Если два поля в письме встречаются с одинаковым label (например,
    "Location" - в блоке Details и "Current Location" - в Request Details,
    это разные labels, коллизий нет; но если бы совпали) - побеждает ПЕРВОЕ
    найденное значение (порядок появления в документе), т.к. блок Details
    обычно идёт раньше и содержит самые релевантные для карточки поля.

    Возвращает {} для не-табличных писем (обычный плоский текст, включая все
    существующие юнит-тесты, где body - это строка вроде "Title: ... Priority:
    ..." без HTML) - в этом случае вызывающий код должен использовать старый
    regex-парсинг по плоскому тексту как fallback.
    """
    if not body:
        return {}
    soup = BeautifulSoup(str(body), "html.parser")
    fields = {}
    for row in soup.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) != 2:
            continue
        label_cell, value_cell = cells
        label_classes = label_cell.get("class") or []
        if not any("label" in cls for cls in label_classes):
            continue
        label = label_cell.get_text(separator=" ", strip=True).rstrip(":").strip().lower()
        value = value_cell.get_text(separator=" ", strip=True)
        if label and label not in fields:
            fields[label] = value
    return fields


def is_middle_east_message(message, recipients_info, clean_body):
    """Определяет, относится ли письмо к Ближнему Востоку."""
    sender = message.sender.address.lower()
    if any(me_email in sender for me_email in MIDDLE_EAST_EMAILS):
        return True
    
    for info in recipients_info:
        if any(me_email in info for me_email in MIDDLE_EAST_EMAILS) or \
           any(re.search(rf'\b{re.escape(kw)}\b', info, re.IGNORECASE) for kw in ME_KEYWORDS):
            return True
            
    lb = clean_body.lower()
    ls = message.subject.lower()
    if any(re.search(rf'\b{re.escape(kw)}\b', ls, re.IGNORECASE) for kw in ME_KEYWORDS) or \
       any(re.search(rf'\b{re.escape(kw)}\b', lb, re.IGNORECASE) for kw in ME_KEYWORDS):
        return True
        
    return False

# Паттерны, чьё совпадение ГАРАНТИРОВАННО относится к нужному сотруднику
# (новому сотруднику для NPR, увольняющемуся для ER) - это явно поименованные
# поля/конструкции, где по смыслу поля не может стоять другой человек.
# Пробуются В ЭТОМ порядке (порядок ВНУТРИ уровня всё ещё важен: "Title: ...
# (date) (Name)" должен проверяться до общих construkций типа "Exit Task for",
# т.к. subject может содержать оба варианта одновременно), но КАЖДЫЙ из них
# принципиально приоритетнее любого fallback-паттерна ниже (см.
# _FALLBACK_NAME_PATTERNS) - это гарантия на архитектурном уровне, а не по
# отдельному найденному кейсу.
_AUTHORITATIVE_NAME_PATTERNS = [
    r'Employee Name\s*:?\s*([A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+(?:\s+[A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+){1,3})',
    r'Trainee\s*:\s*([A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+(?:\s+[A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+){1,3})',
    r'Title:\s*(?:ER|NPR|ReR)?[^()]*\((?:[^)]+)\)\s*\(([^)]+)\)',
    r'Exit Task for\s+([A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+(?:\s+[A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+){1,3})',
    r'\(([A-Z][a-z]+\s+[A-Z][a-z]+)\)\s*Dismount',
    r'\(([A-Z][a-z]+\s+[A-Z][a-z]+)\)\s*Create',
]

# Fallback-паттерны: имя, извлечённое отсюда, МОЖЕТ относиться не к самому
# сотруднику, а к человеку, действующему от его имени (например, менеджер,
# забравший оборудование). Пробуются ТОЛЬКО если ни один авторитетный паттерн
# выше не сработал - независимо от того, в каком порядке они встречаются
# в тексте письма.
_FALLBACK_NAME_PATTERNS = [
    r'Service Recipient\s*:?\s*([A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+(?:\s+[A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+){1,3})',
]


def _clean_extracted_name(raw_name):
    """
    Отрезает от совпадения regex-а всё, что попало туда лишним (следующие
    поля формы, служебные слова), и проверяет, что осталось похожее на
    настоящее имя (минимум два слова). Возвращает None, если после очистки
    результат не похож на имя.
    """
    potential_name = raw_name.strip()
    potential_name = re.split(r'SLA|Location|Dismissal|Date|requires|has|is|Floor|Room| \n|\t|\n', potential_name)[0].strip()
    if len(potential_name.split()) > 4:
        potential_name = " ".join(potential_name.split()[:3])
    if len(potential_name.split()) >= 2:
        return potential_name
    return None


def _parse_employee_info_impl(full_text, subject, raw_body=None):
    """
    Основная реализация разбора NPR/ER-данных о сотруднике. Возвращает
    кортеж (info_dict_or_None, reason_or_None).

    reason - причина, по которой info оказался None, но ТОЛЬКО для случаев,
    которые являются потенциальным ДРИФТОМ ФОРМАТА письма (риск молчаливой
    потери реальной записи в отчёте), а не для обычных "письмо вообще не
    относится к NPR/ER" случаев (это подавляющее большинство писем и не
    должно шуметь как аномалия):
      - None       - письмо не финальное (is_final=False), либо не
                      NPR/ER-запрос вовсе, либо явно исключено бизнес-
                      правилом (Student/Trainee) - ожидаемое, не аномалия.
      - 'name_not_found' - письмо ПОХОЖЕ на финальное NPR/ER-событие
                      (is_final=True, классификация NPR/ER сработала), но ни
                      один паттерн имени не совпал - подозрение на дрифт
                      формата (новое поле с именем, которое паттерны не
                      покрывают).
      - 'city_not_found'  - то же самое, но имя нашлось, а Location/город
                      не распознан - подозрение на дрифт формата локации.

    Публичные обёртки ниже (parse_employee_info/parse_employee_info_with_reason)
    используют этот кортеж по-разному: первая (обратно совместимая, все
    существующие вызовы) отбрасывает reason, вторая (используется в
    extract_report_data() для dead-letter, см. bot/reports.py) его возвращает.
    """
    table_fields = _extract_table_fields(raw_body) if raw_body else {}

    is_final = any(kw in subject.lower() for kw in ['resolved', 'closed', 'exit task', 'completed', 'expired'])
    if not is_final:
        if any(kw in full_text.lower() for kw in ['has been resolved', 'has been closed', 'has been provided', 'successfully provided']):
            is_final = True

    # "requires approval" в subject - это ещё не согласованный запрос (pending
    # approval), а не финальное событие. Без этой проверки, например, subject
    # "Exit Task for Artur Muratov requires approval" ложно триггерит
    # is_final=True из-за подстроки "exit task" в самом названии задачи, хотя
    # увольнение ещё даже не согласовано. Проверяем только subject (не
    # full_text), т.к. тело реально закрытого письма может содержать
    # цитированный старый текст с этой фразой из переписки.
    #
    # "has expired" в subject - это НЕ успешное завершение, а наоборот:
    # exit-задача (например, "вернуть оборудование") просрочена и не была
    # выполнена/подтверждена (напоминание вида "Exit Task for X has
    # expired"). Ключевое слово 'expired' изначально ловило эти письма как
    # финальные из-за совпадения по 'exit task', что приводило к ложному
    # попаданию в отчёт сотрудников, чья реальная дата увольнения была задолго
    # до отчётного окна (например, Nurlykhan Salamatuly, End date 31 Jul, но
    # напоминание "has expired" пришло 06 Aug).
    if 'requires approval' in subject.lower() or 'has expired' in subject.lower():
        is_final = False

    if not is_final:
        return None, None
    
    is_npr = False
    is_er = False
    is_trans = 'Transformation from Trainee' in full_text or 'Transformation Request' in full_text
    
    if 'Relocation' in subject or 'Relocation' in full_text or 'ReR (' in subject or 'ReR (' in full_text:
        if any(kw in full_text.lower() or kw in subject.lower() for kw in ['dismount', 'exit task', 'return', 'returned', 'last working']):
            is_er = True
        elif any(kw in full_text.lower() or kw in subject.lower() for kw in ['prepare', 'create the workstation', 'get workstation', 'new working place']):
            is_npr = True
    
    if not (is_npr or is_er):
        if any(kw in full_text for kw in ['NPR', 'New Profile', 'Prepare workstation']):
            is_npr = True
        elif is_trans:
            is_npr = True

    if not (is_npr or is_er):
        # ВАЖНО: 'ER' проверяется через regex с границей слова (\bER\b), а НЕ
        # простой substring-проверкой 'ER ' in full_text/subject. Substring-
        # вариант ложно совпадал с окончанием ЛЮБОГО слова на "er" перед
        # пробелом (LETTER, MANAGER, OFFICER, TRANSFER, CUSTOMER, PROVIDER,
        # ORDER, OWNER, PARTNER, PROPER и т.д.), из-за чего письма, вообще не
        # относящиеся к увольнению сотрудника (например, "Termination of
        # Lease agreement" про аренду офиса), ложно классифицировались как
        # ER и попадали в отчёт с name=None.
        if re.search(r'\bER\b', full_text) or re.search(r'\bER\b', subject) or \
           any(kw in full_text or kw in subject for kw in ['Exit Request', 'Dismount']):
            is_er = True

    if not (is_npr or is_er):
        return None, None
    
    if not is_trans:
        if re.search(r'(Title|Employee title|Employment type):.*(Student|Trainee)', full_text, re.IGNORECASE):
            return None, None
    
    name = None

    # Двухуровневая система извлечения имени вместо одной плоской цепочки
    # regex-паттернов, пробуемых по порядку. АРХИТЕКТУРНАЯ ПРИЧИНА: "Service
    # Recipient" - это НЕ надёжное поле, оно может означать как самого
    # сотрудника, так и человека, который физически забрал/вернул
    # оборудование ОТ ЕГО ИМЕНИ (менеджер, коллега). Все остальные поля ниже
    # (_AUTHORITATIVE_NAME_PATTERNS) - это поля, где извлечённое имя
    # ГАРАНТИРОВАННО относится к самому нужному сотруднику (Employee Name,
    # Trainee:, имя в скобках из Title, "Exit Task for X" из subject).
    #
    # Раньше все паттерны были одним плоским списком, и порядок между
    # "Service Recipient" и авторитетными полями подбирался ТОЧЕЧНО, отдельным
    # патчем на каждый новый найденный кейс (сначала Trainee: приоритизировали
    # над Service Recipient, затем отдельно Title-bracket). Это не давало
    # гарантии на будущее: у "Service Recipient" оставались МЕНЕЕ приоритетные
    # позиции только относительно уже известных полей, но не относительно
    # ВСЕХ авторитетных полей сразу - т.е. новая комбинация "новое авторитетное
    # поле, стоявшее в списке ниже Service Recipient" могла повторить тот же
    # баг. Теперь Service Recipient принципиально пробуется ТОЛЬКО если НИ ОДИН
    # авторитетный паттерн не сработал - независимо от порядка внутри самого
    # авторитетного уровня.
    for pattern in _AUTHORITATIVE_NAME_PATTERNS:
        m = re.search(pattern, full_text if 'Title:' in pattern else (subject + " " + full_text), re.IGNORECASE)
        if m:
            candidate = _clean_extracted_name(m.group(1))
            if candidate:
                name = candidate
                break

    if not name:
        for pattern in _FALLBACK_NAME_PATTERNS:
            m = re.search(pattern, subject + " " + full_text, re.IGNORECASE)
            if m:
                candidate = _clean_extracted_name(m.group(1))
                if candidate:
                    name = candidate
                    break

    # ЗАЩИТА В ГЛУБИНУ: если ни один паттерн имени не сработал, письмо не
    # относится к реальному NPR/ER-запросу конкретного сотрудника (либо это
    # письмо не про сотрудника вовсе, а классификация is_npr/is_er сработала
    # ложно на какое-то совпадение по ключевому слову) - в отчёт такая запись
    # не должна попадать под именем None (что выглядит как "None (ER) | ..."
    # в финальном отчёте и не несёт пользы получателю).
    #
    # ЭТО ЖЕ - главная точка ДРИФТА ФОРМАТА (см. dead-letter в
    # bot/reports.py::extract_report_data): письмо УЖЕ прошло проверку
    # is_final=True и классификацию NPR/ER, то есть это, по всем признакам,
    # ПОХОЖЕ на реальное финальное NPR/ER-событие конкретного сотрудника -
    # но ни один паттерн имени не совпал. Это именно тот случай, когда
    # реальная запись рискует тихо потеряться из отчёта, а не просто письмо,
    # изначально не относящееся к NPR/ER.
    if not name:
        return None, 'name_not_found'

    req_date = "Unknown"
    # Table-source приоритетнее: "Dismissal Date System"/"Dismissal Date" в
    # табличных письмах ServiceNow - надёжное поле с однозначной границей
    # значения (см. _extract_table_fields), в отличие от regex по plain-тексту
    # ниже, где граница значения угадывается по следующему известному слову.
    table_date = table_fields.get("dismissal date system") or table_fields.get("dismissal date") or table_fields.get("start date") or table_fields.get("first working day")
    if table_date:
        req_date = table_date.split(' ')[0] if '-' in table_date and re.match(r'^\d{4}-\d{2}-\d{2}', table_date) else table_date
    else:
        m_date = re.search(r'(?:effective from|Dismissal Date|Start Date|First Working Day)[:\s]*(\d{4}-\d{2}-\d{2}|\d+\s*[A-Z][a-z]+\s*\d{4})', full_text, re.IGNORECASE)
        if m_date:
            req_date = m_date.group(1).split(' ')[0] if '-' in m_date.group(1) else m_date.group(1)
        else:
            m_title_date = re.search(r'(?:NPR|ER|Transformation)[^()]*\(([^)]+)\)', subject, re.IGNORECASE)
            if m_title_date:
                req_date = m_title_date.group(1)

    city = None
    if 'Relocation Request' in subject or 'Relocation Request' in full_text:
         if 'kyrgyzstan' in full_text.lower() or 'bishkek' in full_text.lower():
             city = 'Bishkek'
         elif 'uzbekistan' in full_text.lower() or 'tashkent' in full_text.lower():
             city = 'Tashkent'

    # Table-source приоритетнее для Location: границу значения задаёт закрывающий
    # </td>, а не список стоп-слов ('Description'/'Service'/'Priority'/'Title'),
    # который приходилось вручную дополнять при появлении нового соседнего поля.
    table_location = table_fields.get("location") or table_fields.get("current location")
    if not city and table_location:
        loc_val = table_location.lower()
        if 'kyrgyzstan' in loc_val: city = 'Bishkek'
        elif 'uzbekistan' in loc_val: city = 'Tashkent'
        if not city:
            for c in ['Almaty', 'Astana', 'Bishkek', 'Karaganda', 'Tashkent']:
                if c.lower() in loc_val:
                    city = c
                    break

    if not city:
        m_loc = re.search(r'Location:\s*(.*?)(?:\n|Description|Service|Priority|Title|$)', full_text, re.IGNORECASE)
        if m_loc:
            loc_val = m_loc.group(1).lower()
            if 'kyrgyzstan' in loc_val: city = 'Bishkek'
            elif 'uzbekistan' in loc_val: city = 'Tashkent'
            if not city:
                for c in ['Almaty', 'Astana', 'Bishkek', 'Karaganda', 'Tashkent']:
                    if c.lower() in loc_val:
                        city = c
                        break
    
    if not city:
        t = full_text.lower()
        if 'kyrgyzstan' in t: city = 'Bishkek'
        elif 'uzbekistan' in t: city = 'Tashkent'
        if not city:
            for c in ['Almaty', 'Astana', 'Bishkek', 'Karaganda', 'Tashkent']:
                if c.lower() in t:
                    city = c
                    break
    
    if not city:
        for me_c in ['Dubai', 'Abu Dhabi', 'Qatar', 'Doha', 'Saudi Arabia', 'Riyadh', 'Kuwait', 'Oman', 'Muscat', 'Jordan', 'Amman']:
            if me_c.lower() in full_text.lower():
                city = me_c
                break

    # Вторая точка потенциального ДРИФТА ФОРМАТА: имя уже нашлось (значит,
    # это точно письмо про конкретного сотрудника, прошедшее классификацию
    # NPR/ER), но город/локация не распознан - реальная запись рискует
    # потеряться из отчёта из-за незнакомого варианта написания локации.
    if not city:
        return None, 'city_not_found'

    link = "#"
    ticket_id = "ServiceNow"
    id_match = re.search(r'(RITM\d+|SCTASK\d+)', full_text)
    if id_match:
        ticket_id = id_match.group(1)

    sn_link_match = re.search(r'(https://[^/]*\.service-now\.com/\S+)', full_text)
    if sn_link_match:
        link = sn_link_match.group(1).rstrip('.')
    else:
        people_link_match = re.search(r'(https://processes\.people\.epam\.com/\S+)', full_text)
        if people_link_match:
            link = people_link_match.group(1).rstrip('.')

    return {
        'name': name,
        'city': city,
        'type': 'NPR' if (is_npr or is_trans) else 'ER',
        'date': req_date,
        'link': link,
        'ticket_id': ticket_id
    }, None


def parse_employee_info(full_text, subject, raw_body=None):
    """
    Извлекает данные о сотруднике (NPR/ER) для отчета.
    Возвращает словарь с данными или None.

    raw_body (опционально) - исходный HTML письма. Если передан и содержит
    распознаваемую табличную структуру (см. _extract_table_fields), поля
    Location/Dismissal Date System берутся оттуда - как более надёжный
    источник, где граница значения задана HTML-тегом, а не угадана regex-ом.
    ВАЖНО: сама классификация NPR/ER и все паттерны извлечения ИМЕНИ
    сотрудника (_AUTHORITATIVE_NAME_PATTERNS/_FALLBACK_NAME_PATTERNS) остаются
    полностью НЕТРОНУТЫМИ и продолжают работать по full_text/subject как
    раньше - таблица используется только там, где раньше применялся
    "открытый" regex со списком стоп-слов (тот же класс риска, что и в
    _extract_ticket_fields). Если raw_body не передан (все существующие
    вызовы/тесты) - поведение идентично прежнему (table_fields = {}).

    Тонкая обёртка над _parse_employee_info_impl(), отбрасывающая reason -
    сохраняет прежний публичный контракт (info_dict_or_None) для всех
    существующих вызывающих. См. parse_employee_info_with_reason() для
    варианта, который возвращает и причину отказа (используется в
    bot/reports.py для dead-letter писем с подозрением на дрифт формата).
    """
    info, _reason = _parse_employee_info_impl(full_text, subject, raw_body=raw_body)
    return info


def parse_employee_info_with_reason(full_text, subject, raw_body=None):
    """
    То же самое, что parse_employee_info(), но возвращает кортеж
    (info_dict_or_None, reason_or_None). reason непустой (см. docstring
    _parse_employee_info_impl) ТОЛЬКО когда письмо прошло классификацию как
    финальное NPR/ER-событие конкретного сотрудника, но извлечение имени или
    города не удалось - то есть когда есть реальный риск, что письмо было
    потенциальной NPR/ER-записью, которая тихо потерялась бы из отчёта.
    Используется bot/reports.py::extract_report_data() для записи такого
    письма в dead-letter (data/dead_letters.json), чтобы дрифт формата
    письма ServiceNow обнаруживал сам бот, а не пользователь постфактум.
    """
    return _parse_employee_info_impl(full_text, subject, raw_body=raw_body)

_CIS_CITIES = ["almaty", "astana", "karaganda", "tashkent", "bishkek"]
_CIS_COUNTRY_TAG_MAP = {"[KZ]": "kazakhstan", "[UZ]": "uzbekistan", "[KG]": "kyrgyzstan"}
_STOP_WORDS = r'(?:Service\s*:|Status\s*:|Description\s*:|Priority\s*:|Service Recipient\s*:|SLA Target Date\s*:|Location\s*:|Request Details|Comments:|Ref:|This is an automatically|$)'


def _resolve_cis_mention_key(clean_body, country_tag):
    """
    Определяет ключ упоминания (город/страна СНГ) на основе поля Location в письме
    или, если оно отсутствует, на основе тега страны, определенного по получателям.
    Возвращает None, если письмо не относится ни к одной разрешенной локации СНГ
    (в этом случае вызывающий код должен проигнорировать письмо).
    """
    allowed_countries = ["kazakhstan", "uzbekistan", "kyrgyzstan", "казахстан", "узбекистан", "кыргызстан"]
    loc_search = re.search(r'Location:\s*(.*?)(?:\s{2,}|Title:|Alert:|IP:|Status:|\n|$)', clean_body, re.IGNORECASE)
    found_location = loc_search.group(1).lower() if loc_search else ""

    if found_location:
        for city in _CIS_CITIES:
            if city in found_location:
                return city

        for country in allowed_countries:
            if country in found_location:
                if "казах" in country or "kazakh" in country:
                    return "kazakhstan"
                elif "узбек" in country or "uzbek" in country:
                    return "uzbekistan"
                elif "кыргыз" in country or "kyrgyz" in country:
                    return "kyrgyzstan"
                return country

        return None

    if country_tag:
        return _CIS_COUNTRY_TAG_MAP.get(country_tag)

    return None


def _should_ignore_ticket(subject, clean_body):
    """Проверяет все условия, при которых письмо должно быть полностью проигнорировано."""
    if "Alert:" in clean_body and "Status:" in clean_body:
        return True

    if any(kw in subject for kw in ["WO00", "Work Order", "SCTASK"]):
        return True

    if "ZABBIX" in subject.upper() or "Auto_EPM" in subject:
        return True

    ignore_keywords = [
        "has been closed", "has been resolved", "resolved", "closed", "withdrawn",
        "has been suspended", "has been updated", "has a new comment",
        "comment has been added", "has been put on hold",
        "has been removed from hold", "no longer on hold", "has been resumed",
        "is back at work", "back at work",
        "снят с удержания", "возобновлен", "снято с удержания",
        "new profile request",
        "incident has been resolved", "request has been resolved"
    ]
    if any(kw in subject.lower() for kw in ignore_keywords):
        return True

    if re.search(r'Status:\s*(Resolved|Closed|Completed)', clean_body, re.IGNORECASE):
        return True

    return False


def _detect_sla_alert(subject, clean_body):
    """Определяет, является ли письмо алертом об истечении SLA."""
    lower_subject = subject.lower()
    if "sla" in lower_subject and ("reached" in lower_subject or "%" in lower_subject or "violation" in lower_subject):
        return True
    if "sla" in lower_subject and "has reached" in clean_body.lower():
        return True
    return False


def _extract_ticket_fields(clean_body, raw_body=None):
    """
    Извлекает title/description/priority/location для карточки тикета.

    Приоритет источника поля - табличная структура письма (см.
    _extract_table_fields), т.к. там граница значения задана HTML-тегом, а
    не угадана regex-ом. raw_body - исходный (неочищенный от тегов) HTML
    письма; если он передан и содержит распознаваемую table-label/table-value
    структуру, поле берётся оттуда. Для писем без такой структуры (обычный
    плоский текст - в т.ч. все синтетические тексты в существующих юнит-
    тестах) используется старый regex-парсинг по clean_body как fallback,
    чтобы поведение для таких писем осталось прежним.
    """
    table_fields = _extract_table_fields(raw_body) if raw_body else {}

    def _extract(pattern):
        m = re.search(pattern + _STOP_WORDS, clean_body, re.IGNORECASE)
        return m.group(1).strip() if m else ""

    title = table_fields.get("title") or _extract(r'Title:\s*(.*?)') or "No title"
    desc = table_fields.get("description") or table_fields.get("comments") or _extract(r'(?:Description:|Comments:?)\s*(.*?)')
    priority = table_fields.get("priority") or _extract(r'Priority:\s*(.*?)')
    location = table_fields.get("location") or _extract(r'Location:\s*(.*?)')

    if len(title) > 80:
        title = title[:80] + "..."
    if len(location) > 80:
        location = location[:80] + "..."
    if len(priority) > 30:
        priority = priority[:30] + "..."

    return title, desc, priority, location


def _detect_me_tag(location):
    """Определяет короткий тег страны Ближнего Востока по локации тикета."""
    low_loc = location.lower()
    if "uae" in low_loc or "dubai" in low_loc or "abu dhabi" in low_loc:
        return "[UAE]"
    elif "qatar" in low_loc or "doha" in low_loc:
        return "[QA]"
    elif "saudi" in low_loc or "riyadh" in low_loc:
        return "[SA]"
    elif "kuwait" in low_loc:
        return "[KW]"
    elif "oman" in low_loc or "muscat" in low_loc:
        return "[OM]"
    elif "jordan" in low_loc or "amman" in low_loc:
        return "[JO]"
    return "[ME]"


def _extract_sla_percent(subject, clean_body):
    """
    Извлекает процент исчерпания SLA (например, 85 из "SLA reached 85%"),
    чтобы показать его в уведомлении отдельным фактом, а не только текстовой пометкой.
    Возвращает int (0-100) или None, если процент не найден.
    """
    m = re.search(r'(\d{1,3})\s*%', subject) or re.search(r'(\d{1,3})\s*%', clean_body)
    if not m:
        return None
    try:
        value = int(m.group(1))
    except ValueError:
        return None
    return value if 0 <= value <= 100 else None


_CIS_CITY_LABELS = {
    "almaty": "Almaty", "astana": "Astana", "karaganda": "Karaganda",
    "tashkent": "Tashkent", "bishkek": "Bishkek",
}
_CIS_COUNTRY_LABELS = {
    "kazakhstan": "Kazakhstan", "uzbekistan": "Uzbekistan", "kyrgyzstan": "Kyrgyzstan",
}


def _short_location_label(location, mention_key, is_middle_east):
    """
    Возвращает короткое, читаемое название локации (город/страна) для отображения
    в карточке крупным планом, вместо длинного полного пути вида
    "Asia - Central and West/Kazakhstan/Almaty/Almaty". Полный путь остаётся
    доступным по кнопке "Показать полностью" в самой карточке.
    """
    if is_middle_east:
        return _detect_me_tag(location).strip("[]")
    if mention_key:
        if mention_key in _CIS_CITY_LABELS:
            return _CIS_CITY_LABELS[mention_key]
        if mention_key in _CIS_COUNTRY_LABELS:
            return _CIS_COUNTRY_LABELS[mention_key]
    return location or ""


def parse_ticket(subject, body, country_tag="", is_middle_east=False):
    """
    Парсит письмо, проверяет фильтры и возвращает структурированные данные тикета
    (dict) для последующего построения Adaptive Card в bot/teams.py, либо строку
    'IGNORE', если письмо должно быть полностью проигнорировано.
    """
    clean_body = cleanup_html(body)

    mention_key = None
    if not is_middle_east:
        mention_key = _resolve_cis_mention_key(clean_body, country_tag)
        if not mention_key:
            return 'IGNORE'

    if _should_ignore_ticket(subject, clean_body):
        return 'IGNORE'

    is_sla_alert = _detect_sla_alert(subject, clean_body)

    ticket_match = re.search(r'(INC\d+|RITM\d+)', subject)
    if not ticket_match and not is_sla_alert:
        return 'IGNORE'

    # real_ticket_id - настоящий номер тикета (или None, если это "голый" SLA-алерт
    # без привязки к конкретному INC/RITM). display_id используется только для
    # отображения в карточке и НЕ используется для дедупликации/кеша, чтобы
    # несколько разных безномерных SLA-алертов не считались одним и тем же тикетом.
    real_ticket_id = ticket_match.group(1) if ticket_match else None
    display_id = real_ticket_id or "SLA Alert"

    ticket_url = ""
    if real_ticket_id:
        link_match = re.search(
            fr'href=["\'](https?://[^"\']+)["\'][^>]*>(?:<[^>]+>)*\s*{real_ticket_id}',
            str(body), re.IGNORECASE
        )
        if link_match:
            ticket_url = link_match.group(1).replace('&amp;', '&')

    title, desc, priority, location = _extract_ticket_fields(clean_body, raw_body=body)

    current_mention = mention_key
    if location:
        for city in _CIS_CITIES:
            if city in location.lower():
                current_mention = city
                break

    if is_sla_alert:
        header_icon, header_label = "⏰", "WARNING: SLA Alert"
    elif real_ticket_id and real_ticket_id.startswith("INC"):
        header_icon, header_label = "🔴", "Incident"
    elif real_ticket_id and real_ticket_id.startswith("RITM"):
        header_icon, header_label = "🟢", "RITM Request"
    else:
        header_icon, header_label = "📝", "Ticket"

    if is_middle_east:
        tag_str = _detect_me_tag(location)
    else:
        tag_str = country_tag if country_tag else (f"[{mention_key.upper()}]" if mention_key else "")

    priority_level = None
    is_critical = is_sla_alert
    if priority:
        low_priority = priority.lower()
        if "1" in priority or "critical" in low_priority:
            priority_level = "critical"
            is_critical = True
        elif "2" in priority or "high" in low_priority:
            priority_level = "high"
        else:
            priority_level = "normal"

    sla_percent = _extract_sla_percent(subject, clean_body) if is_sla_alert else None
    location_short = _short_location_label(location, current_mention, is_middle_east)

    return {
        'ticket_id': real_ticket_id,
        'display_id': display_id,
        'ticket_url': ticket_url,
        'header_icon': header_icon,
        'header_label': header_label,
        'tag_str': tag_str,
        'title': title,
        'priority': priority,
        'priority_level': priority_level,
        'location': location,
        'location_short': location_short,
        'description': desc,
        'is_critical': is_critical,
        'is_sla_alert': is_sla_alert,
        'sla_percent': sla_percent,
        'mention_key': current_mention,
    }
