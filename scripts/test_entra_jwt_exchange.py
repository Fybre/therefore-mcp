#!/usr/bin/env python3
"""
Test the full Azure AD → Therefore JWT exchange flow.

The correct flow (per Therefore support):
  1. Get an OIDC token from Azure AD scoped to Therefore's app registration
  2. Pass it as Bearer token to GetJWTToken
  3. Receive a longer-lived Therefore JWT
  4. Use the Therefore JWT for all subsequent API calls

Key finding: the Entra token MUST have aud = Therefore's Azure AD Client ID
(7153cdac-b8fa-4a0c-aeef-dcaf9013ec5b), not your own app registration's ID.

Usage:
    # Try ROPC first (no browser, but fails if MFA is enabled):
    python3 test_entra_jwt_exchange.py

    # Device code flow (works with MFA, opens browser):
    python3 test_entra_jwt_exchange.py --device-code

    # Use an existing token file:
    python3 test_entra_jwt_exchange.py --token-file .entra_token.txt

    # Skip token acquisition, go straight to exchange:
    python3 test_entra_jwt_exchange.py --token "eyJ0eXAi..."
"""
import os
import sys
import json
import time
import base64
import argparse
import datetime
import urllib.request
import urllib.parse
import urllib.error
import ssl

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
_script_dir = os.path.dirname(os.path.abspath(__file__))
_env_file = os.path.join(_script_dir, '.env')
if os.path.exists(_env_file):
    with open(_env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = value

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
THEREFORE_BASE_URL = os.environ.get(
    'THEREFORE_BASE_URL',
    'https://demo.thereforeonline.com/theservice/v0001/restun'
)

# These are discovered automatically from GetClientDiscoveryInfo.
# You only need to set them manually if auto-discovery fails.
ENTRA_TENANT_ID      = os.environ.get('ENTRA_TENANT_ID', '')
ENTRA_CLIENT_ID      = os.environ.get('ENTRA_CLIENT_ID', '')
ENTRA_CLIENT_SECRET  = os.environ.get('ENTRA_CLIENT_SECRET', '')
THEREFORE_AAD_CLIENT_ID = os.environ.get('ENTRA_THEREFORE_CLIENT_ID', '')

# Only needed for the ROPC flow (username/password — not recommended)
ENTRA_USERNAME = os.environ.get('ENTRA_USERNAME', '')
ENTRA_PASSWORD = os.environ.get('ENTRA_PASSWORD', '')

TOKEN_FILE   = os.path.join(_script_dir, '.entra_id_token.txt')
REFRESH_FILE = os.path.join(_script_dir, '.entra_refresh_token.txt')
JWT_FILE     = os.path.join(_script_dir, '.therefore_jwt.txt')

SSL_CTX = ssl.create_default_context()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def decode_jwt_payload(token):
    """Decode JWT payload without verification."""
    parts = token.split('.')
    if len(parts) != 3:
        return None
    padding = 4 - len(parts[1]) % 4
    if padding != 4:
        parts[1] += '=' * padding
    try:
        return json.loads(base64.urlsafe_b64decode(parts[1]))
    except Exception:
        return None


def show_token_info(token, label="Token"):
    payload = decode_jwt_payload(token)
    if not payload:
        print(f"  [{label}] Could not decode")
        return

    now = datetime.datetime.now(datetime.timezone.utc)
    exp_ts = payload.get('exp', 0)
    exp_dt = datetime.datetime.fromtimestamp(exp_ts, tz=datetime.timezone.utc)
    expired = exp_dt < now
    time_label = (f"EXPIRED {int((now - exp_dt).total_seconds() // 60)}m ago"
                  if expired else
                  f"valid for {int((exp_dt - now).total_seconds() // 60)}m")

    print(f"  [{label}]")
    print(f"    aud : {payload.get('aud', '?')}")
    print(f"    upn : {payload.get('upn') or payload.get('preferred_username', '?')}")
    print(f"    exp : {exp_dt.isoformat()} ({time_label})")
    return not expired


def http_post_json(url, payload, headers):
    """POST JSON, return parsed response."""
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"HTTP {e.code}: {body}")


