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

def parse_employee_info(full_text, subject):
    """
    Извлекает данные о сотруднике (NPR/ER) для отчета.
    Возвращает словарь с данными или None.
    """
    is_final = any(kw in subject.lower() for kw in ['resolved', 'closed', 'exit task', 'completed', 'expired'])
    if not is_final:
        if any(kw in full_text.lower() for kw in ['has been resolved', 'has been closed', 'has been provided', 'successfully provided']):
            is_final = True
            
    if not is_final:
        return None
    
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
        elif 'hardware' in full_text.lower() or 'equipment' in full_text.lower():
            if not any(kw in full_text.lower() or kw in subject.lower() for kw in ['exit', 'dismount', 'return', 'er (']):
                is_npr = True
        elif is_trans:
            is_npr = True

    if not (is_npr or is_er):
        if any(kw in full_text or kw in subject for kw in ['ER ', 'Exit Request', 'Dismount']):
            is_er = True

    if not (is_npr or is_er):
        return None
    
    if not is_trans:
        if re.search(r'(Title|Employee title|Employment type):.*(Student|Trainee)', full_text, re.IGNORECASE):
            return None
    
    name = None
    name_patterns = [
        r'Employee Name\s*:?\s*([A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+(?:\s+[A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+){1,3})',
        r'Service Recipient\s*:?\s*([A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+(?:\s+[A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+){1,3})',
        r'Trainee\s*:?\s*([A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+(?:\s+[A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+){1,3})',
        r'Exit Task for\s+([A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+(?:\s+[A-Zа-яА-Я][a-zа-яA-ZА-Я\-]+){1,3})',
        r'Title:\s*(?:ER|NPR|ReR)?[^()]*\((?:[^)]+)\)\s*\(([^)]+)\)',
        r'\(([A-Z][a-z]+\s+[A-Z][a-z]+)\)\s*Dismount',
        r'\(([A-Z][a-z]+\s+[A-Z][a-z]+)\)\s*Create'
    ]
    
    for pattern in name_patterns:
        m = re.search(pattern, full_text if 'Title:' in pattern else (subject + " " + full_text), re.IGNORECASE)
        if m:
            potential_name = m.group(1).strip()
            potential_name = re.split(r'SLA|Location|Dismissal|Date|requires|has|is|Floor|Room| \n|\t|\n', potential_name)[0].strip()
            if len(potential_name.split()) > 4:
                potential_name = " ".join(potential_name.split()[:3])
            if len(potential_name.split()) >= 2:
                name = potential_name
                break
    
    req_date = "Unknown"
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

    if not city:
        return None

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
    }

