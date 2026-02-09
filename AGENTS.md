# Therefore MCP Project Notes (Continuation)

## Status
- MCP server implemented in Python and registered with Codex.
- Codex config updated at `/Users/craig/.codex/config.toml`:
  - Server name: `therefore`
  - Command: `python3 /Volumes/DataSSD/source/therefore-mcp/src/mcp_server.py`
  - Env: `THEREFORE_ENV_PATH=/Volumes/DataSSD/source/therefore-mcp/docs/reference/user/.env.local`
- Restart Codex to pick up MCP server code changes.
- Latest additions:
  - `execute_statistics_query` tool with QueryType normalization.
  - `execute_single_query` auto-switches to async multi-query when multiple categories are supplied.
  - Tenant inference helper (uses `tenant_hint` and string args to infer tenant; sticky per process).
  - Workflow calls can retry on timeout (see workflow retry env keys).
  - Added read-only helpers + generic endpoint wrapper:
    `get_web_api_server_version`, `get_connection_token`, `get_domain_info`,
    `get_client_discovery_info`, `get_connected_user`, `get_permission_constants`,
    `get_role_permission_constants`, `get_document_properties`,
    `get_document_history`, `get_document_checkout_status`, `get_objects_list`,
    `call_endpoint`.
  - Dev hot-reload watchdog (optional via `THEREFORE_MCP_HOT_RELOAD=restart`).
    Remove/disable for production before deploying.
  - Keyword dict tools added: `get_keywords_by_field_no`, `get_keywords_by_key_dic`,
    `get_keywords_by_dictionary_name`, `add_dictionary_keyword`,
    `update_dictionary_keyword`, `delete_dictionary_keyword`.
  - Keyword fields expect keyword IDs (KeywordNos) for SingleKeywordData values; using
    the keyword string can fail with a conversion error.
  - DeleteDictionaryKeyword currently returns success but does not remove an in-use
    keyword (tested on craigdemo; Manifesto remained in list after delete).

## Key Files
- MCP server: `/Volumes/DataSSD/source/therefore-mcp/src/mcp_server.py`
- API client: `/Volumes/DataSSD/source/therefore-mcp/src/therefore_client.py`
- Env config: `/Volumes/DataSSD/source/therefore-mcp/docs/reference/user/.env.local`

## Multi-Tenant Env Format
Single-tenant (legacy) still works:
```
THEREFORE_BASE_URL=...
THEREFORE_AUTH_METHOD=Basic
THEREFORE_USERNAME=...
THEREFORE_PASSWORD=...
THEREFORE_TENANTNAME=...
THEREFORE_SAFE_DOC_ID=...
THEREFORE_SAFE_CATEGORY_ID=...
THEREFORE_ALLOW_WRITES=true
THEREFORE_WORKFLOW_TIMEOUT_SECONDS=240
THEREFORE_WORKFLOW_MAX_ROWS=10000
THEREFORE_WORKFLOW_RETRY_TIMEOUT_SECONDS=480
THEREFORE_WORKFLOW_RETRY_COUNT=1
```

Multi-tenant:
```
THEREFORE_TENANTS=demo,prod
THEREFORE_DEFAULT_TENANT=demo

THEREFORE_DEMO_BASE_URL=...
THEREFORE_DEMO_AUTH_METHOD=Basic
THEREFORE_DEMO_USERNAME=...
THEREFORE_DEMO_PASSWORD=...
THEREFORE_DEMO_TENANTNAME=...
THEREFORE_DEMO_SAFE_DOC_ID=...
THEREFORE_DEMO_SAFE_CATEGORY_ID=...
THEREFORE_DEMO_ALLOW_WRITES=true

THEREFORE_PROD_BASE_URL=...
THEREFORE_PROD_AUTH_METHOD=Basic
THEREFORE_PROD_USERNAME=...
THEREFORE_PROD_PASSWORD=...
THEREFORE_PROD_TENANTNAME=...
THEREFORE_PROD_SAFE_DOC_ID=...
THEREFORE_PROD_SAFE_CATEGORY_ID=...
THEREFORE_PROD_ALLOW_WRITES=false
```
Workflow settings are global (top-level keys) and apply to all tenants:
```
THEREFORE_WORKFLOW_TIMEOUT_SECONDS=240
THEREFORE_WORKFLOW_MAX_ROWS=10000
THEREFORE_WORKFLOW_RETRY_TIMEOUT_SECONDS=480
THEREFORE_WORKFLOW_RETRY_COUNT=1
```
If multiple tenants are configured and no default is set, tools require a `tenant` argument.
Tenant selection is sticky within the MCP server process: if a tool call specifies a `tenant`, that tenant becomes the default for subsequent calls unless another `tenant` is explicitly provided.
Tenant inference helper: if `tenant` is not provided, the MCP server scans `tenant_hint` and other string args for tenant labels/keys and uses a unique match as the sticky tenant.
Optional assignee/group aliases for task filtering:
- `THEREFORE_<TENANT>_ASSIGNEE_ALIASES` or `THEREFORE_<TENANT>_USER_GROUPS` (comma-separated).

