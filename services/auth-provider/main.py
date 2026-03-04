import os
import json
from typing import Dict, Optional, List
from fastapi import FastAPI, HTTPException, Body, Header
from pydantic import BaseModel

from auth import create_therefore_jwt

app = FastAPI(title="Therefore MCP Bridge Auth Provider")

# In-memory tenant store (could be swapped for DB)
TENANTS_FILE = os.path.join(os.path.dirname(__file__), "tenants.json")

class TenantConfig(BaseModel):
    customer_id: str
    shared_secret: str
    issuer: str = "Therefore-MCP-Bridge"
    user_mapping: str
    bridge_api_key: str
    allowed_users: List[str] = []

class TokenRequest(BaseModel):
    tenant: str
    user_hint: Optional[str] = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 3600

def load_tenants() -> Dict[str, TenantConfig]:
    if not os.path.exists(TENANTS_FILE):
        return {}
    with open(TENANTS_FILE, "r") as f:
        data = json.load(f)
        return {k: TenantConfig(**v) for k, v in data.items()}

@app.get("/health")
def health():
    return {"status": "ok", "tenants_loaded": len(load_tenants())}

@app.get("/tenants")
def list_tenants():
    """List available tenant keys (no secrets)."""
    tenants = load_tenants()
    return list(tenants.keys())

@app.post("/issue-token", response_model=TokenResponse)
def issue_token(
    request: TokenRequest,
    x_bridge_api_key: Optional[str] = Header(None)
):
    """
    Issue a signed JWT for the specified tenant.
    Requires a valid X-Bridge-API-Key header.
    """
    tenants = load_tenants()
    if request.tenant not in tenants:
        raise HTTPException(status_code=404, detail=f"Tenant '{request.tenant}' not configured.")
    
    cfg = tenants[request.tenant]
    
    # 1. Verify Bridge API Key (Who is calling?)
    if not x_bridge_api_key or x_bridge_api_key != cfg.bridge_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing Bridge API Key.")

    # 2. Verify User Hint (What are they requesting?)
    effective_user = request.user_hint or cfg.user_mapping
    
    # If a hint is provided, it MUST be in the whitelist
    if request.user_hint and request.user_hint not in cfg.allowed_users:
        raise HTTPException(
            status_code=403, 
            detail=f"User '{request.user_hint}' is not in the allowed list for this tenant."
        )
    
    try:
        token = create_therefore_jwt(
            tenant_key=request.tenant,
            customer_id=cfg.customer_id,
            shared_secret=cfg.shared_secret,
            user_mapping=effective_user,
            issuer=cfg.issuer
        )
        return TokenResponse(access_token=token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
