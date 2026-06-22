import os
from O365 import Account
from dotenv import load_dotenv
import re

load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
TENANT_ID = os.getenv('TENANT_ID')
TARGET_EMAIL = os.getenv('TARGET_EMAIL')

def investigate_emails():
    credentials = (CLIENT_ID, CLIENT_SECRET)
    account = Account(credentials, tenant_id=TENANT_ID)
    
    if not account.is_authenticated:
        print("Not authenticated.")
        return

    ritms = ["RITM0002190313", "RITM0002191082", "RITM0002191081"]
    
    # Try searching the authenticated user's own mailbox first
    mailbox = account.mailbox()
    print(f"\n===== Checking Authenticated User Mailbox =====")
    inbox = mailbox.inbox_folder()
    
    for ritm in ritms:
        print(f"\n--- Searching for {ritm} ---")
        try:
            query = inbox.new_query().search(ritm)
            messages = inbox.get_messages(query=query, limit=5)
            
            found = False
            for msg in messages:
                found = True
                subject = msg.subject
                body = msg.body
                
                print(f"Subject: {subject}")
                
                lower_subject = subject.lower()
                res_cl = [kw for kw in ['resolved', 'closed'] if kw in lower_subject]
                print(f"Contains 'resolved' or 'closed': {res_cl if res_cl else 'None'}")
                
                title_match = re.search(r'(Title|Employee title|Employment type):.*(Student|Trainee)', body, re.IGNORECASE)
                if title_match:
                    print(f"Employee title field check: Found '{title_match.group(2)}' in '{title_match.group(0).strip()}'")
                else:
                    print("Employee title field check: 'Student' or 'Trainee' NOT found in standard fields.")

                # Logic from bot.py
                skip_reasons = []
                
                ignore_keywords = ['assigned', 'sla', 'pending', 'suspended', 'created', 'assigned to']
                if any(kw in lower_subject for kw in ignore_keywords):
                    skip_reasons.append(f"Ignored keyword in subject: {[kw for kw in ignore_keywords if kw in lower_subject]}")

                is_final = any(kw in lower_subject for kw in ['resolved', 'closed', 'exit task', 'completed'])
                if not is_final:
                    skip_reasons.append("Not a final state")

                is_npr = 'NPR' in body and 'Prepare workstation' in body
                is_er = 'ER' in body and ('Dismount' in body or 'Exit' in body)
                is_trans = 'Transformation from Trainee' in body
                
                if not (is_npr or is_er or is_trans):
                    skip_reasons.append("Type check failed")

                if not is_trans:
                    if title_match:
                        skip_reasons.append(f"Excluded as Student/Trainee")

                if not skip_reasons:
                    name = None
                    if is_trans:
                        m = re.search(r'Trainee:\s*(.*?)(?:,| effective)', body, re.IGNORECASE)
                        if m: name = m.group(1).strip()
                    else:
                        m = re.search(r'Title:\s*([A-Z][a-z]+ [A-Z][a-z]+)', body)
                        if not m:
                            m = re.search(r'(?:NPR|ER)\s*\([^)]+\)\s*\(([^)]+)\)', body)
                        if m: name = m.group(1).strip()
                    
                    if not name:
                        skip_reasons.append("Name extraction failed")

                if skip_reasons:
                    print(f"Why bot.py would skip: {'; '.join(skip_reasons)}")
                else:
                    print("Why bot.py would skip: WOULD NOT SKIP")
            
            if not found:
                print(f"No emails found for {ritm}")
        except Exception as e:
            print(f"Error searching {ritm}: {e}")

    # Then try the shared ones
    emails = [e.strip() for e in (TARGET_EMAIL or "").split(',')]
    for email in emails:
        print(f"\n===== Checking Shared Mailbox: {email} =====")
        try:
            shared_mailbox = account.mailbox(resource=email)
            shared_inbox = shared_mailbox.inbox_folder()
            for ritm in ritms:
                print(f"\n--- Searching for {ritm} ---")
                query = shared_inbox.new_query().search(ritm)
                messages = shared_inbox.get_messages(query=query, limit=5)
                found = False
                for msg in messages:
                    found = True
                    # Re-use logic or print
                    print(f"Subject: {msg.subject}")
                if not found:
                    print(f"No emails found for {ritm} in {email}")
        except Exception as e:
            print(f"Error accessing {email}: {e}")

if __name__ == "__main__":
    investigate_emails()
