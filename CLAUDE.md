# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**therefore-mcp** is a Python MCP (Model Context Protocol) server that bridges AI assistants to the Therefore™ document management system via its WebAPI. It exposes **9 grouped tools** (each with an `operation` parameter) covering document CRUD, querying, workflow management, keyword dictionaries, user/system operations, document checkout/checkin, comments, case management, and comprehensive user administration. Communication uses JSON-RPC 2.0 over stdin/stdout (stdio) or HTTP+SSE.

## Running the Server

```bash
# Stdio mode (default — used by MCP clients like Claude Desktop, Codex)
python3 src/mcp_server.py

# HTTP+SSE mode on port 8000
python3 src/mcp_server.py --http 8000

# Both simultaneously
python3 src/mcp_server.py --stdio --http 8000

# Override env file path
export THEREFORE_ENV_PATH=/path/to/custom/.env.local
python3 src/mcp_server.py
```

The server is registered in Codex at `~/.codex/config.toml` as the `therefore` MCP server. Restart Codex after code changes.

## Utility Scripts

```bash
python3 scripts/validate_therefore_api.py    # Validate API connectivity, generates docs/notes/validation_report.md
python3 scripts/build_therefore_specs.py     # Build constant mappings from extracted docs
python3 scripts/extract_therefore_docs.py    # Scrape Therefore online documentation
```

## Config Generator Tool

```bash
python3 tools/config_generator/generate.py \
  --baseline TheConfiguration.xml \
  --description description.txt \
  --output delta.xml
```

Generates delta XML for Therefore category creation from natural language or YAML/JSON specs.

## Architecture

### Core Files

- **`src/mcp_server.py`** (~6,600 lines) — The MCP server. Contains `MCPServer` class with 9 grouped tool handlers, operation registry, multi-tenant resolution, client access control, audit logging, caching, and JSON-RPC message loop. Also serves HTTP+SSE transport.
- **`src/therefore_client.py`** (~1,800 lines) — HTTP client (`ThereforeClient`) for Therefore™ WebAPI. Handles auth (Basic, Bearer, S2S), timeouts, retries, multi-tenant config building, and stream utilities.
- **`src/knowledge_tools.py`** (~320 lines) — Knowledge base utilities for exposing Therefore API guidance to AI assistants via the `therefore_knowledge` tool.

### Tool Architecture

The server exposes **9 grouped tools** via an operation registry (`OPERATION_REGISTRY` in `mcp_server.py`). Each tool takes a mandatory `operation` string and a mandatory `tenant` string, plus optional operation-specific fields:

| Tool | Operations |
|------|-----------|
| `ask_therefore_expert` | Smart router — suggests the right tool/operation |
| `therefore_system` | system info, connection, domain info, JWT auth |
| `therefore_categories` | get_info, resolve, list_fields, resolve_field, get_referenced_table_info, query_referenced_table, generate_config |
| `therefore_documents` | get, create, update, update_index_data, add_streams, delete, copy, check_out, check_in, undo_check_out, get_versions, get_stream, get_comments, add_comment, get_history, get_checkout_status, get_document_properties |
| `therefore_query` | single, async_single, async_multi, full_text, users, workflow_instances, referenced_table |
| `therefore_workflow` | get_tasks, get_instance, claim, release, complete, delegate, get_history, start, get_process_list, get_workflow_definition |
| `therefore_users` | get_connected, resolve, list, create, set_password, change_password, move_license, set_portal_user, get_settings, set_settings, delete |
| `therefore_keywords` | list_dictionaries, get_keywords, add_keyword, update_keyword, delete_keyword, get_keyword_info |
| `therefore_knowledge` | search, get_workflow, get_field_type_info, get_common_pattern, get_api_quirks, list, get_api_help |

### Key Architectural Patterns

**Multi-Tenant Support:** Configured via `THEREFORE_TENANTS` env var with per-tenant credentials (`THEREFORE_<TENANT>_BASE_URL`, etc.). Tenant resolution priority: explicit `tenant` param → inferred from args → smart default (if client has access to only 1 tenant) → sticky last-used tenant.

**Client Access Control:** `config/clients.json` maps API keys (HTTP Bearer tokens) to allowed tenant lists. Tool calls validate tenant access in `_resolve_tenant()`.

**Audit Logging:** Every tool call is logged via `_audit_log()` — timestamp, masked client key, IP, tenant, tool, args (sensitive fields redacted).

