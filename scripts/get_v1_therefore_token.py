#!/usr/bin/env python3
"""
Get a v1 Azure AD access token specifically scoped to the Therefore app.
Uses the v1 device code endpoint with resource=7153cdac and no extra scopes,
which should yield scp=user_impersonation (the Therefore app's default delegated permission).
"""
import os, sys, json, time, urllib.request, urllib.parse, urllib.error, ssl

_script_dir = os.path.dirname(os.path.abspath(__file__))
_env_file = os.path.join(_script_dir, '.env')
if os.path.exists(_env_file):
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if k not in os.environ:
                os.environ[k] = v

TENANT_ID  = os.environ.get('ENTRA_TENANT_ID', '')
CLIENT_ID  = os.environ.get('ENTRA_CLIENT_ID', '7153cdac-b8fa-4a0c-aeef-dcaf9013ec5b')
RESOURCE   = '7153cdac-b8fa-4a0c-aeef-dcaf9013ec5b'  # Therefore's AAD app

CTX = ssl.create_default_context()

def post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body,
          headers={'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode()}")

dc_url    = f'https://login.microsoftonline.com/{TENANT_ID}/oauth2/devicecode'
token_url = f'https://login.microsoftonline.com/{TENANT_ID}/oauth2/token'

# Step 1 – request device code
# v1 endpoint uses 'resource' not 'scope'
dc = post_form(dc_url, {
    'client_id': CLIENT_ID,
    'resource':  RESOURCE,
    # No 'scope' = just user_impersonation for the resource
})
print()
print('=' * 60)
print('  ACTION REQUIRED')
print('=' * 60)
print(f"  1. Open: {dc['verification_url']}")
print(f"  2. Enter: {dc['user_code']}")
print('=' * 60)
print('  Waiting...')

# Step 2 – poll
interval  = int(dc.get('interval', 5))
max_wait  = int(dc.get('expires_in', 900))
elapsed   = 0
while elapsed < max_wait:
    time.sleep(interval); elapsed += interval
    print('.', end='', flush=True)
    try:
        tok = post_form(token_url, {
            'grant_type':  'device_code',
            'client_id':   CLIENT_ID,
            'code':         dc['device_code'],
            'resource':     RESOURCE,
        })
        access_token = tok.get('access_token')
        if access_token:
            out = os.path.join(_script_dir, '.entra_v1_token.txt')
            with open(out, 'w') as f:
                f.write(access_token)
            print(f'\n✓ Saved to {out}')
            print(f'  Length: {len(access_token)} chars')
            
            import base64, datetime
            p = access_token.split('.')
            payload = json.loads(base64.urlsafe_b64decode(p[1] + '=='))
            print(f'  aud : {payload.get("aud")}')
            print(f'  scp : {payload.get("scp")}')
            print(f'  upn : {payload.get("upn")}')
            print(f'  ver : {payload.get("ver")}')
            sys.exit(0)
        err = tok.get('error')
        if err != 'authorization_pending':
            print(f'\n✗ {err}: {tok.get("error_description","")}')
            sys.exit(1)
    except RuntimeError as e:
        if 'authorization_pending' in str(e): continue
        print(f'\n✗ {e}')
        sys.exit(1)

print('\n✗ Timed out')
sys.exit(1)