def http_post_form(url, form_data):
    """POST URL-encoded form data, return parsed JSON response."""
    data = urllib.parse.urlencode(form_data).encode('utf-8')
    req = urllib.request.Request(
        url, data=data,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        raise RuntimeError(f"HTTP {e.code}: {body}")


def get_tenant_name(url):
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ''
    if host.endswith('.thereforeonline.com'):
        sub = host.split('.')[0]
        if sub and sub != 'www':
            return sub
    return None


# ---------------------------------------------------------------------------
# Therefore discovery
# ---------------------------------------------------------------------------

def extract_tenant_from_auth_url(auth_url):
    """
    Extract the AAD tenant from the AuthenticationUrl returned by GetClientDiscoveryInfo.
    e.g. https://login.microsoftonline.com/mytenant.onmicrosoft.com/oauth2/authorize
                                           ^^^^^^^^^^^^^^^^^^^^^^^^^^^
    Returns the tenant string (domain or GUID), or None if not parseable.
    """
    parsed = urllib.parse.urlparse(auth_url)
    parts = [p for p in parsed.path.split('/') if p]
    # path is typically /{tenant}/oauth2/authorize
    if parts:
        return parts[0]
    return None


def discover_client_settings(base_url):
    """
    Call GetClientDiscoveryInfo to get the AAD config Therefore expects.
    Returns (client_id, tenant, scopes, auth_url) or (None, None, None, None) on failure.

    Everything needed to authenticate is returned by this endpoint — no manual
    Azure AD configuration is required from the caller.
    """
    url = f"{base_url.rstrip('/')}/GetClientDiscoveryInfo"
    tenant_name = get_tenant_name(base_url)
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if tenant_name:
        headers['TenantName'] = tenant_name
    try:
        result = http_post_json(url, {}, headers)
        settings = result.get('ClientSettings', [])
        # Find Azure AD entry (FederationProvider=2)
        for s in settings:
            if s.get('FederationProvider') == 2:
                client_id = s.get('ClientId')
                scopes    = s.get('Scopes', ['openid'])
                auth_url  = s.get('AuthenticationUrl', '')
                tenant    = extract_tenant_from_auth_url(auth_url)
                print(f"  Discovered ClientId : {client_id}")
                print(f"  Discovered Tenant   : {tenant}")
                print(f"  Discovered Scopes   : {scopes}")
                return client_id, tenant, scopes, auth_url
        print("  Discovery: No Azure AD provider found in ClientSettings")
        return None, None, None, None
    except Exception as e:
        print(f"  Discovery failed: {e}")
        return None, None, None, None


# ---------------------------------------------------------------------------
# Token acquisition
# ---------------------------------------------------------------------------

def refresh_id_token(tenant, client_id):
    """
    Attempt silent refresh using a saved refresh token.

    The v1 token endpoint does return an id_token on refresh when scope=openid is
    included, BUT that id_token is unsigned (alg:none). Therefore requires RS256.
    RS256-signed id_tokens are only issued from the v1 authorization endpoint
    (browser implicit flow / device code), not from the token endpoint.

    So this function will always return None and fall through to device code.
    It is kept here to rotate the refresh token (keeping the token family alive)
    and to document why silent refresh cannot work with Therefore.

    To get a fresh id_token, use --device-code (interactive browser login).
    """
    if not os.path.exists(REFRESH_FILE):
        return None
    with open(REFRESH_FILE) as f:
        refresh_token = f.read().strip()
    if not refresh_token:
        return None

    print("\n[Token] Trying silent refresh...")
    # v1 endpoint with resource= and scope=openid — rotates the refresh token.
    # Note: v1 refresh id_tokens are unsigned (alg:none); Therefore requires RS256.
    # We check the alg and fall through to device code if unsigned.
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/token"
    try:
        data = http_post_form(token_url, {
            'grant_type':    'refresh_token',
            'client_id':     client_id,
            'refresh_token': refresh_token,
            'resource':      client_id,
            'scope':         'openid offline_access',
        })
    except RuntimeError as e:
        print(f"  Refresh failed: {e}")
        return None

    id_token = data.get('id_token')
    new_refresh = data.get('refresh_token')
    if new_refresh:
        # Rotate the refresh token regardless — keeps the token family alive
        with open(REFRESH_FILE, 'w') as f:
            f.write(new_refresh)

    if id_token:
        # Check if the id_token is signed — v1 refresh returns alg:none (unsigned)
        # Therefore requires RS256. Only device code / browser implicit gives RS256.
        header_b64 = id_token.split('.')[0]
        header_b64 += '=' * (4 - len(header_b64) % 4)
        import base64 as _b64
        header = json.loads(_b64.urlsafe_b64decode(header_b64))
        alg = header.get('alg', 'none')
        if alg == 'none' or alg == '':
            print(f"  ✗ Refresh id_token is unsigned (alg:none) — Therefore requires RS256")
            print(f"    → Must use --device-code for a fresh RS256-signed token")
            return None
        print(f"  ✓ Got signed id_token ({len(id_token)} chars, alg:{alg})")
        with open(TOKEN_FILE, 'w') as f:
            f.write(id_token)
        print(f"  ✓ Silent refresh succeeded — no login needed")
        return id_token

    print(f"  → No id_token in refresh response — will use device code")
    return None


def get_token_ropc(scope=None):
    """ROPC flow — fast but fails if MFA is required."""
    print("\n[ROPC] Requesting Entra token via password grant...")
    token_url = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}/oauth2/v2.0/token"

    # Use provided scope, or fall back to Therefore's AAD app client ID scope
    if scope is None:
        scope = f"{THEREFORE_AAD_CLIENT_ID}/.default"
    print(f"       client_id : {ENTRA_CLIENT_ID}")
    print(f"       scope     : {scope}")

    form = {
        'grant_type': 'password',
        'client_id':  ENTRA_CLIENT_ID,
        'client_secret': ENTRA_CLIENT_SECRET,
        'username':   ENTRA_USERNAME,
        'password':   ENTRA_PASSWORD,
        'scope':      scope,
    }
    try:
        data = http_post_form(token_url, form)
    except RuntimeError as e:
        err_str = str(e)
        if 'AADSTS50076' in err_str or 'multi-factor' in err_str.lower():
            print("  [!] MFA required — re-run with --device-code")
            return None
        if 'AADSTS70011' in err_str or 'scope' in err_str.lower():
            print("  [!] Scope/permission error. The client app may not have access to the Therefore app.")
            print(f"      Make sure '{ENTRA_CLIENT_ID}' has API permissions for '{THEREFORE_AAD_CLIENT_ID}'")
            print("      in your Azure AD app registration.")
        raise

    token = data.get('access_token') or data.get('id_token')
    if not token:
        raise RuntimeError(f"No token in response: {data}")

    print(f"  ✓ Got token ({len(token)} chars)")
    return token