**Caching:** Category, field, and keyword dictionary data are cached with 300s TTL and persisted to `cache/category_cache_{tenant}.json`, `cache/field_cache_{tenant}.json`, and `cache/keyword_dictionary_cache_{tenant}.json`.

**Fuzzy Matching:** `resolve_category` and `resolve_field` use `difflib.SequenceMatcher` for name resolution. Returns `needs_confirmation` flag when confidence is below threshold.

**Document Creation (Web-Client Flow):** Four-step pipeline: `GetCategoryInfo → PreprocessIndexData → EvaluateConditionalProperties → CreateDocument`.

**Async Query Batching:** `execute_async_single_query_all` and `execute_async_multi_query_all` auto-fetch all result batches and release the query session in `finally` blocks.

**Keyword Fields:** Expect keyword IDs (KeywordNos), not string values. Using strings can cause conversion errors.

### Directory Layout

- `src/` — Server source code
- `cache/` — Runtime caches (per-tenant JSON files, 300s TTL)
- `config/` — `clients.json` (client→tenant access control), `clients.json.example`
- `docs/specs/` — API specifications, tool definitions JSON, constants
- `docs/export/` — Scraped Therefore documentation and code tables
- `docs/notes/` — Validation reports, debug logs
- `docs/reference/user/` — User environment config (`.env.local`)
- `scripts/` — Dev/validation scripts
- `tools/config_generator/` — Category configuration XML generator

## Dependencies

Pure Python standard library — no external packages required for core server operation. Uses `urllib.request` for HTTP, `json` for serialization, `difflib` for fuzzy matching, `concurrent.futures` for async queries, `zoneinfo` (Python 3.9+) for timezone handling.

Optional HTTP transport requires `fastapi` and `uvicorn` (listed in `requirements.txt`).

## Environment Variables

### Per-tenant credentials
```bash
THEREFORE_TENANTS=tenant_a,tenant_b          # Comma-separated tenant list
THEREFORE_DEFAULT_TENANT=tenant_a            # Default when multiple configured
THEREFORE_<TENANT>_BASE_URL=https://...      # Required per tenant
THEREFORE_<TENANT>_AUTH_METHOD=Basic         # Basic | Bearer | S2S
THEREFORE_<TENANT>_USERNAME=...
THEREFORE_<TENANT>_PASSWORD=...
THEREFORE_<TENANT>_TENANTNAME=...            # TenantName header override
THEREFORE_<TENANT>_ASSIGNEE_ALIASES=...      # Task filtering aliases
THEREFORE_<TENANT>_USER_GROUPS=...           # User group filters
```

### S2S / Trusted Token Issuer auth
```bash
THEREFORE_<TENANT>_AUTH_METHOD=S2S
THEREFORE_<TENANT>_AUTH_PROVIDER_URL=https://...   # Token issuer endpoint
THEREFORE_<TENANT>_BRIDGE_API_KEY=...              # Optional API key for issuer
THEREFORE_<TENANT>_USER_MAPPING=...                # User context mapping
```

### Server settings
```bash
THEREFORE_ENV_PATH=...                       # Path to .env.local (default: project root)
THEREFORE_MCP_AUTH_TOKEN=...                 # Global HTTP Bearer token (allows all tenants)
THEREFORE_CACHE_DIR=./cache                  # Cache directory
THEREFORE_DEBUG=1                            # Enable debug output
THEREFORE_LOCAL_TZ=Australia/Sydney          # Timezone for date calculations
THEREFORE_WORKFLOW_TIMEOUT_SECONDS=240       # Workflow call timeout
THEREFORE_WORKFLOW_MAX_ROWS=10000            # Max workflow query rows
THEREFORE_WORKFLOW_RETRY_TIMEOUT_SECONDS=480
THEREFORE_WORKFLOW_RETRY_COUNT=1
```

## Authentication Methods

### Basic Auth (most common)
`Authorization: Basic base64(username:password)` on every request.

### Bearer Token
Pre-issued token: `Authorization: Bearer <token>`. Set `AUTH_METHOD=Bearer`, put token in `PASSWORD` field.

### S2S (Trusted Token Issuer)
Fetches a signed JWT from a centralized auth provider (`POST {auth_provider_url}/issue-token`). Token is cached per tenant. Enables SSO/service-account flows without embedding Therefore credentials in config.

