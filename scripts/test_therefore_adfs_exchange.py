#!/usr/bin/env python3
"""
Test the Therefore GetConnectionTokenFromADFSToken endpoint.

This script:
1. Loads an ADFS/Entra token (from .entra_token.txt or provided)
2. Exchanges it for a Therefore session token
3. Optionally tests the Therefore token with an API call

Usage:
    # Test the exchange only
    python3 test_therefore_adfs_exchange.py --step exchange
    
    # Test with existing Therefore token
    python3 test_therefore_adfs_exchange.py --step test
    
    # Do both (default)
    python3 test_therefore_adfs_exchange.py --step both

Environment variables can be set in a .env file in the scripts directory.
See .env.example for the required variables.
"""
import os
import sys
import argparse
import base64
import json

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

from src.therefore_client import ThereforeClient, ThereforeConfig

# ==============================================================================
# CONFIGURATION - Fill these in with your values
# ==============================================================================

# Your Therefore instance URL
THEREFORE_BASE_URL = os.environ.get(
    'THEREFORE_BASE_URL',
    'https://yourtenant.thereforeonline.com/theservice/v0001/restun'
)

# File containing the ADFS/Entra token
ENTRA_TOKEN_FILE = os.environ.get('ENTRA_TOKEN_FILE', '.entra_token.txt')

# File to save the Therefore token
THEREFORE_TOKEN_FILE = os.environ.get('THEREFORE_TOKEN_FILE', '.therefore_token.txt')

# Connect mode: 'NoLicenseMove', 'ConnectForSignOut', or 'MoveLicense'
CONNECT_MODE = os.environ.get('CONNECT_MODE', 'NoLicenseMove')


def test_exchange():
    """
    Exchange ADFS/Entra token for Therefore session token.
    """
    # Load the Entra token
    try:
        with open(ENTRA_TOKEN_FILE, 'r') as f:
            entra_token = f.read().strip()
        print(f"✓ Loaded Entra token from {ENTRA_TOKEN_FILE}")
        print(f"  Length: {len(entra_token)} chars")
    except FileNotFoundError:
        print(f"✗ No token file found: {ENTRA_TOKEN_FILE}")
        print("\nPlease run one of these first:")
        print("  python3 get_entra_token.py")
        print("  python3 get_entra_token_device_code.py")
        return None
    
    # Create client with dummy credentials
    # The GetConnectionTokenFromADFSToken endpoint ignores auth headers
    # and uses the ADFS token in the request body instead
    config = ThereforeConfig(
        base_url=THEREFORE_BASE_URL,
        auth_method='basic',
        username='adfs-exchange',  # Dummy - ignored by endpoint
        password='adfs-exchange',  # Dummy - ignored by endpoint
    )
    client = ThereforeClient(config)
    
    # Verify tenant name extraction
    headers = client._headers()
    tenant_name = headers.get('TenantName')
    if tenant_name:
        print(f"✓ TenantName header will be sent: {tenant_name}")
    else:
        print("⚠ Warning: TenantName header not set")
        print("  For Therefore Online, this is required!")
    
    print(f"\nCalling GetConnectionTokenFromADFSToken...")
    print(f"  Endpoint: {THEREFORE_BASE_URL}/GetConnectionTokenFromADFSToken")
    print(f"  ConnectMode: {CONNECT_MODE}")
    
    try:
        result = client.get_connection_token_from_adfs(
            security_token=entra_token,
            connect_mode=CONNECT_MODE
        )
        
        print(f"\n[Debug] Full response: {result}")
        
        therefore_token = result.get('Token')
        node_friendly = result.get('NodeFriendly')
        
        print(f"\n{'='*60}")
        print("Response received from Therefore")
        print(f"{'='*60}")
        
        if therefore_token:
            print(f"✓ Token: {therefore_token[:60]}...")
            print(f"  Length: {len(therefore_token)} chars")
        else:
            print("⚠ Warning: Token field is empty")
            print(f"  Response keys: {list(result.keys())}")
        
        # Save the Therefore token
        if therefore_token:
            with open(THEREFORE_TOKEN_FILE, 'w') as f:
                f.write(therefore_token)
            print(f"\n✓ Therefore token saved to: {THEREFORE_TOKEN_FILE}")
        
        return therefore_token
        
    except Exception as e:
        print(f"\n{'='*60}")
        print("✗ TOKEN EXCHANGE FAILED")
        print(f"{'='*60}")
        print(f"Error: {e}")
        
        # Try to provide helpful debugging
        error_str = str(e)
        if 'Tenant name is required' in error_str or '500' in error_str:
            print("\nPossible causes:")
            print("  - TenantName header not being sent (check URL)")
            print("  - Therefore instance doesn't have SSO configured")
            print("  - ADFS token format not accepted (might need SAML instead of JWT)")
        elif '401' in error_str or 'Unauthorized' in error_str:
            print("\nPossible causes:")
            print("  - ADFS token is invalid or expired")
            print("  - Therefore doesn't trust your Entra/ADFS")
            print("  - Wrong audience in the token")
        elif '400' in error_str or 'Bad Request' in error_str:
            print("\nPossible causes:")
            print("  - Malformed ADFS token")
            print("  - Missing required parameters")
        
        raise


