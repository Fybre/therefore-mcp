#!/usr/bin/env python3
"""
Test the Therefore GetJWTToken endpoint.

This script:
1. Loads an ADFS/Entra token (from .entra_token.txt or provided)
2. Uses it as a Bearer token to call GetJWTToken
3. Exchanges it for a Therefore JWT token
4. Tests the Therefore JWT token with an API call

Usage:
    python3 test_therefore_get_jwt_token.py --token "YOUR_ENTRA_TOKEN"
"""
import os
import sys
import argparse
import json
import urllib.request
import urllib.error
import ssl

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

# Add parent directory to path to import src
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ==============================================================================
# CONFIGURATION
# ==============================================================================

THEREFORE_BASE_URL = os.environ.get(
    'THEREFORE_BASE_URL',
    'https://yourtenant.thereforeonline.com/theservice/v0001/restun'
)

ENTRA_TOKEN_FILE = os.environ.get('ENTRA_TOKEN_FILE', '.entra_token.txt')
THEREFORE_JWT_FILE = os.environ.get('THEREFORE_JWT_FILE', '.therefore_jwt.txt')

def call_api(url, payload, headers):
    """Helper to call Therefore API."""
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    
    ctx = ssl.create_default_context()
    # For testing, you might need to disable certificate verification if using self-signed
    # ctx.check_hostname = False
    # ctx.verify_mode = ssl.CERT_NONE
    
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(f"HTTP Error {e.code}: {e.read().decode('utf-8')}")
        raise
    except Exception as e:
        print(f"Error calling API: {e}")
        raise

def get_tenant_name(url):
    """Extract tenant name from URL."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.endswith('.thereforeonline.com'):
        subdomain = parsed.hostname.split('.')[0]
        if subdomain and subdomain != 'www':
            return subdomain
    return None

def main():
    parser = argparse.ArgumentParser(description='Test Therefore GetJWTToken exchange')
    parser.add_argument('--token', help='Entra/Azure AD access token')
    parser.add_argument('--url', help='Therefore REST API base URL', default=THEREFORE_BASE_URL)
    args = parser.parse_args()

    base_url = args.url.rstrip('/')
    entra_token = args.token

    if not entra_token:
        if os.path.exists(ENTRA_TOKEN_FILE):
            with open(ENTRA_TOKEN_FILE, 'r') as f:
                entra_token = f.read().strip()
            print(f"✓ Loaded Entra token from {ENTRA_TOKEN_FILE}")
        else:
            print(f"✗ No Entra token provided and {ENTRA_TOKEN_FILE} not found.")
            print("Please provide a token via --token or create the file.")
            return 1

    tenant_name = get_tenant_name(base_url)
    
    # Step 1: Exchange Entra token for Therefore JWT
    print(f"\n{'='*60}")
    print("STEP 1: Exchange Entra token for Therefore JWT")
    print(f"{'='*60}")
    print(f"Endpoint: {base_url}/GetJWTToken")
    
    headers = {
        'Content-Type': 'application/json; charset=utf-8',
        'Authorization': f'Bearer {entra_token}'
    }
    if tenant_name:
        headers['TenantName'] = tenant_name
        print(f"TenantName: {tenant_name}")

    try:
        # GetJWTTokenParams is empty {}
        result = call_api(f"{base_url}/GetJWTToken", {}, headers)
        
        therefore_jwt = result.get('JWTToken')
        expires = result.get('ExpiresAtISO8601') or result.get('ExpiresAt')
        
        if therefore_jwt:
            print(f"✓ Success! Received Therefore JWT.")
            print(f"  Token: {therefore_jwt[:60]}...")
            print(f"  Expires: {expires}")
            
            with open(THEREFORE_JWT_FILE, 'w') as f:
                f.write(therefore_jwt)
            print(f"  Saved to: {THEREFORE_JWT_FILE}")
        else:
            print("✗ Failed: No JWTToken in response.")
            print(f"  Response: {result}")
            return 1
            
    except Exception as e:
        print(f"✗ Failed to exchange token: {e}")
        return 1

    # Step 2: Test the Therefore JWT
    print(f"\n{'='*60}")
    print("STEP 2: Test Therefore JWT with GetConnectedUser")
    print(f"{'='*60}")
    
    test_headers = {
        'Content-Type': 'application/json; charset=utf-8',
        'Authorization': f'Bearer {therefore_jwt}'
    }
    if tenant_name:
        test_headers['TenantName'] = tenant_name

    try:
        user_info = call_api(f"{base_url}/GetConnectedUser", {"Create": True}, test_headers)
        print("✓ Success! API call authenticated.")
        print(f"  User: {user_info.get('UserName')}")
        print(f"  UserNo: {user_info.get('UserNo')}")
        print(f"  Full response: {json.dumps(user_info, indent=2)}")
    except Exception as e:
        print(f"✗ API test failed: {e}")
        return 1

    print(f"\n{'='*60}")
    print("VERIFICATION COMPLETE")
    print(f"{'='*60}")
    return 0

if __name__ == '__main__':
    sys.exit(main())
