# Email Template for Therefore Support

## Subject
Question about SSO authentication via REST API - Azure AD/Entra ID token exchange

## Body

---

Hello Therefore Support,

We are developing an integration with our Therefore Online instance and need to authenticate users via Azure AD (Entra ID) SSO through the REST API.

**Our Goal:**
Allow users to authenticate via Azure AD SSO and obtain a Therefore session token through the REST API (not the web interface).

**What We Found:**
We discovered the `GetConnectionTokenFromADFSToken` endpoint at:
```
POST /theservice/v0001/restun/GetConnectionTokenFromADFSToken
```

We attempted to exchange an Azure AD token using this endpoint, but received an empty response:
```json
{
  "NodeFriendly": null,
  "Token": ""
}
```

**Our Questions:**

1. **Does `GetConnectionTokenFromADFSToken` support Azure AD (Entra ID) JWT tokens?**
   - Or is this endpoint only for ADFS SAML assertions?

2. **If Azure AD is not supported by this endpoint, is there an alternative?**
   - Is there a `GetConnectionTokenFromAzureADToken` or similar endpoint?
   - Or another recommended approach for Azure AD SSO via REST API?

3. **What is the correct token format expected?**
   - ID token vs Access token?
   - JWT vs SAML?
   - Specific claims or audience requirements?

4. **Is there documentation available** for SSO authentication via the REST API?

**Our Environment:**
- Therefore Online instance: [your-tenant].thereforeonline.com
- Identity Provider: Azure AD (Entra ID)
- Current SSO status: Working via web interface, need API access

**Use Case:**
We are building an MCP (Model Context Protocol) server that allows AI assistants to interact with Therefore on behalf of users. We need users to authenticate via their existing Azure AD credentials.

Thank you for your assistance.

Best regards,
[Your Name]
[Your Organization]

---

## Additional Context You Can Include

If they ask for more details, mention:

1. **Token we tried:**
   - JWT format (3 parts, starts with `eyJ`)
   - Issuer: `https://sts.windows.net/{tenant-id}/`
   - Audience: Azure AD app client ID
   - Contains claims: `oid`, `tid`, `upn`, `name`

2. **Error Details:**
   - HTTP 200 OK (no error), but empty token returned
   - NodeFriendly is null
   - No error message in response

3. **What Works:**
   - Web interface SSO works fine
   - Basic Auth to REST API works fine
   - Only the token exchange is not working

## Expected Responses

| If They Say... | What It Means |
|---------------|---------------|
| "Use GetConnectionTokenFromAzureADToken" | There's a different endpoint - great! |
| "ADFS only" | Azure AD not supported via API |
| "Configure federation" | You need ADFS in front of Azure AD |
| "Not supported" | Feature doesn't exist |
| "Use OAuth2/OIDC" | There might be a modern OAuth flow |

## Follow-up Questions to Ask

1. Is there a roadmap for Azure AD native support?
2. Can we use the WebClient authentication flow instead?
3. Is there a service account approach recommended?
4. What do other customers use for API-based SSO?