def test_with_therefore_token():
    """
    Test making an API call with the Therefore token.
    """
    # Load the Therefore token
    try:
        with open(THEREFORE_TOKEN_FILE, 'r') as f:
            therefore_token = f.read().strip()
        print(f"✓ Loaded Therefore token from {THEREFORE_TOKEN_FILE}")
        print(f"  Length: {len(therefore_token)} chars")
    except FileNotFoundError:
        print(f"✗ No Therefore token found: {THEREFORE_TOKEN_FILE}")
        print("\nPlease run the exchange step first:")
        print("  python3 test_therefore_adfs_exchange.py --step exchange")
        return None
    
    print(f"\nTesting API call with Therefore token...")
    
    config = ThereforeConfig(
        base_url=THEREFORE_BASE_URL,
        auth_method='bearer',
        password=therefore_token,  # Bearer token
    )
    client = ThereforeClient(config)
    
    # Test: Get connected user info
    print("\nCalling GetConnectedUser...")
    try:
        user_info = client.get_connected_user(create=True)
        
        print(f"\n{'='*60}")
        print("✓ API CALL SUCCESSFUL!")
        print(f"{'='*60}")
        
        # Pretty print the user info
        print("\nConnected User Details:")
        if isinstance(user_info, dict):
            for key, value in user_info.items():
                if value:
                    print(f"  {key}: {value}")
        else:
            print(f"  Response: {user_info}")
        
        return user_info
        
    except Exception as e:
        print(f"\n{'='*60}")
        print("✗ API CALL FAILED")
        print(f"{'='*60}")
        print(f"Error: {e}")
        
        error_str = str(e)
        if '401' in error_str or 'Unauthorized' in error_str:
            print("\nThe token was rejected. Possible causes:")
            print("  - Token has expired")
            print("  - Token was not properly issued")
            print("  - User doesn't have Therefore license")
        
        raise


def decode_and_inspect_token():
    """
    Decode and show info about the Entra token (for debugging).
    """
    try:
        with open(ENTRA_TOKEN_FILE, 'r') as f:
            token = f.read().strip()
    except FileNotFoundError:
        print("No token to inspect.")
        return
    
    print(f"\n{'='*60}")
    print("Entra Token Inspection")
    print(f"{'='*60}")
    
    # Decode JWT
    parts = token.split('.')
    if len(parts) != 3:
        print("Not a valid JWT format")
        return
    
    # Add padding and decode payload
    payload = parts[1]
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += '=' * padding
    
    try:
        decoded = base64.urlsafe_b64decode(payload)
        claims = json.loads(decoded)
        
        print("\nToken Claims:")
        important_claims = [
            'upn', 'email', 'preferred_username', 'name',
            'oid', 'tid', 'iss', 'aud', 'iat', 'exp'
        ]
        
        for claim in important_claims:
            value = claims.get(claim)
            if value:
                if claim == 'exp' or claim == 'iat':
                    import datetime
                    dt = datetime.datetime.fromtimestamp(value, tz=datetime.timezone.utc)
                    print(f"  {claim}: {value} ({dt.isoformat()})")
                else:
                    print(f"  {claim}: {value}")
        
        # Check expiration
        import time
        now = int(time.time())
        exp = claims.get('exp', 0)
        if exp:
            if exp < now:
                print(f"\n⚠ WARNING: Token has EXPIRED!")
            else:
                mins_left = (exp - now) // 60
                print(f"\n✓ Token valid for {mins_left} more minutes")
        
    except Exception as e:
        print(f"Could not decode token: {e}")


def main():
    parser = argparse.ArgumentParser(
        description='Test Therefore ADFS token exchange'
    )
    parser.add_argument(
        '--step',
        choices=['exchange', 'test', 'both', 'inspect'],
        default='both',
        help='Which step to run: exchange (get Therefore token), '
             'test (test the token), both (default), or inspect (show Entra token info)'
    )
    parser.add_argument(
        '--token',
        help='Provide Entra token directly instead of reading from file'
    )
    
    args = parser.parse_args()
    
    # If token provided directly, save it to file
    if args.token:
        with open(ENTRA_TOKEN_FILE, 'w') as f:
            f.write(args.token)
        print(f"✓ Saved provided token to {ENTRA_TOKEN_FILE}")
    
    try:
        if args.step == 'inspect':
            decode_and_inspect_token()
            return 0
        
        if args.step in ('exchange', 'both'):
            print(f"{'='*60}")
            print("Step 1: Exchange Entra token for Therefore token")
            print(f"{'='*60}")
            token = test_exchange()
            if not token:
                return 1
        
        if args.step in ('test', 'both'):
            print(f"\n{'='*60}")
            print("Step 2: Test Therefore token with API call")
            print(f"{'='*60}")
            test_with_therefore_token()
        
        print(f"\n{'='*60}")
        print("All tests completed successfully!")
        print(f"{'='*60}")
        return 0
        
    except Exception as e:
        print(f"\n{'='*60}")
        print("Test failed")
        print(f"{'='*60}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
