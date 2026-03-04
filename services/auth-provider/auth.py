import jwt
import datetime
from typing import Optional

# Mandated claim for user mapping to avoid 500/401 errors
WINDOWS_ACCOUNT_CLAIM = "http://schemas.microsoft.com/ws/2008/06/identity/claims/windowsaccountname"

def create_therefore_jwt(
    tenant_key: str,
    customer_id: str,
    shared_secret: str,
    user_mapping: str,
    issuer: str = "Therefore-MCP-Bridge",
    expires_in_minutes: int = 60
) -> str:
    """
    Sign a JWT using HS256 for Therefore Trusted Token Issuer (S2S) authentication.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    
    payload = {
        "iss": issuer,
        "aud": customer_id,
        "exp": now + datetime.timedelta(minutes=expires_in_minutes),
        "iat": now,
        "nbf": now,
        "scope": "urn:oauth:scope:therefore_user",
        # Use the specific claim Therefore expects for user mapping
        WINDOWS_ACCOUNT_CLAIM: user_mapping
    }
    
    token = jwt.encode(payload, shared_secret, algorithm="HS256")
    return token

def decode_therefore_jwt(token: str, shared_secret: str, customer_id: str, issuer: str):
    """
    Utility to decode and verify a token (for internal testing/validation).
    """
    return jwt.decode(
        token, 
        shared_secret, 
        algorithms=["HS256"], 
        audience=customer_id, 
        issuer=issuer
    )