def get_token_device_code(tenant=None, scope=None):
    """
    Device code flow — works with MFA, requires browser interaction.

    Uses the v1 OAuth2 endpoint with resource= parameter to get a v1 ID token.
    Therefore requires a v1 ID token (RS256, ver:1.0, upn present, no appid).
    The v2 device code endpoint gives ver:2.0 tokens that Therefore rejects.
    """
    print("\n[Device Code] Requesting Entra token via v1 device code flow...")
    # Must use v1 endpoint with resource= (not v2 scope=).
    # The v1 endpoint returns an RS256 ID token with ver:1.0 and upn claim —
    # exactly what Therefore's GetJWTToken endpoint requires.
    # The v2 endpoint returns ver:2.0 tokens (no upn) that Therefore rejects.
    aad_tenant = tenant or ENTRA_TENANT_ID
    client_id = ENTRA_CLIENT_ID
    dc_url    = f"https://login.microsoftonline.com/{aad_tenant}/oauth2/devicecode"
    token_url = f"https://login.microsoftonline.com/{aad_tenant}/oauth2/token"

    print(f"  client_id : {client_id}")
    print(f"  resource  : {client_id}")
    print(f"  → Therefore needs the v1 ID token (no appid claim)")

    dc_data = http_post_form(dc_url, {
        'client_id': client_id,
        'resource':  client_id,
        'scope':     'openid offline_access',
    })

    # v1 uses verification_url (not verification_uri)
    verify_url = dc_data.get('verification_url') or dc_data.get('verification_uri', '')
    print()
    print("=" * 60)
    print("  ACTION REQUIRED")
    print("=" * 60)
    print(f"  1. Open:  {verify_url}")
    print(f"  2. Enter: {dc_data['user_code']}")
    print("=" * 60)
    print("  Waiting for you to authenticate...", flush=True)

    # v1 poll: grant_type=device_code, code= (not device_code=)
    poll = {
        'grant_type': 'device_code',
        'client_id':  client_id,
        'code':       dc_data['device_code'],
    }

    max_wait = int(dc_data.get('expires_in', 900))
    interval = int(dc_data.get('interval', 5))
    elapsed = 0

    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        print('.', end='', flush=True)
        try:
            data = http_post_form(token_url, poll)
            id_token      = data.get('id_token')
            access_token  = data.get('access_token')
            refresh_token = data.get('refresh_token')
            if id_token or access_token:
                if access_token:
                    with open(os.path.join(_script_dir, '.entra_access_token.txt'), 'w') as f:
                        f.write(access_token)
                if id_token:
                    with open(TOKEN_FILE, 'w') as f:
                        f.write(id_token)
                if refresh_token:
                    # Save refresh token — allows silent re-auth for ~90 days
                    with open(REFRESH_FILE, 'w') as f:
                        f.write(refresh_token)
                    print(f"\n  ✓ Refresh token saved → next login will be silent for ~90 days")

                # Therefore requires the ID token (no appid claim).
                # The access token has appid=aud which Therefore treats as an
                # application token and rejects with AccessDenied.
                token = id_token or access_token
                kind  = 'id_token' if id_token else 'access_token'
                print(f"  ✓ Got {kind} ({len(token)} chars)")
                if not id_token:
                    print("  ⚠ No id_token returned — access_token will likely fail")
                return token
        except RuntimeError as e:
            if 'authorization_pending' in str(e):
                continue
            if 'expired' in str(e).lower():
                raise RuntimeError("Device code expired before user authenticated")
            raise

    raise RuntimeError("Timed out waiting for device code authentication")


