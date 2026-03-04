# Therefore MCP Bridge: Auth & Multi-Tenant Admin Guide

This guide explains how to configure and manage the **Therefore MCP Bridge** architecture for secure, multi-tenant access via a centralized gateway.

---

## 1. Architectural Overview
The system consists of three layers of security:
1.  **Auth Provider:** Signs JWTs using a Shared Secret. (The "Trust" layer)
2.  **MCP Server:** Orchestrates tool calls and enforces tenant whitelists. (The "Gateway" layer)
3.  **MCP Client:** (Claude, Goose, etc.) Connects to the Gateway using a unique API Key.

---

## 2. Phase 1: Therefore Server Setup
Before configuring the Bridge, you must establish trust on each Therefore tenant.

1.  **Open Therefore Solution Designer.**
2.  Go to **Access** -> **Authentication** -> **Custom JWT Tokens**.
3.  Add a new Entry:
    *   **Issuer ID:** `Therefore-MCP-Bridge`
    *   **Name** `Therefore MCP Bridge Token`
    *   **Secret/Certificate:** Generate a long, random string (e.g., 32+ characters). **Save this; you will need it for the Auth Provider.**
4.  Note your **Customer ID** (e.g., `ASDNYY175N`) from the License Settings.

---

## 3. Phase 2: Auth Provider Configuration
The Auth Provider service (in `services/auth-provider/`) manages the secrets for all your tenants.

1.  **Create the config file:**
    ```bash
    cp services/auth-provider/tenants.json.example services/auth-provider/tenants.json
    ```
2.  **Configure Tenants:** Edit `tenants.json` with your Therefore secrets.
    ```json
    {
      "customer_a": {
        "customer_id": "ASDNYY175N",
        "shared_secret": "your-therefore-shared-secret",
        "bridge_api_key": "generate-a-unique-key-for-mcp-server",
        "user_mapping": "DOMAIN\\MCPUser",
        "allowed_users": ["DOMAIN\\MCPUser", "DOMAIN\\AdminUser"]
      }
    }
    ```
    *   **`bridge_api_key`**: A secret key the **MCP Server** must provide to request tokens for this tenant.
    *   **`user_mapping`**: The default Windows account Therefore will see for this tenant.
    *   **`allowed_users`**: A whitelist of Windows accounts the MCP Server is allowed to impersonate via the `user_hint` parameter.

---

## 4. Phase 3: MCP Server Configuration
The central MCP Server handles the actual AI tool calls and restricts client access.

1.  **Configure `.env.local`:**
    Set up the connection to the Auth Provider for each tenant:
    ```env
    THEREFORE_TENANTS=customer_a
    THEREFORE_CUSTOMER_A_BASE_URL=https://customer_a.thereforeonline.com/theservice/v0001/restun
    THEREFORE_CUSTOMER_A_AUTH_METHOD=S2S
    THEREFORE_CUSTOMER_A_AUTH_PROVIDER_URL=http://auth-provider:8001
    THEREFORE_CUSTOMER_A_BRIDGE_API_KEY=key-from-tenants-json-above
    ```

2.  **Configure Client Access Control:**
    Create `config/clients.json` to define which MCP Clients can access which tenants:
    ```json
    {
      "sk_live_alpha_12345": ["customer_a"],
      "sk_live_beta_67890": ["customer_b", "demo"]
    }
    ```
    *   **Key**: The API Key you give to your user (e.g., for their Claude Desktop config).
    *   **Value**: Array of tenant keys they are permitted to query.

---

## 5. Deployment with Docker
Use Docker Compose to run the full stack (including the Auth Provider and MCP Server).

1.  **Build the Auth Provider Image:**
    ```bash
    cd services/auth-provider && ./build_docker.sh
    ```
2.  **Start the Stack:**
    ```bash
    # Starts Auth Provider (8001) and MCP Gateway (8000)
    docker compose --profile http up -d
    ```

---

## 6. Client Connection (Claude/Goose)
Give the user their specific API Key from `clients.json` and the URL of your tunnel.

**Example Claude Desktop Config:**
```json
{
  "mcpServers": {
    "therefore": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/sdk", "connect", "https://therefore-mcp.fybre.me/mcp"],
      "env": {
        "Authorization": "Bearer sk_live_alpha_12345"
      }
    }
  }
}
```

---

## 7. Security & Auditing
*   **Audit Logs:** Monitor `stderr` (or `docker logs`) for `[AUDIT]` entries. They contain the client ID, IP address (Cloudflare supported), and redact all sensitive data.
*   **Token Refresh:** Tokens are automatically cached for 60 minutes and refreshed by the MCP Server when they expire.
*   **Isolation:** If a client key is leaked, you can simply remove that one key from `clients.json` without affecting other customers.
*   **Cloudflare Tunnel:** Ensure your tunnel passes the `CF-Connecting-IP` header so the audit logs accurately track client origins.