### ADFS/Entra ID Exchange
`therefore_system` → `operation: get_connection_token_from_adfs` exchanges a pre-obtained Entra ID token for a Therefore JWT. Requires v1 ID token (RS256, ver:1.0, `upn` claim). See `AUTHENTICATION_README.md` for details.

## Writing Python Code for Therefore

When writing Python scripts that use the Therefore API directly (not via MCP tools):

### Essential Files
- **`src/therefore_client.py`** - ThereforeClient class with all API methods
- **`docs/PYTHON_QUICK_REFERENCE.md`** - Condensed patterns and field types (~850 tokens)
- **`docs/PYTHON_EXAMPLES.md`** - Complete examples including XML processing (~3K tokens)

### Instantiating ThereforeClient
```python
from therefore_client import ThereforeClient, ThereforeConfig

cfg = ThereforeConfig(
    base_url='https://tenant.thereforeonline.com/theservice/v0001/restun',
    auth_method='Basic',
    username='user',
    password='pass',
    tenant_name='tenant',   # TenantName header
)
client = ThereforeClient(cfg)
```

### Quick Field Type Reference
| Type | TypeNo | IndexData | Example |
|------|--------|-----------|---------|
| String | 0 | StringIndexData | `{"Value": {"StringIndexData": {"Value": "text"}}}` |
| Integer | 1 | IntIndexData | `{"Value": {"IntIndexData": {"Value": 42}}}` |
| Date | 2 | DateIndexData | `{"Value": {"DateIndexData": {"Value": "2024-01-15"}}}` |
| Keyword | 6 | KeywordIndexData | `{"Value": {"KeywordIndexData": {"KeywordNo": 42}}}` ⚠️ Use ID not name! |

## Therefore API Knowledge Resources

For detailed guidance on Therefore WebAPI usage, consult these resources:

- **`docs/therefore-api-complete-guide.md`** — Comprehensive guide with workflows, examples, field mappings, and troubleshooting
- **`docs/knowledge-base.json`** — Machine-readable knowledge base with structured workflows, patterns, and quirks
- **`src/knowledge_tools.py`** — Python utilities for programmatic knowledge access
- **`docs/KNOWLEDGE_TOOLS_USAGE.md`** — Guide for using the MCP knowledge tools

### MCP Knowledge Tools (Available via Natural Language)

The server exposes knowledge tools via `therefore_knowledge`:

1. **`search`** - Search knowledge base with natural language queries
2. **`get_workflow`** - Get step-by-step workflow guides
3. **`get_field_type_info`** - Get field type structures and examples
4. **`get_common_pattern`** - Get coding patterns with examples
5. **`get_api_quirks`** - Get known issues and workarounds
6. **`list`** - List all available knowledge resources
7. **`get_api_help`** - Fetch live API documentation from Therefore server

### Key Learning Resources

**Common Workflows:**
- Query documents with filters and iterate results
- Create documents using web-client flow
- Work with table fields and structured data
- Map query IndexValues to column names

**Field Types Reference:**
- 0=StringField, 1=IntField, 2=DateField, 3=DateTimeField, 5=MoneyField, 6=KeywordField, 9=TableField
- Each type has specific IndexData structure (StringIndexData, IntIndexData, TableData, etc.)

**Critical Patterns:**
- `IndexValues[i]` maps to `Columns[i].ColName` (positional array mapping)
- Always release query sessions in `finally` blocks
- Use `GetDocumentIndexData` for structured table data (not query IndexValues)
- Keyword fields require KeywordNos, not string values

## Known Quirks

- `DeleteDictionaryKeyword` returns success but does not actually remove in-use keywords.
- `execute_single_query` auto-switches to async multi-query when multiple category numbers are supplied.
- Conversion enums in `update_document` and `add_streams_to_document` accept both string names and numeric values (e.g., `ConvertTo: "SinglePDF"` or `2`).
- Non-Therefore user accounts (AD/LDAP) return `UserId: 0` from user resolution APIs.
- Table data in query `IndexValues` is concatenated strings; use `GetDocumentIndexData` for structured access.
- Wildcard character in query conditions is `*` not `%` — using `%` returns 0 results silently.
- `ExecuteAsyncSingleQuery` returns `QueryId` (lowercase d); `GetNextSingleQueryRows` and `ReleaseSingleQuery` expect `QueryID` (uppercase D).