# ---------------------------------------------------------------------------
# Therefore JWT exchange
# ---------------------------------------------------------------------------

def exchange_for_therefore_jwt(entra_token, base_url):
    """Exchange an Entra token for a Therefore JWT via GetJWTToken."""
    tenant_name = get_tenant_name(base_url)
    endpoint = f"{base_url.rstrip('/')}/GetJWTToken"

    print(f"\n[Exchange] Calling GetJWTToken...")
    print(f"  endpoint   : {endpoint}")
    print(f"  TenantName : {tenant_name or '(none)'}")

    headers = {
        'Content-Type': 'application/json; charset=utf-8',
        'Authorization': f'Bearer {entra_token}',
    }
    if tenant_name:
        headers['TenantName'] = tenant_name

    # GetJWTToken takes an empty body
    result = http_post_json(endpoint, {}, headers)

    jwt = result.get('JWTToken') or result.get('Token')
    expires = result.get('ExpiresAtISO8601') or result.get('ExpiresAt') or result.get('Expires')

    if not jwt:
        print(f"  ✗ No JWT in response: {json.dumps(result, indent=2)}")
        return None

    print(f"  ✓ Got Therefore JWT ({len(jwt)} chars)")
    if expires:
        print(f"  Expires: {expires}")
    return jwt


