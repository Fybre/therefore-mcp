# Therefore Authentication Guide: Entra ID & Service Accounts

This guide explains how to authenticate with the Therefore REST API using Microsoft Entra ID (Azure AD) or a custom Service Account (Trusted Token Issuer).

---

## 1. Microsoft Entra ID (Azure AD) Flow

This is the standard flow for interactive users. It requires no manual Azure AD configuration, as Therefore's server provides the necessary Client ID and Tenant info automatically.

### How it Works
1. **Discovery:** The script calls `GetClientDiscoveryInfo` to ask Therefore which Entra App ID (`7153cdac...`) and Tenant it uses.
2. **Login (Device Code):** You visit `microsoft.com/device` and enter a code shown by the script. You log in with your Microsoft account (MFA supported).
3. **Token:** Microsoft issues a signed **v1.0 ID Token** (valid for 60 minutes).
4. **Exchange:** You send this ID token to Therefore's `GetJWTToken` endpoint to receive a **Therefore JWT** (valid for 30 minutes).

### Direct Authentication (Short-Circuit)
You can skip the `GetJWTToken` step and use the Entra ID token directly as a Bearer token for functional API calls (e.g., `GetConnectedUser`, `CreateDocument`) for its full 60-minute lifetime.

### The "Silent Refresh" Limitation
**Silent refresh is NOT possible with the standard Therefore App ID.**
* **Why:** To get a signed token, Microsoft requires a "Confidential Client" (an app with a secret). Since Therefore's App ID (`7153...`) is a "Public Client" in your tenant, the `refresh_token` grant returns an **unsigned** (`alg:none`) ID token.
* **Result:** Therefore rejects unsigned tokens. You must re-run the `--device-code` flow every 60 minutes.

---

## 2. Service Account Flow (Trusted Token Issuer)

This is the **recommended solution** for background services, MCP servers, or any "headless" integration that requires silent, long-lived access without user interaction.

### How it Works
You configure Therefore to trust a "Shared Secret." Your script then generates its own signed tokens locally.

### Setup (Solution Designer)
1. Go to **External Authentication** -> **Trusted Token Issuers**.
2. Add a new Issuer (e.g., `Therefore-MCP-Bridge`).
3. Set a **Shared Secret** (a strong random string).
4. Note your **Customer ID** (found in the `aud` claim of any working Therefore JWT, e.g., `ASDNYY175N`).

### Usage
Your script generates a JWT locally using the Shared Secret (HS256) with these claims:
* `iss`: Your Issuer ID (e.g., `Therefore-MCP-Bridge`)
* `aud`: Your Customer ID (e.g., `ASDNYY175N`)
* `http://schemas.microsoft.com/ws/2008/06/identity/claims/windowsaccountname`: The user's account name (e.g., `domain.onmicrosoft.com\user`)
* `urn:oauth:scope`: `therefore_user`
* `exp`: Set this to 24 hours or more.

---

## 3. Multi-Tenant Architecture (Service Providers)

If you are providing an MCP service to **multiple Therefore tenants**, use a **Consistent Issuer ID** with **Unique Secrets** to ensure security and isolation.

### Recommended Strategy
* **Consistent Issuer ID:** Use the same name (e.g., `Therefore-MCP-Bridge`) for every customer. This simplifies your code and makes your service easy for customers to set up.
* **Unique Shared Secret:** Every customer generates their own unique secret. This ensures that a token for Tenant A can never be used to access Tenant B.
* **Unique Audience (`aud`):** Use the customer's specific Therefore Customer ID as the `aud` claim. Therefore's server will reject any token where the `aud` does not match its own ID.

### Tenant Mapping Logic
Your service should maintain a secure mapping of tenants to their respective secrets:

| Tenant Subdomain | Issuer ID (Consistent) | Shared Secret (Unique) | Customer ID (Unique `aud`) |
| :--- | :--- | :--- | :--- |
| `acme.thereforeonline.com` | `Therefore-MCP-Bridge` | `secret_acme_123...` | `ACME_001` |
| `globex.thereforeonline.com` | `Therefore-MCP-Bridge` | `secret_globex_999...` | `GLOBEX_777` |

### Security Benefits
1. **Isolation:** A compromised secret only affects a single tenant.
2. **Revocation:** A customer can stop your access instantly by deleting the Issuer from their Solution Designer.
3. **Auditability:** The administrator sees exactly which "Bridge" (your service) is performing actions in their logs.

---

## Technical Reference

### Token Lifetimes (Defaults)
| Token | Source | Lifetime |
|-------|--------|----------|
| Entra ID Token | Microsoft | ~60 minutes |
| Therefore JWT | `GetJWTToken` | ~30 minutes |
| Trusted Issuer Token | Your Script | Custom (e.g., 24h) |

### Claim Mapping Requirements
Therefore is highly specific about the claims it requires to identify a user:

| Token Type | Required Claim | Value Example |
|------------|----------------|---------------|
| **Entra v1.0** | `upn` | `user@domain.com` |
| **Trusted Issuer** | `windowsaccountname` | `domain.onmicrosoft.com\user` |

> **Note:** Entra **v2.0** tokens are currently **rejected** by Therefore because they lack the `upn` claim and use a version format (`ver: 2.0`) Therefore does not recognise.

---

## Scripts & Usage

### 1. Interactive Entra Login
```bash
python3 test_entra_jwt_exchange.py --device-code
```
Follow the browser prompts. The tokens are saved to `.entra_id_token.txt` and `.therefore_jwt.txt`.

### 2. Service Account (Trusted Issuer) Test
Ensure these are set in your `.env`:
* `THEREFORE_MCP_BRIDGE_ID`
* `THEREFORE_MCP_BRIDGE_SECRET`
* `THEREFORE_MCP_CUSTOMER_ID`

Then run:
```bash
python3 test_trusted_token_issuer.py
```

### 3. Debugging Tokens
```bash
python3 decode_token.py .therefore_jwt.txt
```
Decodes any JWT to inspect claims, issuer, and expiration.

---

## Troubleshooting

* **HTTP 401 (Unauthorized):** Usually means the token signature is missing (`alg:none`) or the `aud` (Audience) claim doesn't match your Customer ID.
* **HTTP 500 (Server Error):** Often means the user mapping failed. Ensure you are sending `upn` for Entra tokens or `windowsaccountname` for Trusted Issuer tokens.
* **MFA Errors:** ROPC (username/password) flows will fail if MFA is enabled. Use the Device Code flow or the Trusted Token Issuer instead.
