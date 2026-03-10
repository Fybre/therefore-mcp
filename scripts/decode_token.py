#!/usr/bin/env python3
"""
Decode and inspect a JWT token (for debugging).

This script decodes a JWT token without verification, showing:
- Header information (algorithm, token type)
- Payload claims (user info, expiration, etc.)
- Important claims highlighted
- Expiration status

Usage:
    # Inspect the Entra token
    python3 decode_token.py .entra_token.txt
    
    # Inspect the Therefore token
    python3 decode_token.py .therefore_token.txt
    
    # Inspect token from command line
    python3 decode_token.py --token "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIs..."
"""
import base64
import json
import sys
import os
import datetime


def decode_base64_urlsafe(data):
    """Decode base64url-encoded data with proper padding."""
    padding = 4 - len(data) % 4
    if padding != 4:
        data += '=' * padding
    return base64.urlsafe_b64decode(data)


def decode_jwt(token):
    """
    Decode a JWT token into its parts.
    
    Args:
        token: JWT string
        
    Returns:
        Tuple of (header_dict, payload_dict, signature_bytes)
    """
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError(f"Invalid JWT format: expected 3 parts, got {len(parts)}")
    
    # Decode header
    header_json = decode_base64_urlsafe(parts[0])
    header = json.loads(header_json)
    
    # Decode payload
    payload_json = decode_base64_urlsafe(parts[1])
    payload = json.loads(payload_json)
    
    # Signature is just bytes (we don't verify it)
    signature = decode_base64_urlsafe(parts[2])
    
    return header, payload, signature


def format_timestamp(timestamp):
    """Format a Unix timestamp as human-readable."""
    try:
        dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
        return f"{timestamp} ({dt.isoformat()})"
    except:
        return str(timestamp)


def inspect_token(token, label="Token"):
    """
    Inspect and display information about a JWT token.
    """
    print(f"\n{'='*70}")
    print(f"{label} Inspection")
    print(f"{'='*70}")
    
    # Basic info
    print(f"\nToken Length: {len(token)} characters")
    print(f"Token Parts: {len(token.split('.'))}")
    
    if len(token.split('.')) != 3:
        print("\n⚠ This doesn't look like a standard JWT (should have 3 parts)")
        # Try to detect what it might be
        if token.startswith('eyJ'):
            print("  It starts with 'eyJ' which suggests base64-encoded JSON...")
        return
    
    # Decode
    try:
        header, payload, signature = decode_jwt(token)
    except Exception as e:
        print(f"\n✗ Failed to decode token: {e}")
        return
    
    # Header
    print(f"\n{'─'*70}")
    print("HEADER (Algorithm & Token Type)")
    print(f"{'─'*70}")
    for key, value in header.items():
        print(f"  {key}: {value}")
    
    # Payload
    print(f"\n{'─'*70}")
    print("PAYLOAD (Claims)")
    print(f"{'─'*70}")
    
    # Important claims to highlight
    important_claims = {
        'aud': 'Audience (who the token is for)',
        'iss': 'Issuer (who issued the token)',
        'sub': 'Subject (who the token is about)',
        'upn': 'User Principal Name',
        'email': 'Email address',
        'preferred_username': 'Preferred username',
        'name': 'Display name',
        'oid': 'Object ID (unique user ID)',
        'tid': 'Tenant ID',
        'iat': 'Issued At',
        'nbf': 'Not Before',
        'exp': 'Expiration Time',
        'roles': 'Roles',
        'groups': 'Groups',
    }
    
    # Show important claims first
    print("\n  Key Claims:")
    shown = set()
    for claim, description in important_claims.items():
        if claim in payload:
            value = payload[claim]
            shown.add(claim)
            
            # Format special claims
            if claim in ('iat', 'nbf', 'exp'):
                formatted = format_timestamp(value)
            elif isinstance(value, list):
                formatted = f"[{len(value)} items]"
                if len(value) <= 5:
                    formatted += f" {value}"
            elif isinstance(value, dict):
                formatted = json.dumps(value, indent=4)
                formatted = '\n    ' + formatted.replace('\n', '\n    ')
            else:
                formatted = value
            
            print(f"    {claim}: {formatted}")
            print(f"         ({description})")
    
    # Show remaining claims
    remaining = {k: v for k, v in payload.items() if k not in shown}
    if remaining:
        print("\n  Other Claims:")
        for key, value in remaining.items():
            if isinstance(value, (list, dict)):
                value_str = json.dumps(value)[:80]
                if len(json.dumps(value)) > 80:
                    value_str += "..."
            else:
                value_str = str(value)[:80]
            print(f"    {key}: {value_str}")
    
    # Expiration check
    now = datetime.datetime.now(datetime.timezone.utc)
    if 'exp' in payload:
        exp_ts = payload['exp']
        exp_dt = datetime.datetime.fromtimestamp(exp_ts, tz=datetime.timezone.utc)
        time_left = exp_dt - now
        
        print(f"\n{'─'*70}")
        print("EXPIRATION STATUS")
        print(f"{'─'*70}")
        
        if exp_dt < now:
            print(f"  ⚠ EXPIRED {abs(time_left).total_seconds() // 60:.0f} minutes ago")
            print(f"    Expired at: {exp_dt.isoformat()}")
        else:
            hours = time_left.total_seconds() // 3600
            mins = (time_left.total_seconds() % 3600) // 60
            print(f"  ✓ VALID for {hours:.0f} hours {mins:.0f} minutes")
            print(f"    Expires at: {exp_dt.isoformat()}")
    
    # Signature info
    print(f"\n{'─'*70}")
    print("SIGNATURE")
    print(f"{'─'*70}")
    print(f"  Algorithm: {header.get('alg', 'unknown')}")
    print(f"  Signature length: {len(signature)} bytes")
    print(f"  (Signature not verified)")
    
    print(f"\n{'='*70}")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Decode and inspect JWT tokens'
    )
    parser.add_argument(
        'file',
        nargs='?',
        help='File containing the JWT token (default: .entra_token.txt)'
    )
    parser.add_argument(
        '--token',
        help='Provide token directly on command line'
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='List available token files'
    )
    
    args = parser.parse_args()
    
    # List available tokens
    if args.list:
        print("Available token files:")
        files = ['.entra_token.txt', '.therefore_token.txt']
        for f in files:
            if os.path.exists(f):
                size = os.path.getsize(f)
                print(f"  ✓ {f} ({size} bytes)")
            else:
                print(f"  ✗ {f} (not found)")
        return 0
    
    # Get token
    if args.token:
        token = args.token
        source = "command line"
    elif args.file:
        try:
            with open(args.file, 'r') as f:
                token = f.read().strip()
            source = args.file
        except FileNotFoundError:
            print(f"✗ File not found: {args.file}")
            return 1
        except Exception as e:
            print(f"✗ Error reading file: {e}")
            return 1
    else:
        # Try default files
        default_files = ['.entra_token.txt', '.therefore_token.txt']
        token = None
        source = None
        for f in default_files:
            if os.path.exists(f):
                try:
                    with open(f, 'r') as file:
                        token = file.read().strip()
                    source = f
                    print(f"✓ Using default file: {f}")
                    break
                except:
                    pass
        
        if not token:
            print("✗ No token file found. Specify a file or use --token.")
            print(f"\nUsage: {sys.argv[0]} [.entra_token.txt | .therefore_token.txt]")
            print(f"   or: {sys.argv[0]} --token \"eyJ0eXAiOiJKV1Qi...\"")
            return 1
    
    # Inspect
    try:
        inspect_token(token, source)
        return 0
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
