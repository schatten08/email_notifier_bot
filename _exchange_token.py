import os
import time
import json
import requests

def load_env(path):
    env = {}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    return env

env = load_env('.env')
CLIENT_ID = env['CLIENT_ID']
CLIENT_SECRET = env['CLIENT_SECRET']
TENANT_ID = env['TENANT_ID']

with open('auth_code.txt', 'r') as f:
    code = f.read().strip()

redirect_uri = 'https://login.microsoftonline.com/common/oauth2/nativeclient'
scope = 'https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/User.Read offline_access'

token_url = f'https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token'
data = {
    'grant_type': 'authorization_code',
    'code': code,
    'redirect_uri': redirect_uri,
    'client_id': CLIENT_ID,
    'client_secret': CLIENT_SECRET,
    'scope': scope,
}

resp = requests.post(token_url, data=data)
print('HTTP status:', resp.status_code)
result = resp.json()

if resp.status_code != 200:
    print('ERROR RESPONSE:')
    print(json.dumps(result, indent=2))
else:
    now = time.time()
    token = {
        'token_type': result.get('token_type', 'Bearer'),
        'scope': result.get('scope', scope),
        'expires_in': result.get('expires_in'),
        'ext_expires_in': result.get('ext_expires_in'),
        'access_token': result.get('access_token'),
        'refresh_token': result.get('refresh_token'),
        'id_token': result.get('id_token'),
        'expires_at': now + float(result.get('expires_in', 3600)),
    }
    out_path = 'data/o365_token.txt'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(token, f, indent=True)
    print('SUCCESS: token saved to', out_path)
    print('has refresh_token:', bool(token.get('refresh_token')))
    print('expires_in:', token.get('expires_in'))
