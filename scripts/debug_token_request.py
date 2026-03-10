#!/usr/bin/env python3
import requests
import os

# From .env
TENANT_ID = os.environ['ENTRA_TENANT_ID']
CLIENT_ID = os.environ['ENTRA_CLIENT_ID']
CLIENT_SECRET = os.environ['ENTRA_CLIENT_SECRET']

token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

# Try Client Credentials Grant (simplest way to verify client_id/client_secret)
data = {
    'grant_type': 'client_credentials',
    'client_id': CLIENT_ID,
    'client_secret': CLIENT_SECRET,
    'scope': 'https://graph.microsoft.com/.default'
}

print(f"Testing client credentials for {CLIENT_ID}...")
response = requests.post(token_url, data=data)

print(f"Status: {response.status_code}")
if response.status_code == 200:
    print("✓ Success! Client credentials are valid.")
else:
    print(f"✗ Failed: {response.text}")
