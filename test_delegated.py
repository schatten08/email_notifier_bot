import os
from O365 import Account
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
TENANT_ID = os.getenv('TENANT_ID')

def test_delegated_enrichment():
    credentials = (CLIENT_ID, CLIENT_SECRET)
    account = Account(credentials, tenant_id=TENANT_ID)
    
    if not account.is_authenticated:
        print("Not authenticated! check o365_token.txt")
        return

    # test email
    email = "rustam_baratov@epam.com"
    
    url = f"https://graph.microsoft.com/v1.0/users/{email}?$select=displayName,jobTitle,department"
    
    print(f"Testing Delegated Auth for: {email}")
    try:
        # O365 connection.get uses the authenticated session
        response = account.connection.get(url)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            print(f"Data: {response.json()}")
            
            # test manager
            mgr_url = f"https://graph.microsoft.com/v1.0/users/{email}/manager?$select=displayName"
            mgr_resp = account.connection.get(mgr_url)
            print(f"Manager Status: {mgr_resp.status_code}")
            if mgr_resp.status_code == 200:
                print(f"Manager Data: {mgr_resp.json()}")
        else:
            print(f"Error: {response.text}")
            
    except Exception as e:
        print(f"Request failed: {e}")

if __name__ == "__main__":
    test_delegated_enrichment()
