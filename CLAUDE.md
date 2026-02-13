# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**therefore-mcp** is a Python MCP (Model Context Protocol) server that bridges AI assistants to the Therefore™ document management system via its WebAPI. It exposes 87+ tools for document CRUD, querying, workflow management, keyword dictionaries, user/system operations, document checkout/checkin, comments, case management, and comprehensive user administration (create, password management, licensing, portal users, settings). Communication uses LSP-style JSON-RPC over stdin/stdout.

## Running the Server

```bash
# Run (uses .env.local in project root by default)
python3 src/mcp_server.py

# Or override with a custom path
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

- **`src/mcp_server.py`** (~5,700 lines) — The MCP server. Contains `MCPServer` class with all 87 tool handlers, caching, tenant management, and JSON-RPC message loop.
- **`src/therefore_client.py`** (~1,400 lines) — HTTP client (`ThereforeClient`) for Therefore™ WebAPI. Handles auth, timeouts, retries, multi-tenant config building, and stream utilities.

### Key Architectural Patterns

**Multi-Tenant Support:** Configured via `THEREFORE_TENANTS` env var with per-tenant credentials (`THEREFORE_<TENANT>_BASE_URL`, etc.). Tenant selection is "sticky" — once a tool call specifies a tenant, it becomes the default. The server also infers tenant from `tenant_hint` and string arguments.

**Caching:** Category, field, and keyword dictionary data are cached with 300s TTL and persisted to `docs/notes/category_cache_{tenant}.json`, `field_cache_{tenant}.json`, and `keyword_dictionary_cache_{tenant}.json`.

**Fuzzy Matching:** `resolve_category` and `resolve_field` use `difflib.SequenceMatcher` for name resolution. Returns `needs_confirmation` flag when confidence is below threshold.

**Document Creation (Web-Client Flow):** Four-step pipeline: `GetCategoryInfo → PreprocessIndexData → EvaluateConditionalProperties → CreateDocument`.

**Async Query Batching:** `execute_async_single_query` and `execute_async_multi_query` auto-fetch all result batches and release the query session in `finally` blocks.

**Keyword Fields:** Expect keyword IDs (KeywordNos), not string values. Using strings can cause conversion errors.

### Directory Layout

- `docs/specs/` — API specifications, tool definitions JSON, constants
- `docs/export/` — Scraped Therefore documentation and code tables
- `docs/notes/` — Runtime caches, validation reports, debug logs
- `docs/reference/user/` — User environment config (`.env.local`)
- `scripts/` — Dev/validation scripts
- `tools/config_generator/` — Category configuration XML generator

## Dependencies

Pure Python standard library — no external packages required for core server operation. Uses `urllib.request` for HTTP, `json` for serialization, `difflib` for fuzzy matching, `concurrent.futures` for async queries, `zoneinfo` (Python 3.9+) for timezone handling.

## Environment Variables

Key env vars beyond per-tenant credentials:
- `THEREFORE_ENV_PATH` — Path to `.env.local` config file
- `THEREFORE_WORKFLOW_TIMEOUT_SECONDS` — Workflow call timeout (default 240)
- `THEREFORE_WORKFLOW_MAX_ROWS` — Max workflow query rows (default 10000)
- `THEREFORE_WORKFLOW_RETRY_TIMEOUT_SECONDS` / `THEREFORE_WORKFLOW_RETRY_COUNT` — Retry settings
- `THEREFORE_LOCAL_TZ` — Local timezone (e.g., `Australia/Sydney`)
- `THEREFORE_<TENANT>_ALLOW_WRITES` — Enable write operations per tenant
- `THEREFORE_<TENANT>_ASSIGNEE_ALIASES` / `THEREFORE_<TENANT>_USER_GROUPS` — Task filtering aliases

## Known Quirks

- `DeleteDictionaryKeyword` returns success but does not actually remove in-use keywords.
- `execute_single_query` auto-switches to async multi-query when multiple category numbers are supplied.
- Conversion enums in `update_document` and `add_streams_to_document` accept both string names and numeric values (e.g., `ConvertTo: "SinglePDF"` or `2`).
