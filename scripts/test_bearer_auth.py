#!/usr/bin/env python3
"""
Test Bearer/JWT authentication against the Therefore WebAPI.

Authenticates via Basic auth to obtain a JWT, decodes and inspects it,
then uses Bearer auth with a custom redirect handler that preserves
POST method on 307/308 redirects (urllib normally blocks these).
"""

import base64
import json
import os
import ssl
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# .env.local loader
# ---------------------------------------------------------------------------

def load_env(path: str) -> dict[str, str]:
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            env[key.strip()] = value.strip()
    return env


# ---------------------------------------------------------------------------
# JWT decoder (no external deps)
# ---------------------------------------------------------------------------

def decode_jwt_payload(token: str) -> dict:
    """Decode the payload (second segment) of a JWT without verification."""
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError(f"Expected 3 JWT segments, got {len(parts)}")
    payload_b64 = parts[1]
    # Add padding
    payload_b64 += '=' * (-len(payload_b64) % 4)
    payload_bytes = base64.urlsafe_b64decode(payload_b64)
    return json.loads(payload_bytes)


def fmt_ts(epoch: int | float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Redirect-preserving handler for POST on 307/308
# ---------------------------------------------------------------------------

class RedirectHandlerPreserveMethod(urllib.request.HTTPRedirectHandler):
    """Follow 307/308 redirects while keeping the original method, body, and headers."""

    redirects: list[str] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirects.append(f"  -> {code} {msg} -> {newurl}")
        if code in (307, 308):
            new_req = urllib.request.Request(
                newurl,
                data=req.data,
                headers=dict(req.header_items()),
                method=req.get_method(),
            )
            return new_req
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def make_ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode('ascii')
    return f"Basic {token}"


def do_post(url: str, payload: dict, headers: dict, ctx: ssl.SSLContext,
            opener: urllib.request.OpenerDirector | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    for k, v in headers.items():
        req.add_header(k, v)
    if opener:
        resp = opener.open(req, timeout=30)
    else:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
    with resp:
        body = resp.read().decode('utf-8', errors='replace')
        return resp.status, json.loads(body) if body else {}


def do_get(url: str, headers: dict, ctx: ssl.SSLContext,
           opener: urllib.request.OpenerDirector | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method='GET')
    for k, v in headers.items():
        req.add_header(k, v)
    if opener:
        resp = opener.open(req, timeout=30)
    else:
        resp = urllib.request.urlopen(req, context=ctx, timeout=30)
    with resp:
        body = resp.read().decode('utf-8', errors='replace')
        return resp.status, json.loads(body) if body else {}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Check for --token argument
    provided_token = None
    if '--token' in sys.argv:
        idx = sys.argv.index('--token')
        if idx + 1 < len(sys.argv):
            provided_token = sys.argv[idx + 1]
        else:
            print("ERROR: --token requires a JWT string")
            sys.exit(1)

    # Locate .env.local
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / 'docs' / 'reference' / 'user' / '.env.local'
    if not env_path.exists():
        print(f"ERROR: {env_path} not found")
        sys.exit(1)

    env = load_env(str(env_path))
    base_url = env.get('THEREFORE_CRAIGDEMO_BASE_URL', '').rstrip('/')
    username = env.get('THEREFORE_CRAIGDEMO_USERNAME', '')
    password = env.get('THEREFORE_CRAIGDEMO_PASSWORD', '')
    tenant   = env.get('THEREFORE_CRAIGDEMO_TENANTNAME', '')

    ctx = make_ssl_context()

    if provided_token:
        # Skip step 1 — use the provided token directly
        print("=" * 60)
        print("STEP 1: SKIPPED — using provided JWT token")
        print("=" * 60)
        print(f"JWT length: {len(provided_token)} chars")
        jwt_token = provided_token
    else:
        if not all([base_url, username, password]):
            print("ERROR: Missing craigdemo credentials in .env.local")
            sys.exit(1)

        # -- Step 1: Get JWT via Basic auth --
        print("=" * 60)
        print("STEP 1: Obtain JWT via Basic auth (GetJWTToken)")
        print("=" * 60)

        basic_headers = {
            'Content-Type': 'application/json; charset=utf-8',
            'Authorization': basic_auth_header(username, password),
            'TenantName': tenant,
        }

        try:
            status, jwt_resp = do_post(f"{base_url}/GetJWTToken", {}, basic_headers, ctx)
        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode('utf-8', errors='replace')
            except Exception:
                pass
            print(f"FAILED: HTTP {e.code} — {body or e.reason}")
            sys.exit(1)

        jwt_token = jwt_resp.get('JWTToken', '')
        expires_iso = jwt_resp.get('ExpiresAtISO8601', '')

        print(f"HTTP {status}")
        print(f"ExpiresAtISO8601: {expires_iso}")
        print(f"JWT length: {len(jwt_token)} chars")

        if not jwt_token:
            print("ERROR: No JWT token in response")
            sys.exit(1)

    # -- Step 2: Decode JWT --
    print()
    print("=" * 60)
    print("STEP 2: Decode JWT payload")
    print("=" * 60)

    try:
        claims = decode_jwt_payload(jwt_token)
    except Exception as e:
        print(f"ERROR decoding JWT: {e}")
        sys.exit(1)

    for key in sorted(claims.keys()):
        val = claims[key]
        extra = ''
        if key in ('nbf', 'exp', 'iat') and isinstance(val, (int, float)):
            extra = f"  ({fmt_ts(val)})"
        print(f"  {key}: {val}{extra}")

    nbf = claims.get('nbf')
    exp = claims.get('exp')
    now = datetime.now(tz=timezone.utc).timestamp()
    if nbf and exp:
        print()
        if now < nbf:
            print(f"  STATUS: Not yet valid (nbf is in the future)")
        elif now > exp:
            print(f"  STATUS: EXPIRED (exp was {fmt_ts(exp)})")
        else:
            remaining = exp - now
            print(f"  STATUS: Valid — expires in {remaining:.0f}s ({remaining/60:.1f}m)")

    handler = RedirectHandlerPreserveMethod()
    opener = urllib.request.build_opener(
        handler,
        urllib.request.HTTPSHandler(context=ctx),
    )

    bearer_headers = {
        'Content-Type': 'application/json; charset=utf-8',
        'Authorization': f'Bearer {jwt_token}',
        'TenantName': tenant,
    }

    # -- Step 3a: GET endpoint (GetSystemCustomerId) --
    print()
    print("=" * 60)
    print("STEP 3a: GET GetSystemCustomerId with Bearer JWT")
    print("=" * 60)

    print(f"GET {base_url}/GetSystemCustomerId")
    print(f"Authorization: Bearer <{len(jwt_token)} chars>")
    print(f"TenantName: {tenant}")
    print()

    try:
        handler.redirects = []
        status, cust_resp = do_get(f"{base_url}/GetSystemCustomerId", bearer_headers, ctx, opener)
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        print(f"FAILED: HTTP {e.code} — {body or e.reason}")
        if handler.redirects:
            print("Redirect chain:")
            for r in handler.redirects:
                print(r)
        cust_resp = None
        status = e.code

    if cust_resp is not None:
        if handler.redirects:
            print("Redirect chain:")
            for r in handler.redirects:
                print(r)
            print()
        print(f"HTTP {status}")
        print(f"Response: {json.dumps(cust_resp, indent=2)}")

    get_ok = status == 200

    # -- Step 3b: POST endpoint (GetCategoryInfo) --
    safe_cat = env.get('THEREFORE_CRAIGDEMO_SAFE_CATEGORY_ID', '262')
    cat_no = int(safe_cat) if safe_cat else 262

    print()
    print("=" * 60)
    print(f"STEP 3b: POST GetCategoryInfo (CategoryNo={cat_no}) with Bearer JWT")
    print("=" * 60)

    payload = {"CategoryNo": cat_no}
    print(f"POST {base_url}/GetCategoryInfo")
    print(f"Authorization: Bearer <{len(jwt_token)} chars>")
    print(f"TenantName: {tenant}")
    print(f"Body: {json.dumps(payload)}")
    print()

    post_ok = False
    try:
        handler.redirects = []
        status, cat_resp = do_post(f"{base_url}/GetCategoryInfo", payload, bearer_headers, ctx, opener)
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode('utf-8', errors='replace')
        except Exception:
            pass
        print(f"FAILED: HTTP {e.code} — {body or e.reason}")
        if handler.redirects:
            print("Redirect chain:")
            for r in handler.redirects:
                print(r)
        status = e.code
        cat_resp = None

    if cat_resp is not None:
        if handler.redirects:
            print("Redirect chain:")
            for r in handler.redirects:
                print(r)
            print()
        post_ok = status == 200
        print(f"HTTP {status}")
        # Show just key fields, not the entire category schema
        name = cat_resp.get('Name', cat_resp.get('CategoryName', ''))
        print(f"Category: {name} (No={cat_resp.get('CategoryNo', '?')})")
        field_count = len(cat_resp.get('IndexFields', []))
        print(f"IndexFields: {field_count} fields")

    # -- Summary --
    print()
    print("=" * 60)
    print(f"RESULT: Bearer GET  — {'SUCCEEDED' if get_ok else 'FAILED'}")
    print(f"RESULT: Bearer POST — {'SUCCEEDED' if post_ok else 'FAILED'}")
    print("=" * 60)


if __name__ == '__main__':
    main()
