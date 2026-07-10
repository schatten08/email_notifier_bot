import os
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
TENANT_ID = os.getenv('TENANT_ID')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_graph_access_token():
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    payload = {
        'client_id': CLIENT_ID,
        'scope': 'https://graph.microsoft.com/.default',
        'client_secret': CLIENT_SECRET,
        'grant_type': 'client_credentials'
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status()
        return response.json().get('access_token')
    except Exception as e:
        print(f"Token Error: {e}")
        return None

def get_user_details(email_or_upn):
    token = get_graph_access_token()
    if not token:
        return None
        
    headers = {'Authorization': f'Bearer {token}'}
    base_url = f"https://graph.microsoft.com/v1.0/users/{email_or_upn}"
    
    print(f"Checking user: {email_or_upn}")
    try:
        profile_res = requests.get(f"{base_url}?$select=displayName,jobTitle,department", headers=headers, timeout=5)
        print(f"Profile Status: {profile_res.status_code}")
        if profile_res.status_code != 200:
            print(f"Profile Error: {profile_res.text}")
            return None
        profile = profile_res.json()
        
        details = {
            "name": profile.get("displayName"),
            "job": profile.get("jobTitle"),
            "dept": profile.get("department"),
            "manager": "Not found"
        }
        
        manager_res = requests.get(f"{base_url}/manager?$select=displayName", headers=headers, timeout=5)
        print(f"Manager Status: {manager_res.status_code}")
        if manager_res.status_code == 200:
            details["manager"] = manager_res.json().get("displayName", "Not found")
        else:
            print(f"Manager Error: {manager_res.text}")
            
        return details
    except Exception as e:
        print(f"Request Error: {e}")
        return None

if __name__ == "__main__":
    # Test with a known email from logs
    test_emails = ["rustam_baratov@epam.com", "pavel_vasilev2@epam.com"]
    for email in test_emails:
        print("-" * 30)
        res = get_user_details(email)
        print(f"Result: {res}")
