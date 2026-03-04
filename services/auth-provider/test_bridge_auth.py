#!/usr/bin/env python3
import json
import os
import sys
import urllib.request
import urllib.error

# Add current dir to path to import auth.py for manual checks
sys.path.append(os.path.dirname(__file__))
from auth import decode_therefore_jwt

def test_bridge_auth():
    """
    Test script to verify the end-to-end bridge auth flow.
    Requires the auth provider server to be running (default port 8001).
    """
    auth_provider_url = "http://localhost:8001"
    
    # Load settings from tenants.json to mimic a real environment
    tenants_path = os.path.join(os.path.dirname(__file__), "tenants.json")
    if not os.path.exists(tenants_path):
        print(f"✗ ERROR: {tenants_path} not found. Copy tenants.json.example and fill it out first.")
        return

    with open(tenants_path, "r") as f:
        tenants = json.load(f)
    
    tenant_key = next(iter(tenants.keys()))
    config = tenants[tenant_key]
    
    print(f"--- Testing Bridge Auth for tenant '{tenant_key}' ---")
    
    # 1. Request a token from the Auth Provider
    issue_url = f"{auth_provider_url}/issue-token"
    payload = json.dumps({
        "tenant": tenant_key,
        "user_hint": config["user_mapping"]
    }).encode("utf-8")
    
    req = urllib.request.Request(issue_url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Bridge-API-Key", config["bridge_api_key"])
    
    print(f"1. Requesting JWT from {issue_url}...")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            resp = json.loads(r.read().decode("utf-8"))
            access_token = resp.get("access_token")
            print(f"   ✓ Got JWT ({len(access_token)} chars)")
    except urllib.error.URLError as e:
        print(f"   ✗ FAILED to connect to Auth Provider: {e}")
        print("   (Ensure 'python3 services/auth-provider/main.py' is running)")
        return
    except Exception as e:
        print(f"   ✗ FAILED to get token: {e}")
        return

    # 2. Verify the token structure (Local verification)
    print("2. Verifying JWT structure locally...")
    try:
        decoded = decode_therefore_jwt(
            access_token, 
            config["shared_secret"], 
            config["customer_id"], 
            config["issuer"]
        )
        print("   ✓ JWT signature and claims verified!")
        print(f"   ✓ Claim 'windowsaccountname': {decoded.get('http://schemas.microsoft.com/ws/2008/06/identity/claims/windowsaccountname')}")
        print(f"   ✓ Audience (Customer ID): {decoded.get('aud')}")
    except Exception as e:
        print(f"   ✗ FAILED to verify JWT: {e}")
        return

    print("
SUCCESS: Bridge Auth Provider and configuration are valid!")
    print(f"To use this in MCP, set:")
    print(f"  THEREFORE_{tenant_key.upper()}_AUTH_METHOD=S2S")
    print(f"  THEREFORE_{tenant_key.upper()}_AUTH_PROVIDER_URL={auth_provider_url}")
    print(f"  THEREFORE_{tenant_key.upper()}_BRIDGE_API_KEY={config['bridge_api_key']}")
    print(f"  THEREFORE_{tenant_key.upper()}_USER_MAPPING={config['user_mapping']}")

if __name__ == "__main__":
    test_bridge_auth()
