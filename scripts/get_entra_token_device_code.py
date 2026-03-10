#!/usr/bin/env python3
import requests
import time
import os
import sys

# Load environment variables
_script_dir = os.path.dirname(os.path.abspath(__file__))
_env_file = os.path.join(_script_dir, '.env')
if os.path.exists(_env_file):
    with open(_env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = value

TENANT_ID = os.environ.get('ENTRA_TENANT_ID', 'your-tenant-id')
CLIENT_ID = os.environ.get('ENTRA_CLIENT_ID', 'your-app-client-id')
CLIENT_SECRET = os.environ.get('ENTRA_CLIENT_SECRET', '')
TOKEN_FILE = os.environ.get('TOKEN_FILE', '.entra_token.txt')
SCOPES = os.environ.get('ENTRA_SCOPES', 'openid profile User.Read')

def get_token_device_code():
    # Step 1: Request device code
    device_code_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/devicecode"
    data = {
        'client_id': CLIENT_ID,
        'scope': SCOPES,
    }
    if CLIENT_SECRET:
        data['client_secret'] = CLIENT_SECRET

    print(f"Requesting device code for {CLIENT_ID}...")
    response = requests.post(device_code_url, data=data)
    if response.status_code != 200:
        print(f"✗ Failed to get device code: {response.status_code}")
        print(response.text)
        response.raise_for_status()
    
    device_code_data = response.json()
    print("\n" + "="*60)
    print("ACTION REQUIRED")
    print("="*60)
    print(f"1. Go to: {device_code_data['verification_uri']}")
    print(f"2. Enter code: {device_code_data['user_code']}")
    print("="*60 + "\n")
    
    # Step 2: Poll for token
    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    poll_data = {
        'grant_type': 'urn:ietf:params:oauth:grant-type:device_code',
        'client_id': CLIENT_ID,
        'device_code': device_code_data['device_code'],
    }
    if CLIENT_SECRET:
        poll_data['client_secret'] = CLIENT_SECRET

    max_wait = int(device_code_data['expires_in'])
    interval = int(device_code_data.get('interval', 5))
    elapsed = 0
    
    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        print(".", end='', flush=True)
        
        response = requests.post(token_url, data=poll_data)
        if response.status_code == 200:
            token_data = response.json()
            access_token = token_data['access_token']
            with open(TOKEN_FILE, 'w') as f:
                f.write(access_token)
            print(f"\n✓ Token saved to: {TOKEN_FILE}")
            return access_token
        
        err = response.json().get('error')
        if err != 'authorization_pending':
            print(f"\n✗ Error: {err}")
            print(response.text)
            raise Exception(f"Failed: {err}")
    
    raise Exception("Timed out")

if __name__ == '__main__':
    get_token_device_code()
