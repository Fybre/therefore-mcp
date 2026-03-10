#!/usr/bin/env python3
"""
Manually enter or paste an Entra/ADFS token.

This is useful when:
- Device code flow doesn't work with your app registration
- ROPC flow is blocked by MFA
- You already have a token from browser dev tools or another source

Usage:
    python3 get_entra_token_manual.py
    
    Then paste your token when prompted.
"""
import os
import sys

TOKEN_FILE = os.environ.get('TOKEN_FILE', '.entra_token.txt')


def main():
    print("="*60)
    print("Manual Token Entry")
    print("="*60)
    print()
    print("Paste your Entra/ADFS token below and press Enter twice:")
    print("(The token typically starts with 'eyJ' and is very long)")
    print()
    
    # Read token (allow multi-line paste)
    lines = []
    try:
        while True:
            line = input()
            if not line.strip():
                break
            lines.append(line)
    except EOFError:
        pass
    
    token = ''.join(lines).strip()
    
    if not token:
        print("✗ No token provided")
        return 1
    
    # Basic validation
    if not token.startswith('ey'):
        print(f"⚠ Warning: Token doesn't start with 'ey' - got '{token[:10]}...'")
        print("  Entra tokens usually start with 'eyJ' (JWT format)")
        response = input("Continue anyway? (y/N): ")
        if response.lower() != 'y':
            return 1
    
    # Check if it's a JWT
    parts = token.split('.')
    if len(parts) == 3:
        print(f"✓ Looks like a JWT (3 parts, {len(token)} chars)")
    else:
        print(f"⚠ Warning: Token has {len(parts)} parts (JWT should have 3)")
    
    # Save
    with open(TOKEN_FILE, 'w') as f:
        f.write(token)
    
    print(f"\n✓ Token saved to: {TOKEN_FILE}")
    print(f"\nNext step: python3 test_therefore_adfs_exchange.py --step exchange")
    return 0


if __name__ == '__main__':
    sys.exit(main())
