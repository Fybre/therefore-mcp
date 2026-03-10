# therefore-mcp

A Python [MCP](https://modelcontextprotocol.io/) server that connects AI assistants to the [Therefore](https://therefore.net/) document management system via its WebAPI.

Exposes 60+ tools covering document CRUD, querying, workflow management, keyword dictionaries, user administration, and system operations. Supports stdio and HTTP/SSE transports. Zero external dependencies for stdio mode -- pure Python standard library. HTTP mode requires `fastapi` and `uvicorn`.

## Quick Start

### Prerequisites

- Python 3.9+
- A Therefore instance with WebAPI access
- An MCP-compatible client (Claude Code, Claude Desktop, Codex, etc.)

### Configuration

Create a `.env.local` in the project root (or set `THEREFORE_ENV_PATH`):

```env
THEREFORE_TENANTS=mytenant
THEREFORE_DEFAULT_TENANT=mytenant

THEREFORE_MYTENANT_BASE_URL=https://mytenant.thereforeonline.com/theservice/v0001/restun
THEREFORE_MYTENANT_AUTH_METHOD=Basic
THEREFORE_MYTENANT_USERNAME=your.username
THEREFORE_MYTENANT_PASSWORD=your-password
THEREFORE_MYTENANT_TENANTNAME=mytenant
```

Two authentication methods are supported:

- **Basic** -- uses `USERNAME` and `PASSWORD` (default)
- **Bearer** -- uses `PASSWORD` as the token (set `AUTH_METHOD=Bearer`)

Multiple tenants are supported -- add additional `THEREFORE_<TENANT>_*` blocks and list them in `THEREFORE_TENANTS`.

### Running

The server supports three transport modes:

```bash
# stdio only (default) -- for MCP clients
python3 src/mcp_server.py

# HTTP only -- JSON-RPC over HTTP + SSE transport on port 8000
python3 src/mcp_server.py --http 8000

# Both -- stdio for MCP client, HTTP/SSE on port 8000 for other consumers
python3 src/mcp_server.py --stdio --http 8000
```

HTTP mode requires `fastapi` and `uvicorn` (`pip install fastapi uvicorn`). The HTTP server exposes three transports:

| Endpoint | Transport | Clients |
|----------|-----------|---------|
| `POST /mcp` | Streamable HTTP | Goose, newer MCP clients |
| `GET /sse` + `POST /messages` | SSE | Older MCP clients with `"url"` config |
| `POST /` | Direct JSON-RPC | curl, custom integrations |

Additionally:
- `GET /health` -- health check
- `DELETE /mcp` -- terminate a Streamable HTTP session

### Docker

```bash
# stdio only (default):
docker run --rm -i --env-file /path/to/.env.local fybre/therefore-mcp

# HTTP only:
docker run --rm -p 8000:8000 --env-file /path/to/.env.local fybre/therefore-mcp --http 8000

# Both stdio + HTTP:
docker run --rm -i -p 8000:8000 --env-file /path/to/.env.local fybre/therefore-mcp --stdio --http 8000

# Build locally:
docker build -t therefore-mcp /path/to/therefore-mcp
docker run --rm -i --env-file /path/to/.env.local therefore-mcp

# Build multi-platform and push to registry:
docker buildx create --name multiplatform --driver docker-container --use  # first time only
docker buildx build --platform linux/amd64,linux/arm64 -t fybre/therefore-mcp --push .
```

### Docker Compose

The included `docker-compose.yml` uses profiles to select the transport mode:

```bash
# HTTP mode (production) -- runs detached with auto-restart
docker compose --profile http up -d

# stdio mode -- for MCP clients that manage the process
docker compose --profile stdio up
```

Create a `.env.local` in the project root before starting (see [Configuration](#configuration) above).

## MCP Host Configuration

Below are example configurations for popular MCP-compatible clients. Replace `/path/to/therefore-mcp` with the actual path to your clone.

All examples below use Python directly. To run via Docker instead, substitute the command and args in any example:

```json
// Docker Hub -- stdio only (recommended for MCP clients):
"command": "docker",
"args": ["run", "--rm", "-i", "--env-file", "/path/to/.env.local", "fybre/therefore-mcp"]

// Docker Hub -- stdio + HTTP on port 8000:
"command": "docker",
"args": ["run", "--rm", "-i", "-p", "8000:8000", "--env-file", "/path/to/.env.local", "fybre/therefore-mcp", "--stdio", "--http", "8000"]
```

### Claude Code (CLI)

```bash
claude mcp add therefore -- python3 /path/to/therefore-mcp/src/mcp_server.py
```

Or add to `.claude/settings.json`:

```json
{
  "mcpServers": {
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

### Claude Desktop / Claude for Mac

Add to `claude_desktop_config.json`:

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

With Docker (stdio + HTTP):

```json
{
  "mcpServers": {
    "therefore": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i", "-p", "8000:8000",
        "--env-file", "/path/to/.env.local",
        "fybre/therefore-mcp",
        "--stdio", "--http", "8000"
      ]
    }
  }
}
```

> **Note:** Claude Desktop only supports stdio transport (`"command"`). The `--http` flag adds an HTTP/SSE endpoint on the side for other consumers but is not required.

### Goose

Goose uses Streamable HTTP transport. Start the server with `--http` (Docker or Python), then configure:

```yaml
# ~/.config/goose/config.yaml
extensions:
  therefore:
    type: sse
    uri: http://localhost:8000/mcp
```

If you have `THEREFORE_MCP_AUTH_TOKEN` set, add the token as a header:

```yaml
extensions:
  therefore:
    type: sse
    uri: http://localhost:8000/mcp
    headers:
      Authorization: "Bearer your-secret-token"
```

Or for a remote server:

```yaml
extensions:
  therefore:
    type: sse
    uri: http://192.168.x.x:8000/mcp
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

### Kimi Code (Moonshot)

Add to your Kimi Code MCP settings:

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

## Tools

Tools are grouped into the following areas:

| Area | Examples |
|------|----------|
| **Categories & Fields** | `resolve_category`, `list_category_fields`, `resolve_field`, `get_categories_tree`, `get_category_info` |
| **Documents** | `get_document`, `create_document`, `update_document`, `delete_document`, `get_converted_doc_streams` |
| **Querying** | `execute_single_query`, `execute_async_single_query`, `execute_full_text_query`, `execute_statistics_query` |
| **Workflows** | `get_my_workflow_tasks`, `get_workflow_instance`, `get_workflow_history`, `get_workflow_process` |
| **Keywords** | `get_keywords_by_field_no`, `add_dictionary_keyword`, `update_dictionary_keyword`, `validate_keywords` |
| **Users & System** | `execute_users_query`, `get_connected_user`, `get_domain_info`, `get_logfiles`, `get_login_history` |
| **Config Generation** | `generate_category_config` -- create category definitions from natural language or structured specs |

## Architecture

```
src/
  mcp_server.py        # MCP server -- tool definitions, handlers, caching, JSON-RPC loop
  therefore_client.py   # HTTP client for Therefore WebAPI (auth, retries, multi-tenant)
tools/
  config_generator/     # Delta XML generator for category creation
scripts/
  validate_therefore_api.py   # API connectivity validation
  build_therefore_specs.py    # Build constant mappings from docs
  extract_therefore_docs.py   # Scrape Therefore online documentation
docs/
  specs/       # API specifications and constants
  export/      # Scraped Therefore documentation
  notes/       # Runtime caches and debug output
```

### Key Design Decisions

- **Multi-tenant:** Tenant selection is "sticky" -- once specified, it becomes the session default. Tenants can also be inferred from hints in tool arguments.
- **Fuzzy matching:** Category and field names are resolved using `difflib.SequenceMatcher` with a configurable confidence threshold.
- **Web-client document flow:** Document creation follows the four-step pipeline: `GetCategoryInfo -> PreprocessIndexData -> EvaluateConditionalProperties -> CreateDocument`.
- **Caching:** Category, field, and keyword dictionary metadata is cached with a 300-second TTL.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `THEREFORE_ENV_PATH` | Path to `.env.local` config | Project root |
| `THEREFORE_TENANTS` | Comma-separated tenant keys | -- |
| `THEREFORE_DEFAULT_TENANT` | Default tenant | First in list |
| `THEREFORE_WORKFLOW_TIMEOUT_SECONDS` | Workflow call timeout | `240` |
| `THEREFORE_WORKFLOW_MAX_ROWS` | Max workflow query rows | `10000` |
| `THEREFORE_LOCAL_TZ` | Local timezone | System default |
| `THEREFORE_MCP_AUTH_TOKEN` | Bearer token for HTTP endpoints (optional) | None (no auth) |
| `THEREFORE_DEBUG` | Enable debug logging to stderr (`1`, `true`, or `yes`) | Disabled |

## Debugging

Set `THEREFORE_DEBUG=1` to enable verbose request/response logging. All debug output goes to **stderr** so it won't interfere with the JSON-RPC protocol on stdout.

```bash
# In your .env.local
THEREFORE_DEBUG=true
```

Or pass it directly:

```bash
THEREFORE_DEBUG=1 python3 src/mcp_server.py
```

When enabled, you'll see output like:

```
[THEREFORE] POST https://tenant.thereforeonline.com/theservice/v0001/restun/GetCategoryInfo (142 bytes)
[THEREFORE]  <- 200 OK (3854 bytes, 237ms)
[THEREFORE] POST https://tenant.thereforeonline.com/theservice/v0001/restun/ExecuteSingleQuery (285 bytes)
[THEREFORE]  <- 401 'Invalid credentials' (94 bytes, 58ms)
```

Each log line includes the HTTP method, URL, response status, body size, and round-trip time. Redirect hops are also logged when they occur. This is useful for diagnosing authentication issues, timeouts, and unexpected API responses.

## License

[MIT](LICENSE)

## Attribution

Built by [Fybre (Craig)](https://github.com/fybre) with assistance from [Claude Code](https://claude.ai/code) (Anthropic).
