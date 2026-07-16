# therefore-mcp

A Python [MCP](https://modelcontextprotocol.io/) server that connects AI assistants to the [Therefore™](https://therefore.net/) document management system via its WebAPI.

Exposes **10 tools**: a natural-language router (`ask_therefore_expert`), a runtime tenant/login registration tool (`therefore_connect`), and **8 grouped tools** (100 operations total) covering document CRUD, querying, workflow management and Cases, keyword dictionaries, user administration, categories, and system operations. Supports multi-tenant deployments with per-client access control and audit logging, including tenants registered at runtime via `therefore_connect` rather than pre-configured in `.env.local`. Zero external dependencies for stdio mode — pure Python standard library.

## Quick Start

### Prerequisites

- Python 3.9+
- A Therefore instance with WebAPI access
- An MCP-compatible client (Claude Code, Claude Desktop, Codex, Cursor, etc.)

### Configuration

Create a `.env.local` in the project root (or point `THEREFORE_ENV_PATH` at another path):

```env
THEREFORE_TENANTS=mytenant
THEREFORE_DEFAULT_TENANT=mytenant

THEREFORE_MYTENANT_BASE_URL=https://mytenant.thereforeonline.com/theservice/v0001/restun
THEREFORE_MYTENANT_AUTH_METHOD=Basic
THEREFORE_MYTENANT_USERNAME=your.username
THEREFORE_MYTENANT_PASSWORD=your-password
THEREFORE_MYTENANT_TENANTNAME=mytenant
```

See [Environment Variables](#environment-variables) for the full reference.

**This step is optional.** The server starts fine with zero tenants configured (or none
at all — no `.env.local` required). An agent can instead call the `therefore_connect`
tool at runtime to register a tenant/login for the session:

```json
{"tenant_name": "mytenant", "username": "your.username", "password": "your-password"}
```

It verifies the login before registering it and returns a `tenant_key` to use as the
`tenant` argument on every other call. Registration is in-memory only (not written to
`.env.local`) and, in HTTP mode, scoped to the API key that registered it. Useful for ad
hoc/multi-tenant agent use without maintaining credentials in a file on disk.

### Running

```bash
# stdio (default) — for MCP clients
python3 src/mcp_server.py

# HTTP/SSE on port 8000
python3 src/mcp_server.py --http 8000

# Both simultaneously
python3 src/mcp_server.py --stdio --http 8000
```

HTTP mode requires `fastapi` and `uvicorn`:

```bash
pip install fastapi uvicorn
```

---

## MCP Client Configuration

### Claude Desktop / Claude Code

Add to `~/.claude/claude_desktop_config.json` (Desktop) or `~/.claude/config.json` (Code):

```json
{
  "mcpServers": {
    "therefore": {
      "command": "python3",
      "args": ["/path/to/therefore-mcp/src/mcp_server.py"],
      "env": {
        "THEREFORE_ENV_PATH": "/path/to/therefore-mcp/.env.local"
      }
    }
  }
}
```

### Cursor

Add to `.cursor/mcp.json` in your project root:

```json
{
  "mcpServers": {
    "therefore": {
      "command": "python3",
      "args": ["/path/to/therefore-mcp/src/mcp_server.py"],
      "env": {
        "THEREFORE_ENV_PATH": "/path/to/therefore-mcp/.env.local"
      }
    }
  }
}
```

### VS Code (Copilot)

Add to `.vscode/mcp.json` in your workspace:

```json
{
  "servers": {
    "therefore": {
      "type": "stdio",
      "command": "python3",
      "args": ["/path/to/therefore-mcp/src/mcp_server.py"],
      "env": {
        "THEREFORE_ENV_PATH": "/path/to/therefore-mcp/.env.local"
      }
    }
  }
}
```

### Goose (HTTP transport)

Start the server with `--http 8000`, then configure:

```yaml
# ~/.config/goose/config.yaml
extensions:
  therefore:
    type: sse
    uri: http://localhost:8000/mcp
    headers:
      Authorization: "Bearer your-secret-token"   # if THEREFORE_MCP_AUTH_TOKEN is set
```

---

## Tools

`ask_therefore_expert` and `therefore_connect` take direct parameters (no `operation`).
The 8 grouped tools below each take a mandatory `operation` string selecting the
sub-operation, plus a `tenant` string (optional on most calls — inferred/defaulted per
the tenant resolution order described later in this doc).

| Tool | Operations |
|------|-----------|
| `ask_therefore_expert` | Smart router — describes the right tool, operation, and parameters for a task. Understands connect-flavored questions too, even with zero tenants configured. |
| `therefore_connect` | Register a tenant/login at runtime — no `.env.local` edit or server restart needed. See [Configuration](#configuration). |
| `therefore_system` | Connection info, domain info, server version, JWT/ADFS token exchange |
| `therefore_categories` | Get info, resolve by name, list fields, resolve field, referenced table info and query, generate config XML |
| `therefore_documents` | Get, create, update, update index data, add streams, delete, check out/in, undo checkout, get versions/stream/comments/history/properties, add/edit comment |
| `therefore_query` | Synchronous query, async query, multi-category query, full-text search, users query, workflow instances query, referenced table query |
| `therefore_workflow` | Get tasks, get instance, claim, release, complete, delegate, get history, start workflow, get process list and definition, Cases (get case definition, create/get case, get case documents/history) |
| `therefore_users` | Get connected user, resolve, list, create, set/change password, move license, portal user management, get/set settings, delete |
| `therefore_keywords` | List dictionaries, get keywords, add/update/delete keyword, get keyword info |
| `therefore_knowledge` | Search API docs, get workflow guides, field type info, common patterns, known quirks, list resources, fetch live API help |

**Note:** `therefore_documents`' `get` operation includes attachment metadata
(`StreamsInfo` — filenames/StreamNos, not content) by default; use `get_stream` /
`get_stream_raw` with a `StreamNo` from there to fetch the actual file content. There is
no `copy` operation — the underlying `CopyDocument` endpoint doesn't exist on the live
Therefore server.

---

## Authentication

Three authentication methods are supported per tenant:

### Basic Auth (most common)

```env
THEREFORE_MYTENANT_AUTH_METHOD=Basic
THEREFORE_MYTENANT_USERNAME=your.username
THEREFORE_MYTENANT_PASSWORD=your-password
```

### Bearer Token

```env
THEREFORE_MYTENANT_AUTH_METHOD=Bearer
THEREFORE_MYTENANT_PASSWORD=your-bearer-token
```

### S2S — Trusted Token Issuer

For service-to-service deployments where Therefore credentials are managed by a central auth provider rather than stored in env files:

```env
THEREFORE_MYTENANT_AUTH_METHOD=S2S
THEREFORE_MYTENANT_AUTH_PROVIDER_URL=https://your-auth-provider/
THEREFORE_MYTENANT_BRIDGE_API_KEY=optional-api-key
THEREFORE_MYTENANT_USER_MAPPING=service-account-name
```

The server calls `POST {AUTH_PROVIDER_URL}/issue-token` with `{"tenant": "...", "user_hint": "..."}` and caches the returned JWT per tenant. See `services/auth-provider/` for a reference implementation.

### ADFS / Entra ID

Use `therefore_system` → `operation: get_connection_token_from_adfs` to exchange a pre-obtained Entra ID token for a Therefore JWT. Requires a v1 ID token (RS256, ver:1.0 with `upn` claim). See `AUTHENTICATION_README.md` for the full flow and `scripts/` for helper scripts.

---

## Multi-Tenant Setup

Define multiple tenants in `.env.local`:

```env
THEREFORE_TENANTS=acme,contoso
THEREFORE_DEFAULT_TENANT=acme

THEREFORE_ACME_BASE_URL=https://acme.thereforeonline.com/theservice/v0001/restun
THEREFORE_ACME_AUTH_METHOD=Basic
THEREFORE_ACME_USERNAME=svc.account
THEREFORE_ACME_PASSWORD=secret
THEREFORE_ACME_TENANTNAME=acme

THEREFORE_CONTOSO_BASE_URL=https://contoso.thereforeonline.com/theservice/v0001/restun
THEREFORE_CONTOSO_AUTH_METHOD=Basic
THEREFORE_CONTOSO_USERNAME=svc.account
THEREFORE_CONTOSO_PASSWORD=secret
THEREFORE_CONTOSO_TENANTNAME=contoso
```

**Tenant resolution order** (per request):

1. Explicit `tenant` parameter in the tool call
2. Inferred from argument content (names, hints)
3. Smart default — if the caller has access to only one tenant, use it automatically
4. Sticky — fall back to the last-used tenant

### Client Access Control (HTTP mode)

When running in HTTP mode, create `config/clients.json` to restrict which tenants each API key can access:

```json
{
  "api-key-for-team-a": ["acme"],
  "api-key-for-team-b": ["acme", "contoso"]
}
```

Clients authenticate with `Authorization: Bearer <api-key>`. All access is recorded in the audit log.

---

## Docker

```bash
# stdio (default)
docker run --rm -i --env-file /path/to/.env.local fybre/therefore-mcp

# HTTP on port 8000
docker run --rm -p 8000:8000 --env-file /path/to/.env.local fybre/therefore-mcp --http 8000

# Build locally
docker build -t therefore-mcp .
docker run --rm -i --env-file /path/to/.env.local therefore-mcp
```

### Docker Compose

```bash
# HTTP mode (production, detached)
docker compose --profile http up -d

# HTTP + optional S2S auth provider
docker compose --profile http --profile auth up -d

# stdio mode
docker compose --profile stdio up
```

Create `.env.local` in the project root before starting.

---

## Environment Variables

### Per-tenant

| Variable | Description |
|----------|-------------|
| `THEREFORE_TENANTS` | Comma-separated list of tenant keys |
| `THEREFORE_DEFAULT_TENANT` | Default tenant when multiple are configured |
| `THEREFORE_<T>_BASE_URL` | Therefore WebAPI base URL |
| `THEREFORE_<T>_AUTH_METHOD` | `Basic`, `Bearer`, or `S2S` |
| `THEREFORE_<T>_USERNAME` | Username (Basic auth) |
| `THEREFORE_<T>_PASSWORD` | Password or bearer token |
| `THEREFORE_<T>_TENANTNAME` | TenantName header value (defaults to URL subdomain) |
| `THEREFORE_<T>_AUTH_PROVIDER_URL` | Token issuer URL (S2S auth) |
| `THEREFORE_<T>_BRIDGE_API_KEY` | API key for token issuer (S2S auth) |
| `THEREFORE_<T>_USER_MAPPING` | User context for S2S token requests |
| `THEREFORE_<T>_ASSIGNEE_ALIASES` | Comma-separated workflow assignee aliases |
| `THEREFORE_<T>_USER_GROUPS` | Comma-separated user group filters |

### Server

| Variable | Description | Default |
|----------|-------------|---------|
| `THEREFORE_ENV_PATH` | Path to `.env.local` | Project root |
| `THEREFORE_MCP_AUTH_TOKEN` | Global Bearer token for HTTP endpoints | None (open) |
| `THEREFORE_CACHE_DIR` | Cache directory | `./cache` |
| `THEREFORE_DEBUG` | Verbose request/response logging (`1`/`true`) | Disabled |
| `THEREFORE_LOCAL_TZ` | Timezone for date calculations | System default |
| `THEREFORE_WORKFLOW_TIMEOUT_SECONDS` | Workflow call timeout | `240` |
| `THEREFORE_WORKFLOW_MAX_ROWS` | Max workflow query rows | `10000` |
| `THEREFORE_WORKFLOW_RETRY_TIMEOUT_SECONDS` | Retry wait time | `480` |
| `THEREFORE_WORKFLOW_RETRY_COUNT` | Retry attempts | `1` |

---

## Architecture

```
src/
  mcp_server.py         # MCP server — router, therefore_connect, 8 grouped tools, operation registry, tenant
  therefore_client.py   # HTTP client — auth, retries, config building, all API methods
  knowledge_tools.py    # Knowledge base utilities for therefore_knowledge tool
config/
  clients.json          # Client API key → tenant access list (HTTP mode)
  clients.json.example  # Template
services/
  auth-provider/        # Reference S2S token issuer implementation
tools/
  config_generator/     # Delta XML generator for Therefore category creation
scripts/
  validate_therefore_api.py      # API connectivity validation
  get_entra_token_device_code.py # Entra ID v1 device code flow
  test_entra_jwt_exchange.py     # Test ADFS/Entra → Therefore JWT exchange
docs/
  therefore-api-complete-guide.md  # Comprehensive API reference
  PYTHON_EXAMPLES.md               # Python code examples
  PYTHON_QUICK_REFERENCE.md        # Quick field type and pattern reference
  knowledge-base.json              # Structured API knowledge (used by therefore_knowledge tool)
```

### Keeping Knowledge in Sync

The server's local knowledge base (`docs/knowledge-base.json`) and the extended markdown
documentation (`docs/PYTHON_EXAMPLES.md`, `docs/PYTHON_QUICK_REFERENCE.md`,
`docs/therefore-api-complete-guide.md`) are two separate layers that should stay consistent:

- `knowledge-base.json` — structured JSON queried by the `therefore_knowledge` MCP tool at runtime
- Markdown docs — referenced by the [therefore-api skill](https://github.com/Fybre/therefore-api-skill) via GitHub raw URLs

When you discover a new API quirk, update a workflow, or correct a pattern, **update both**:
1. Add/edit the relevant entry in `docs/knowledge-base.json`
2. Update the corresponding section in the relevant markdown doc

The `therefore_knowledge` search tool will direct AI assistants to the GitHub docs as a
fallback if the local knowledge base does not have a satisfactory answer.

### Key Design Decisions

- **Grouped tools:** 9 domain tools with an `operation` parameter, rather than hundreds of individual tools. Reduces MCP tool list noise while keeping full coverage.
- **Multi-tenant with access control:** Per-client API key → tenant allowlist enforced at every tool call. All calls are audit-logged.
- **Fuzzy matching:** Category and field names resolved with `difflib.SequenceMatcher`. Returns a `needs_confirmation` flag when confidence is below threshold.
- **Web-client document flow:** Document creation follows the four-step pipeline used by the Therefore web client: `GetCategoryInfo → PreprocessIndexData → EvaluateConditionalProperties → CreateDocument`.
- **Async query batching:** `execute_async_single_query_all` auto-fetches all pages and always releases the server session in a `finally` block.
- **Caching:** Category, field, and keyword metadata cached with 300s TTL, persisted to `cache/` per tenant.

---

## Debugging

Set `THEREFORE_DEBUG=1` for verbose request/response logging to stderr:

```bash
THEREFORE_DEBUG=1 python3 src/mcp_server.py
```

Output example:

```
[THEREFORE] POST https://tenant.thereforeonline.com/.../GetCategoryInfo (142 bytes)
[THEREFORE]  <- 200 OK (3854 bytes, 237ms)
[THEREFORE] POST https://tenant.thereforeonline.com/.../ExecuteAsyncSingleQuery (285 bytes)
[THEREFORE]  <- 200 OK (1204 bytes, 89ms)
```

---

## License

[MIT](LICENSE)

## Attribution

Built by [Fybre](https://github.com/fybre) with assistance from [Claude Code](https://claude.ai/code) (Anthropic).
