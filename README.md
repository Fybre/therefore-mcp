# therefore-mcp

A Python [MCP](https://modelcontextprotocol.io/) server that connects AI assistants to the [Therefore](https://therefore.net/) document management system via its WebAPI.

Exposes 60+ tools covering document CRUD, querying, workflow management, keyword dictionaries, user administration, and system operations. Zero external dependencies -- pure Python standard library.

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
THEREFORE_MYTENANT_ALLOW_WRITES=true
```

Two authentication methods are supported:

- **Basic** -- uses `USERNAME` and `PASSWORD` (default)
- **Bearer** -- uses `PASSWORD` as the token (set `AUTH_METHOD=Bearer`)

Multiple tenants are supported -- add additional `THEREFORE_<TENANT>_*` blocks and list them in `THEREFORE_TENANTS`.

### Running

```bash
python3 src/mcp_server.py
```

The server communicates over stdin/stdout using JSON-RPC (MCP protocol).

### Docker

```bash
# Copy your .env.local to the project root, then:
docker compose up --build
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
| `THEREFORE_<TENANT>_ALLOW_WRITES` | Enable write operations | `false` |

## License

[MIT](LICENSE)

## Attribution

Built by [Fybre (Craig)](https://github.com/fybre) with assistance from [Claude Code](https://claude.ai/code) (Anthropic).
