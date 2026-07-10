import re

MIDDLE_EAST_EMAILS = ['uae@epam.com', 'qatar@epam.com', 'saudiarabia@epam.com', 'saudi_arabia@epam.com', 'oman@epam.com', 'jordan@epam.com']

def check_me(sender, all_recipients, subject, clean_msg_body):
    is_middle_east = False
    if sender.lower() in MIDDLE_EAST_EMAILS:
        is_middle_east = True
    else:
        for addr in all_recipients:
            if any(me_email in addr.lower() for me_email in MIDDLE_EAST_EMAILS):
                is_middle_east = True
                break
    return is_middle_east

# Test Case from screenshot
sender = "wft_support@epam.com"
all_recipients = ["wftitservicesuae@epam.com"] # Predicted address for "WFT IT Services UAE"
subject = "Requested Item (RITM) RITM0002227880 has been created and assigned to your group"
body = "Location: Asia - Central and West/UAE/Dubai/Dubai/Aurora Tower Office 2202-2207"

print(f"Old logic result: {check_me(sender, all_recipients, subject, body)}")

def check_me_new(sender, all_recipients, subject, clean_msg_body):
    is_middle_east = False
    me_keywords = ['uae', 'qatar', 'saudi', 'oman', 'jordan', 'dubai', 'abu dhabi']
    
    if any(me_email in sender.lower() for me_email in MIDDLE_EAST_EMAILS):
        is_middle_east = True
    
    if not is_middle_east:
        for addr in all_recipients:
            if any(me_email in addr.lower() for me_email in MIDDLE_EAST_EMAILS) or \
               any(kw in addr.lower() for kw in me_keywords):
                is_middle_east = True
                break
    
    if not is_middle_east:
        if any(kw in subject.lower() for kw in me_keywords) or \
           any(kw in clean_msg_body.lower() for kw in me_keywords):
            if re.search(r'Location:.*?(?:' + '|'.join(me_keywords) + ')', clean_msg_body, re.IGNORECASE):
                is_middle_east = True
    return is_middle_east

print(f"New logic result: {check_me_new(sender, all_recipients, subject, body)}")