## MCP Tools (MVP)
- `resolve_category` (fuzzy category matching; returns candidates + `needs_confirmation`)
- `list_category_fields`
- `resolve_field` (fuzzy field matching; returns candidates + `needs_confirmation`)
- `get_categories_tree`
- `get_category_info`
- `get_document`
- `get_document_index_data`
- `get_web_api_server_version`
- `get_connection_token`
- `get_domain_info`
- `get_client_discovery_info`
- `get_system_customer_id`
- `get_connected_user`
- `get_permission_constants`
- `get_role_permission_constants`
- `get_document_properties`
- `get_document_history`
- `get_document_checkout_status`
- `get_objects_list`
- `get_objects`
- `execute_users_query` (defaults to Flags=5 for user search)
- `get_users_from_group`
- `get_user_details`
- `get_keywords_by_field_no`
- `get_keywords_by_key_dic`
- `validate_keywords`
- `get_keywords_by_dictionary_name`
- `add_dictionary_keyword`
- `update_dictionary_keyword`
- `delete_dictionary_keyword`
- `deactivate_dictionary_keyword`
- `execute_workflow_query_for_all`
- `execute_workflow_query_for_process`
- `get_linked_workflows_for_doc`
- `get_workflow_history`
- `get_workflow_instance`
- `get_workflow_process`
- `get_workflow_task_settings`
- `get_my_workflow_tasks` (defaults to RunningInstances, filters by connected user/assignee values; can resolve group membership; can flag overdue tasks and return schedule summary)
- `execute_single_query` (auto-switches to async multi-query when query includes multiple category numbers)
- `execute_async_single_query` (auto-fetches batches via ExecuteAsyncSingleQuery → GetNextSingleQueryRows → ReleaseSingleQuery)
- `get_next_single_query_rows`
- `release_single_query`
- `execute_async_multi_query` (auto-fetches batches via ExecuteAsyncMultiQuery → GetNextMultiQueryRows → ReleaseMultiQuery)
- `get_next_multi_query_rows`
- `release_multi_query`
- `execute_full_text_query`
- `execute_statistics_query`
- `call_endpoint` (generic POST wrapper)

## Async Multi-Query Grouping
`execute_async_multi_query_all` merges batches by `(CaseDefinitionNo, CategoryNo, ProcessNo)` so results are grouped by category/case/process rather than position in the query list.
- `create_document` (uses web-client flow)
- `update_document_index_data` (uses SaveDocumentIndexData; requires LastChangeTime from GetDocumentIndexData)
- `update_document` (uses UpdateDocument for streams + optional index updates)
- `add_streams_to_document` (uses AddStreamsToDocument; supports conversion options like ConvertTo)
- `delete_document`

## Conversion Enums
Conversion options in `update_document` and `add_streams_to_document` accept string names or numeric values.
- ConvertTo: Original=0, SingleTIFF=1, SinglePDF=2, MultipageTIFF=3, MultipagePDF=4, SearchablePDF=5, SearchablePDFA=6, JPEG=50
- AnnotationMode: Default=0, Merge=1, Hide=2
- SignatureMode: NoSignature=0, SignatureOnly=1, SignatureAndTimestamp=2
- NewStreamInsertMode: Append=0, Prepend=1

## Write Path (Web-Client Flow)
`GetCategoryInfo → PreprocessIndexData → EvaluateConditionalProperties → CreateDocument`
- Default `WithAutoAppendMode=0` unless user overrides.
- `EvaluateConditionalProperties` response persisted to:
  - `/Volumes/DataSSD/source/therefore-mcp/docs/notes/evaluate_conditional_properties.json`

## Caching
- Category cache: `/Volumes/DataSSD/source/therefore-mcp/docs/notes/category_cache.json` (TTL 300s)
- Field cache: `/Volumes/DataSSD/source/therefore-mcp/docs/notes/field_cache.json` (TTL 300s)

## Validation Artifacts
- Latest validation report:
  - `/Volumes/DataSSD/source/therefore-mcp/docs/notes/validation_report.md`
  - `/Volumes/DataSSD/source/therefore-mcp/docs/notes/validation_report.json`

## Next Steps After Restart
1. Verify MCP tools list is visible in Codex.
2. Smoke test:
   - `resolve_category` with a known category name.
   - `resolve_field` with a label from that category.
   - `create_document` (if writes enabled in `.env.local`).
