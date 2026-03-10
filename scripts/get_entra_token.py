#!/usr/bin/env python3
"""
Get an Entra (Azure AD) token using ROPC (Resource Owner Password Credentials) flow.

This is the simplest flow for testing, but has limitations:
- Doesn't work with MFA-enabled accounts
- Not recommended for production use
- Requires admin consent for some permissions

For production or MFA-enabled accounts, use get_entra_token_device_code.py instead.

Environment variables can be set in a .env file in the scripts directory.
See .env.example for the required variables.
"""
import requests
import os
import sys

# Load environment variables from .env file if present
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

# ==============================================================================
# CONFIGURATION - Fill these in with your values
# ==============================================================================

# From your Azure AD app registration:
TENANT_ID = os.environ.get('ENTRA_TENANT_ID', 'your-tenant-id')
CLIENT_ID = os.environ.get('ENTRA_CLIENT_ID', 'your-app-client-id')
CLIENT_SECRET = os.environ.get('ENTRA_CLIENT_SECRET', 'your-client-secret')

# Your Therefore/Entra credentials:
USERNAME = os.environ.get('ENTRA_USERNAME', 'your-username@domain.com')
PASSWORD = os.environ.get('ENTRA_PASSWORD', 'your-password')

# Output file for the token
TOKEN_FILE = os.environ.get('TOKEN_FILE', '.entra_token.txt')


def get_token_ropc():
    """
    Get an access token using Resource Owner Password Credentials flow.
    
    Returns:
        The access token string
    """
    token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    
    data = {
        'grant_type': 'password',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'username': USERNAME,
        'password': PASSWORD,
        'scope': f'api://{CLIENT_ID}/.default', 
    }
    
    print(f"Requesting token from: {token_url}")
    print(f"User: {USERNAME}")
    
    response = requests.post(token_url, data=data)
    
    if response.status_code != 200:
        print(f"\n✗ Error: {response.status_code}")
        print(f"Response: {response.text}")
        
        error_data = response.json()
        error_code = error_data.get('error')
        error_desc = error_data.get('error_description', '')
        
        if 'AADSTS50076' in error_desc or 'multi-factor authentication' in error_desc.lower():
            print("\n⚠ This account requires MFA. Use get_entra_token_device_code.py instead!")
        elif 'AADSTS7000218' in error_desc:
            print("\n⚠ Client secret issue. Check your CLIENT_SECRET.")
        elif 'AADSTS50034' in error_desc or 'user does not exist' in error_desc.lower():
            print("\n⚠ User not found. Check your USERNAME.")
        elif 'AADSTS50126' in error_desc or 'invalid credentials' in error_desc.lower():
            print("\n⚠ Invalid credentials. Check your PASSWORD.")
            
        response.raise_for_status()
    
    token_data = response.json()
    access_token = token_data['access_token']
    
    print(f"\n✓ Got access token!")
    print(f"  Length: {len(access_token)} chars")
    print(f"  Token type: {token_data.get('token_type')}")
    print(f"  Expires in: {token_data.get('expires_in')} seconds")
    
    if 'scope' in token_data:
        print(f"  Scopes: {token_data.get('scope')}")
    
    # Save for later
    with open(TOKEN_FILE, 'w') as f:
        f.write(access_token)
    print(f"\n✓ Token saved to: {TOKEN_FILE}")
    
    return access_token


def main():
    """Main entry point."""
    # Check configuration
    if TENANT_ID == 'your-tenant-id':
        print("ERROR: Please set ENTRA_TENANT_ID environment variable or edit the script.")
        print("\nTo get your Tenant ID:")
        print("1. Go to Azure Portal (portal.azure.com)")
        print("2. Azure Active Directory → Overview")
        print("3. Copy 'Tenant ID'")
        sys.exit(1)
    
    if CLIENT_ID == 'your-app-client-id':
        print("ERROR: Please set ENTRA_CLIENT_ID environment variable or edit the script.")
        print("\nTo get your Client ID:")
        print("1. Go to Azure Portal → Azure Active Directory → App registrations")
        print("2. Select your app")
        print("3. Copy 'Application (client) ID'")
        sys.exit(1)
    
    if CLIENT_SECRET == 'your-client-secret':
        print("ERROR: Please set ENTRA_CLIENT_SECRET environment variable or edit the script.")
        print("\nTo create a client secret:")
        print("1. Go to your app registration in Azure Portal")
        print("2. Certificates & secrets → New client secret")
        print("3. Copy the 'Value' (not the Secret ID)")
        sys.exit(1)
    
    if USERNAME == 'your-username@domain.com':
        print("ERROR: Please set ENTRA_USERNAME environment variable or edit the script.")
        sys.exit(1)
    
    if PASSWORD == 'your-password':
        print("ERROR: Please set ENTRA_PASSWORD environment variable or edit the script.")
        sys.exit(1)
    
    # Get the token
    try:
        token = get_token_ropc()
        print(f"\n{'='*60}")
        print("Token preview (first 100 chars):")
        print(f"{token[:100]}...")
        print(f"{'='*60}")
        return 0
    except Exception as e:
        print(f"\n✗ Failed to get token: {e}")
        return 1


if __name__ == '__main__':
    sys.exit(main())
