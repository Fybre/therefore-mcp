# Capturing Entra/ADFS Token from Browser

## Universal Method: Browser Network Tab (Works Regardless of MSAL)

### Step 1: Open Developer Tools
- Chrome/Edge: Press **F12** or **Ctrl+Shift+I** (Cmd+Option+I on Mac)
- Firefox: Press **F12** or **Ctrl+Shift+K**

### Step 2: Preserve Network Log
- Go to **Network** tab
- Check **"Preserve log"** checkbox (crucial!)

### Step 3: Clear Current Log
- Click the **🚫 Clear** button (circle with line through it)

### Step 4: Login to Therefore
1. Go to your Therefore web URL
2. Click login (should redirect to Microsoft/ADFS)
3. Enter credentials and complete MFA
4. Get redirected back to Therefore

### Step 5: Find the Token

Look for these request patterns in the Network tab:

#### Pattern A: Token Endpoint (most common)
- URL contains: `login.microsoftonline.com` + `/token` or `/oauth2/v2.0/token`
- Method: **POST**
- Look at **Response** tab for JSON with `access_token`

#### Pattern B: Authorization Response
- URL contains: `your-tenant.thereforeonline.com` + `code=` or `token=`
- Look at **Headers** → **Response Headers** for redirects

#### Pattern C: Therefore API Calls
- URL contains: `your-tenant.thereforeonline.com/theservice/`
- Look at **Request Headers** for `Authorization: Bearer ...`

### Step 6: Extract the Token

1. Click on the relevant request
2. Look for:
   - **Response** tab → `access_token` field
   - **Headers** → **Request Headers** → `Authorization` header (remove "Bearer ")

3. Right-click the token value → **Copy value**

---

## Alternative: Cookie/Storage Inspection

In browser console (F12 → Console), try:

```javascript
// List all cookies
document.cookie.split(';').map(c => c.trim())

// Search all storage for "token"
const searchStorage = (storage, name) => {
  for (let i = 0; i < storage.length; i++) {
    const key = storage.key(i);
    if (key && key.toLowerCase().includes('token')) {
      console.log(`${name}["${key}"]:`, storage.getItem(key).substring(0, 100) + '...');
    }
  }
};
searchStorage(localStorage, 'localStorage');
searchStorage(sessionStorage, 'sessionStorage');
```

---

## Alternative: Proxy Method (Fiddler/Charles)

If browser methods fail:

1. Install [Fiddler](https://www.telerik.com/fiddler) or [Charles Proxy](https://www.charlesproxy.com/)
2. Enable HTTPS decryption
3. Login to Therefore through the proxy
4. Look for requests to:
   - `login.microsoftonline.com`
   - Your Therefore domain
5. Find the token in request/response bodies

---

## Alternative: Browser Extension

Install "**Token Debugger**" or "**JWT Debugger**" extensions:
- Chrome: [JWT Debugger](https://chrome.google.com/webstore/detail/jwt-debugger/ppedokobpbdajgiejhnjfbdjlgobcpkp)
- Firefox: [JWT Inspector](https://addons.mozilla.org/en-US/firefox/addon/jwt-inspector/)

These automatically detect JWTs in network traffic.

---

## Quick Check: Is it a JWT?

A valid JWT looks like this:
```
eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsIng1dCI6IjJ... (header)
.eyJhdWQiOiJodHRwczovL2dyYXBoLm1pY3Jvc29mdC5jb20iLCJpc3MiOiJodHRw... (payload)
.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c... (signature)
```

- 3 parts separated by dots
- Starts with `ey` (base64url encoded `{`)
- Usually 1000+ characters

---

## If All Else Fails

Use **ROPC flow with a service account** that doesn't have MFA:

1. Create a test user in Azure AD without MFA
2. Use `get_entra_token.py` with those credentials
3. Or ask your IT admin for a service principal token
