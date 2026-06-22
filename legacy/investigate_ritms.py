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
        print("Not authenticated. Please check your tokens/credentials.")
        return

    ritms = ["RITM0002190313", "RITM0002191082", "RITM0002191081"]
    
    # Target email might be a comma-separated list
    emails = [e.strip() for e in TARGET_EMAIL.split(',')]
    
    for email in emails:
        print(f"\n===== Checking Mailbox: {email} =====")
        try:
            mailbox = account.mailbox(resource=email)
            inbox = mailbox.inbox_folder()
        except Exception as e:
            print(f"Error accessing inbox for {email}: {e}")
            continue

        # We'll search for each RITM
        for ritm in ritms:
            print(f"\n--- Searching for {ritm} ---")
            # Correct O365 query syntax
            query = inbox.new_query().search(ritm)
            messages = inbox.get_messages(query=query, limit=5)
            
            found = False
            for msg in messages:
                found = True
                subject = msg.subject
                body = msg.body
                
                print(f"Subject: {subject}")
                
                # 2. Print whether 'resolved' or 'closed' is in the subject
                lower_subject = subject.lower()
                res_cl = [kw for kw in ['resolved', 'closed'] if kw in lower_subject]
                print(f"Contains 'resolved' or 'closed': {res_cl if res_cl else 'None'}")
                
                # 3. Check if the body contains 'Student' or 'Trainee' in the Employee title field
                # Look for common patterns from extract_report_data
                title_match = re.search(r'(Title|Employee title|Employment type):.*(Student|Trainee)', body, re.IGNORECASE)
                if title_match:
                    print(f"Employee title field check: Found '{title_match.group(2)}' in '{title_match.group(0)}'")
                else:
                    print("Employee title field check: 'Student' or 'Trainee' NOT found in standard fields.")

                # 4. Report why bot.py's extract_report_data() would skip them
                # Re-implementing logic from bot.py
                skip_reasons = []
                
                ignore_keywords = ['assigned', 'sla', 'pending', 'suspended', 'created', 'assigned to']
                if any(kw in lower_subject for kw in ignore_keywords):
                    skip_reasons.append(f"Ignored keyword in subject: {[kw for kw in ignore_keywords if kw in lower_subject]}")

                is_final = any(kw in lower_subject for kw in ['resolved', 'closed', 'exit task', 'completed'])
                if not is_final:
                    skip_reasons.append("Not a final state (resolved/closed/exit task/completed)")

                is_npr = 'NPR' in body and 'Prepare workstation' in body
                is_er = 'ER' in body and ('Dismount' in body or 'Exit' in body)
                is_trans = 'Transformation from Trainee' in body
                
                if not (is_npr or is_er or is_trans):
                    skip_reasons.append("Type check failed (Not NPR with 'Prepare workstation', not ER with 'Dismount'/'Exit', and not 'Transformation from Trainee')")

                if not is_trans:
                    if title_match:
                        skip_reasons.append(f"Excluded as Student/Trainee: {title_match.group(0)}")

                if not skip_reasons:
                    # Check for Name extraction
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
                    print("Why bot.py would skip: WOULD NOT SKIP (matches criteria)")
            
            if not found:
                print(f"No emails found for {ritm}")

    print("\n--- Investigation Complete ---")

if __name__ == "__main__":
    investigate_emails()


            if skip_reasons:
                print(f"Why bot.py would skip: {'; '.join(skip_reasons)}")
            else:
                print("Why bot.py would skip: WOULD NOT SKIP (matches criteria)")
                
        if not found:
            print(f"No emails found for {ritm}")

if __name__ == "__main__":
    investigate_emails()