def parse_ticket(subject, body, country_tag="", is_middle_east=False):
    """Парсит письмо, проверяет фильтры и возвращает текст уведомления."""
    clean_body = cleanup_html(body)
    
    if not is_middle_east:
        allowed_countries = ["kazakhstan", "uzbekistan", "kyrgyzstan", "казахстан", "узбекистан", "кыргызстан"]
        loc_search = re.search(r'Location:\s*(.*?)(?:\s{2,}|Title:|Alert:|IP:|Status:|\n|$)', clean_body, re.IGNORECASE)
        found_location = loc_search.group(1).lower() if loc_search else ""
        
        mention_key = None
        if found_location:
            for city in ["almaty", "astana", "karaganda", "tashkent", "bishkek"]:
                if city in found_location:
                    mention_key = city
                    break
            
            if not mention_key:
                for country in allowed_countries:
                    if country in found_location:
                        if "казах" in country or "kazakh" in country: mention_key = "kazakhstan"
                        elif "узбек" in country or "uzbek" in country: mention_key = "uzbekistan"
                        elif "кыргыз" in country or "kyrgyz" in country: mention_key = "kyrgyzstan"
                        else: mention_key = country
                        break
            
            if not mention_key:
                return 'IGNORE'
        else:
            if country_tag:
                mapping = {"[KZ]": "kazakhstan", "[UZ]": "uzbekistan", "[KG]": "kyrgyzstan"}
                mention_key = mapping.get(country_tag)
            if not mention_key:
                return 'IGNORE'
    else:
        mention_key = None

    if "Alert:" in clean_body and "Status:" in clean_body:
        return 'IGNORE'

    if any(kw in subject for kw in ["WO00", "Work Order", "SCTASK"]):
        return 'IGNORE'
    
    if "ZABBIX" in subject.upper() or "Auto_EPM" in subject:
        return 'IGNORE'
    
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
        return 'IGNORE'
    
    if re.search(r'Status:\s*(Resolved|Closed|Completed)', clean_body, re.IGNORECASE):
        return 'IGNORE'
        
    is_sla_alert = False
    lower_subject = subject.lower()
    if "sla" in lower_subject and ("reached" in lower_subject or "%" in lower_subject or "violation" in lower_subject):
        is_sla_alert = True
    elif "sla" in lower_subject and "has reached" in clean_body.lower():
        is_sla_alert = True
        
    ticket_match = re.search(r'(INC\d+|RITM\d+)', subject)
    
    if not ticket_match and not is_sla_alert:
        return 'IGNORE'
        
    ticket_id = ticket_match.group(1) if ticket_match else "SLA Alert"
    
    link_match = re.search(fr'href=["\'](https?://[^"\']+)["\'][^>]*>(?:<[^>]+>)*\s*{ticket_id}', str(body), re.IGNORECASE)
    ticket_url = link_match.group(1).replace('&amp;', '&') if link_match else ""
    
    clean_body = cleanup_html(body)
    
    stop_words = r'(?:Service\s*:|Status\s*:|Description\s*:|Priority\s*:|Service Recipient\s*:|SLA Target Date\s*:|Location\s*:|Request Details|Comments:|Ref:|This is an automatically|$)'
    
    title_match = re.search(r'Title:\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    title = title_match.group(1).strip() if title_match else "Нет заголовка"
    
    desc_match = re.search(r'(?:Description:|Comments:?)\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    desc = desc_match.group(1).strip() if desc_match else ""
    
    priority_match = re.search(r'Priority:\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    priority = priority_match.group(1).strip() if priority_match else ""
    
    loc_match = re.search(r'Location:\s*(.*?)' + stop_words, clean_body, re.IGNORECASE)
    location = loc_match.group(1).strip() if loc_match else ""
    
    current_mention = mention_key
    if location:
        for city in ["almaty", "astana", "karaganda", "tashkent", "bishkek"]:
            if city in location.lower():
                current_mention = city
                break

    if len(title) > 80: title = title[:80] + "..."
    if len(location) > 80: location = location[:80] + "..."
    if len(priority) > 30: priority = priority[:30] + "..."
    
    if ticket_id.startswith("INC"):
        ticket_type = "🔴 Инцидент"
    elif ticket_id.startswith("RITM"):
        ticket_type = "🟢 RITM Запрос"
    else:
        ticket_type = "📝 Тикет"
        
    if is_sla_alert:
        ticket_type = "⏰ **ВНИМАНИЕ: SLA Alert**"
    
    if is_middle_east:
        me_tag = "[ME]"
        low_loc = location.lower()
        if "uae" in low_loc or "dubai" in low_loc or "abu dhabi" in low_loc: me_tag = "[UAE]"
        elif "qatar" in low_loc or "doha" in low_loc: me_tag = "[QA]"
        elif "saudi" in low_loc or "riyadh" in low_loc: me_tag = "[SA]"
        elif "kuwait" in low_loc: me_tag = "[KW]"
        elif "oman" in low_loc or "muscat" in low_loc: me_tag = "[OM]"
        elif "jordan" in low_loc or "amman" in low_loc: me_tag = "[JO]"
        tag_str = f" {me_tag}"
    else:
        tag_str = f" {country_tag}" if country_tag else (f" [{mention_key.upper()}]" if mention_key else "")
    
    if ticket_url:
        msg = f"{ticket_type}{tag_str}: [**{ticket_id}**]({ticket_url})\n\n"
    else:
        msg = f"{ticket_type}{tag_str}: **{ticket_id}**\n\n"
        
    msg += f"**Тема:** {title}\n"
    
    is_critical = False
    if is_sla_alert:
        is_critical = True
        
    if priority:
        msg += f"**Приоритет:** {priority}\n"
        if "1" in priority or "critical" in priority.lower():
            is_critical = True
            
    if location:
        msg += f"**Локация:** {location}\n"
    if desc:
        short_desc = desc[:250] + "..." if len(desc) > 250 else desc
        msg += f"\n**Описание:**\n*{short_desc}*"
        
    return msg, is_critical, current_mention