def test_therefore_jwt(jwt_token, base_url):
    """Test the Therefore JWT by calling GetConnectedUser."""
    tenant_name = get_tenant_name(base_url)
    endpoint = f"{base_url.rstrip('/')}/GetConnectedUser"

    print(f"\n[Test] Calling GetConnectedUser...")
    headers = {
        'Content-Type': 'application/json; charset=utf-8',
        'Authorization': f'Bearer {jwt_token}',
    }
    if tenant_name:
        headers['TenantName'] = tenant_name

    result = http_post_json(endpoint, {"Create": True}, headers)

    user = result.get('UserName') or result.get('Name') or '?'
    user_no = result.get('UserNo') or result.get('UserId') or '?'
    print(f"  ✓ Authenticated as: {user} (UserNo={user_no})")
    print(f"  Full response: {json.dumps(result, indent=2)}")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global THEREFORE_AAD_CLIENT_ID, ENTRA_CLIENT_ID, ENTRA_TENANT_ID

    parser = argparse.ArgumentParser(description='Test Entra → Therefore JWT exchange')
    parser.add_argument('--device-code', action='store_true',
                        help='Use device code flow (works with MFA)')
    parser.add_argument('--token', help='Provide Entra token directly')
    parser.add_argument('--token-file', default=TOKEN_FILE,
                        help=f'Read Entra token from file (default: {TOKEN_FILE})')
    parser.add_argument('--url', default=THEREFORE_BASE_URL,
                        help='Therefore REST API base URL')
    args = parser.parse_args()

    print("=" * 60)
    print("Therefore Entra/Azure AD Authentication Test")
    print("=" * 60)
    print(f"Therefore URL : {args.url}")

    # Discover AAD config from Therefore — no manual configuration needed
    print("\n[Discovery] Calling GetClientDiscoveryInfo...")
    disc_client_id, disc_tenant, disc_scopes, disc_auth_url = discover_client_settings(args.url)
    if not disc_client_id:
        print("✗ Could not discover Azure AD config from Therefore.")
        print("  Make sure the Therefore server has Azure AD configured,")
        print("  or set ENTRA_TENANT_ID and ENTRA_CLIENT_ID manually in .env")
        return 1

    # Use discovered values, falling back to .env overrides
    THEREFORE_AAD_CLIENT_ID = disc_client_id
    ENTRA_CLIENT_ID = ENTRA_CLIENT_ID or disc_client_id
    aad_tenant = ENTRA_TENANT_ID or disc_tenant

    print(f"\n  AAD Tenant  : {aad_tenant}")
    print(f"  AAD App     : {THEREFORE_AAD_CLIENT_ID}")

    entra_token = None

    # 1. Get the Entra token
    if args.token:
        entra_token = args.token.strip()
        print(f"\n[Token] Using token from command line ({len(entra_token)} chars)")
    elif not args.device_code and os.path.exists(args.token_file):
        # Only use cached token when not explicitly requesting a fresh one
        with open(args.token_file) as f:
            candidate = f.read().strip()
        payload = decode_jwt_payload(candidate)
        if payload:
            exp_ts = payload.get('exp', 0)
            exp_dt = datetime.datetime.fromtimestamp(exp_ts, tz=datetime.timezone.utc)
            now = datetime.datetime.now(datetime.timezone.utc)
            if exp_dt > now:
                print(f"\n[Token] Using cached token from {args.token_file}")
                entra_token = candidate
            else:
                print(f"\n[Token] Cached token is expired, acquiring fresh one...")
        else:
            print(f"\n[Token] Could not decode cached token, acquiring fresh one...")

    if not entra_token and not args.device_code:
        # Try silent refresh first — works for ~90 days after initial login
        entra_token = refresh_id_token(aad_tenant, THEREFORE_AAD_CLIENT_ID)

    if not entra_token:
        # No cached token and no refresh token — must do interactive login
        entra_token = get_token_device_code(tenant=aad_tenant)

        if not entra_token:
            print("\n✗ Failed to obtain Entra token. Exiting.")
            return 1

        with open(TOKEN_FILE, 'w') as f:
            f.write(entra_token)
        print(f"  Saved to: {TOKEN_FILE}")

    # 2. Show token info
    print("\n[Token Info]")
    still_valid = show_token_info(entra_token, "Entra")
    if not still_valid:
        print("  ⚠ Token is expired — acquiring a fresh one...")
        if args.device_code:
            entra_token = get_token_device_code(scope=token_scope)
        else:
            entra_token = get_token_ropc(scope=token_scope) or get_token_device_code(scope=token_scope)
        with open(TOKEN_FILE, 'w') as f:
            f.write(entra_token)
        print(f"  Saved to: {TOKEN_FILE}")
        show_token_info(entra_token, "Entra (fresh)")

    # Verify audience
    payload = decode_jwt_payload(entra_token)
    aud = payload.get('aud', '') if payload else ''
    if aud != THEREFORE_AAD_CLIENT_ID:
        print(f"\n  ⚠ WARNING: token audience '{aud}' does not match")
        print(f"    expected '{THEREFORE_AAD_CLIENT_ID}'")
        print(f"    Therefore may reject this token.")
        print(f"    If exchange fails, confirm the correct Therefore AAD Client ID.")

    # 3. Exchange for Therefore JWT
    try:
        therefore_jwt = exchange_for_therefore_jwt(entra_token, args.url)
    except RuntimeError as e:
        print(f"\n✗ Exchange failed: {e}")
        return 1

    if not therefore_jwt:
        return 1

    with open(JWT_FILE, 'w') as f:
        f.write(therefore_jwt)
    print(f"  Saved to: {JWT_FILE}")

    print("\n[JWT Info]")
    show_token_info(therefore_jwt, "Therefore JWT")

    # 4. Test the JWT
    try:
        test_therefore_jwt(therefore_jwt, args.url)
    except RuntimeError as e:
        print(f"\n✗ JWT test failed: {e}")
        return 1

    print("\n" + "=" * 60)
    print("SUCCESS — Entra → Therefore JWT exchange working!")
    print("=" * 60)
    print(f"Therefore JWT saved to: {JWT_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
