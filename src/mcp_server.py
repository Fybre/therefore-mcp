#!/usr/bin/env python3
import argparse
import base64
import json
import os
import re
import sys
import traceback

# Ensure sibling modules (therefore_client) are importable regardless of cwd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Repo root for locating tools/config_generator
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "tools", "config_generator"))
import difflib
import time
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - fallback for older environments
    ZoneInfo = None

from therefore_client import (
    ThereforeClient,
    ThereforeConfig,
    build_tenant_configs_from_env,
    load_env,
    normalize_tenant_key,
)

try:
    import asyncio
    import uuid
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response, StreamingResponse
    import uvicorn

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


def _read_message() -> Optional[Dict[str, Any]]:
    """Read a newline-delimited JSON-RPC message from stdin (MCP stdio transport)."""
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return None
    return json.loads(line.decode("utf-8", errors="replace"))


def _write_message(payload: Dict[str, Any]) -> None:
    """Write a newline-delimited JSON-RPC message to stdout (MCP stdio transport)."""
    data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def _error_response(msg_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _result_response(msg_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": result,
    }


def _tool_content(obj: Any) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(obj, indent=2)}]}


# Operation registry: maps (tool_name, operation) to parameter info
OPERATION_REGISTRY = {
    # therefore_system operations
    ("therefore_system", "get_customer_id"): {
        "description": "Get the tenant customer/client/system ID",
        "required": [],
        "optional": {},
    },
    ("therefore_system", "get_connected_user"): {
        "description": "Get the currently connected user information",
        "required": [],
        "optional": {"create": "boolean - create connection if needed"},
    },
    ("therefore_system", "get_version"): {
        "description": "Get the Therefore WebAPI server version",
        "required": [],
        "optional": {},
    },
    ("therefore_system", "get_connection_token"): {
        "description": "Get a connection token for the current session",
        "required": [],
        "optional": {},
    },
    ("therefore_system", "get_connection_token_from_adfs"): {
        "description": "Exchange an ADFS/Entra token for a Therefore connection token (SSO auth)",
        "required": ["security_token"],
        "optional": {
            "connect_mode": "string - 'NoLicenseMove' (default), 'ConnectForSignOut', or 'MoveLicense'"
        },
    },
    ("therefore_system", "get_domain_info"): {
        "description": "Get domain configuration information",
        "required": [],
        "optional": {},
    },
    ("therefore_system", "get_discovery_info"): {
        "description": "Get client discovery information",
        "required": [],
        "optional": {},
    },
    ("therefore_system", "get_permission_constants"): {
        "description": "Get permission constant definitions",
        "required": [],
        "optional": {},
    },
    ("therefore_system", "get_role_permission_constants"): {
        "description": "Get role permission constant definitions",
        "required": [],
        "optional": {},
    },
    ("therefore_system", "get_objects_list"): {
        "description": "Get a list of Therefore objects by IDs",
        "required": ["load_items_list"],
        "optional": {},
    },
    ("therefore_system", "get_objects"): {
        "description": "Get Therefore objects by type",
        "required": ["obj_type", "flags"],
        "optional": {},
    },
    ("therefore_system", "get_statistics"): {
        "description": "Execute a statistics query",
        "required": [],
        "optional": {
            "query_type": "integer or string - statistics query type",
            "restrict_to_obj_no": "integer - restrict to object number",
            "restrict_to_user": "boolean - restrict to user",
        },
    },
    ("therefore_system", "get_logfiles"): {
        "description": "Get server log files",
        "required": [],
        "optional": {
            "days_back": "integer - how many days back",
            "application_filter": "string - filter by application",
            "max_docs": "integer - max documents to retrieve",
            "include_raw": "boolean - include raw log data",
            "output_mode": "string - output format",
            "severity_filter": "string - filter by severity level",
        },
    },
    ("therefore_system", "get_login_history"): {
        "description": "Get login history for a user",
        "required": [],
        "optional": {
            "username": "string - username to query",
            "max_entries": "integer - max history entries",
        },
    },
    ("therefore_system", "call_endpoint"): {
        "description": "Call a custom Therefore API endpoint",
        "required": ["endpoint"],
        "optional": {"payload": "object - request payload"},
    },
    # therefore_categories operations
    ("therefore_categories", "get_tree"): {
        "description": "Get the category tree/hierarchy",
        "required": [],
        "optional": {"payload": "object - optional request payload"},
    },
    ("therefore_categories", "get_info"): {
        "description": "Get detailed information about a category",
        "required": ["category_no"],
        "optional": {},
    },
    ("therefore_categories", "resolve"): {
        "description": "Resolve a category by name/query",
        "required": ["query"],
        "optional": {
            "max_results": "integer - max results to return",
            "min_score": "number - minimum match score",
            "include_non_categories": "boolean - include non-category results",
            "confirm_threshold": "number - confirmation threshold",
        },
    },
    ("therefore_categories", "list_fields"): {
        "description": "List all fields for a category",
        "required": ["category_no"],
        "optional": {},
    },
    ("therefore_categories", "resolve_field"): {
        "description": "Resolve a field by name within a category",
        "required": ["category_no", "query"],
        "optional": {
            "confirm_threshold": "number - confirmation threshold",
            "field_type_hint": "integer - expected field type",
        },
    },
    ("therefore_categories", "get_referenced_table_info"): {
        "description": "Get information about a referenced table field",
        "required": ["data_type_no"],
        "optional": {},
    },
    ("therefore_categories", "execute_dependent_fields_query"): {
        "description": "List referenced-table rows valid for a field. Specify exactly one of category_no or case_definition_no and pass the complete current index-data state.",
        "required": ["field_no"],
        "optional": {
            "index_data_items": "array - current typed index data; new categories are preprocessed by resolve_referenced_field",
            "category_no": "integer - category context",
            "case_definition_no": "integer - case-definition context",
            "max_rows": "integer - maximum rows (default 500)",
            "save_mode": "boolean - apply save-mode filtering (default false)",
        },
    },
    ("therefore_categories", "fill_dependent_fields"): {
        "description": "Populate dependent fields after selecting a referenced-table ID. Specify exactly one of doc_no, category_no, or case_definition_no.",
        "required": ["primary_field_no", "index_data_items"],
        "optional": {
            "doc_no": "integer - existing-document context",
            "category_no": "integer - new-category context",
            "case_definition_no": "integer - case-definition context",
            "exclude_redundant": "boolean - omit redundant fields (default false)",
            "include_access_mask": "boolean - include access masks (default false)",
            "do_calculate_fields": "boolean - calculate fields (default true)",
        },
    },
    ("therefore_categories", "resolve_referenced_field"): {
        "description": "High-level referenced-field helper: loads field/table metadata, prepares current index data, queries valid rows, and optionally selects/fills one row in the same call.",
        "required": ["field_no"],
        "optional": {
            "doc_no": "integer - existing-document context",
            "category_no": "integer - new-category context",
            "case_definition_no": "integer - case-definition context",
            "index_data_items": "array - current typed index data; inferred/preprocessed when omitted",
            "max_rows": "integer - maximum valid rows (default 500)",
            "save_mode": "boolean - apply save-mode filtering (default false)",
            "selected_row_index": "integer - zero-based row to select and pass to FillDependentFields",
            "index_data_kind": "string - override typed wrapper, e.g. StringIndexData or IntIndexData",
        },
    },
    ("therefore_categories", "query_referenced_table"): {
        "description": "Query rows in a referenced table with optional filter conditions",
        "required": [],
        "optional": {
            "data_type_no": "integer - DataTypeNo of the referenced table",
            "name": "string - name of the referenced table (used if data_type_no not supplied)",
            "conditions": "array - filter conditions [{FieldNoOrName, Condition}]",
            "max_rows": "integer - max rows to return (default 5000)",
        },
    },
    ("therefore_categories", "generate_config"): {
        "description": "Generate a category configuration XML",
        "required": ["spec_or_description"],
        "optional": {
            "spec": "object - JSON specification",
            "description": "string - natural language description",
            "baseline_path": "string - path to baseline XML",
            "api_check": "boolean - check against API",
            "output_path": "string - output file path",
        },
    },
    # therefore_documents operations
    ("therefore_documents", "get"): {
        "description": "Get a document by number. StreamsInfo (attachment filenames/StreamNos, not content) is included by default - use get_stream/get_stream_raw with a StreamNo from here to fetch actual file content.",
        "required": ["doc_no"],
        "optional": {
            "include_index_data": "boolean - include index data (default true)",
            "include_streams_info": "boolean - include streams info: filenames/StreamNos only, not file content (default true) - an empty StreamsInfo here reliably means no attachments only when this is true",
            "include_streams_data": "boolean - include full streams data inline, i.e. the actual file bytes (default false - expensive, prefer get_stream/get_stream_raw instead)",
            "include_checkout_status": "boolean - include checkout status",
            "include_access_mask": "boolean - include access mask",
        },
    },
    ("therefore_documents", "get_index_data"): {
        "description": "Get document index data",
        "required": ["doc_no"],
        "optional": {},
    },
    ("therefore_documents", "get_properties"): {
        "description": "Get document properties",
        "required": ["doc_no"],
        "optional": {
            "version_no": "integer - version number",
            "is_doc_title_needed": "boolean - include document title",
        },
    },
    ("therefore_documents", "get_history"): {
        "description": "Get document history",
        "required": ["doc_no"],
        "optional": {},
    },
    ("therefore_documents", "get_checkout_status"): {
        "description": "Get document checkout status",
        "required": ["doc_no"],
        "optional": {},
    },
    ("therefore_documents", "get_versions"): {
        "description": "Get document versions",
        "required": ["doc_no"],
        "optional": {},
    },
    ("therefore_documents", "get_converted_streams"): {
        "description": "Get converted document streams",
        "required": ["doc_no"],
        "optional": {
            "convert_to": "string or integer - conversion format",
            "annotation_mode": "string or integer - annotation mode",
            "signature_mode": "string or integer - signature mode",
            "certificate_name": "string - certificate name",
            "time_stamp_server": "string - timestamp server URL",
            "time_stamp_user": "string - timestamp username",
            "time_stamp_pwd": "string - timestamp password",
            "multipage_stream_name": "string - multipage stream name",
            "stream_nos": "array of integers - stream numbers",
            "retrieve_reason": "string - retrieval reason",
            "archive_converted_files": "boolean - archive converted files",
            "custom_archive_file_name": "string - custom archive filename",
        },
    },
    ("therefore_documents", "get_stream"): {
        "description": "Get a document stream as base64-encoded JSON",
        "required": ["doc_no", "stream_no"],
        "optional": {
            "version_no": "integer - version number",
            "retrieve_reason": "string - reason for retrieval",
        },
    },
    ("therefore_documents", "get_stream_raw"): {
        "description": "Get a document stream as raw binary data (for large files)",
        "required": ["doc_no", "stream_no"],
        "optional": {
            "version_no": "integer - version number",
            "retrieve_reason": "string - reason for retrieval",
            "timeout_override": "integer - timeout in seconds for large files",
        },
    },
    ("therefore_documents", "create"): {
        "description": "Create a new document",
        "required": ["category_no", "streams_or_content"],
        "optional": {
            "streams": "array - file streams",
            "content_text": "string - text content",
            "content_filename": "string - filename for text content",
            "index_data_items": "array - index data values",
            "check_in_comments": "string - check-in comments",
            "with_auto_append_mode": "integer - auto append mode",
            "do_fill_dependent_fields": "boolean - fill dependent fields (default true)",
            "run_webclient_flow": "boolean - run web client flow (default true)",
        },
    },
    ("therefore_documents", "update"): {
        "description": "Update a document",
        "required": ["doc_no"],
        "optional": {
            "updates": "array - update items",
            "stream_nos_to_delete": "array - stream numbers to delete",
            "streams_to_rename": "array - streams to rename",
            "conversion_options": "object - conversion options",
        },
    },
    ("therefore_documents", "update_index_data"): {
        "description": "Update document index data",
        "required": ["doc_no", "index_data_items"],
        "optional": {},
    },
    ("therefore_documents", "add_streams"): {
        "description": "Add streams to a document",
        "required": ["doc_no", "streams"],
        "optional": {"conversion_options": "object - conversion options"},
    },
    ("therefore_documents", "delete"): {
        "description": "Delete a document",
        "required": ["doc_no"],
        "optional": {},
    },
    ("therefore_documents", "check_out"): {
        "description": "Check out a document",
        "required": ["doc_no"],
        "optional": {"version_no": "integer - version number"},
    },
    ("therefore_documents", "check_in"): {
        "description": "Check in a document",
        "required": ["doc_no"],
        "optional": {
            "check_in_comments": "string - check-in comments",
            "version_no": "integer - version number",
        },
    },
    ("therefore_documents", "undo_check_out"): {
        "description": "Undo document checkout",
        "required": ["doc_no"],
        "optional": {"version_no": "integer - version number"},
    },
    ("therefore_documents", "add_comment"): {
        "description": "Add a comment to a document",
        "required": ["doc_no", "comment_text"],
        "optional": {"obj_type": "integer - object type, default 2 (document)"},
    },
    ("therefore_documents", "edit_comment"): {
        "description": "Edit an existing comment on a document",
        "required": ["doc_no", "comment_id", "comment_text"],
        "optional": {"obj_type": "integer - object type, default 2 (document)"},
    },
    ("therefore_documents", "get_comments"): {
        "description": "Get document comments",
        "required": ["doc_no"],
        "optional": {"obj_type": "integer - object type, default 2 (document)"},
    },
    # therefore_query operations
    ("therefore_query", "search"): {
        "description": "Execute a document search query",
        "required": ["query"],
        "optional": {"full_text": "string - full text search"},
    },
    ("therefore_query", "search_async"): {
        "description": "Execute an async document search query",
        "required": ["query"],
        "optional": {
            "full_text": "string - full text search",
            "row_block_size": "integer - row block size (default 1000)",
            "max_rows": "integer - max rows (default 2147483647)",
            "auto_fetch_all": "boolean - auto fetch all rows (default true)",
        },
    },
    ("therefore_query", "search_multi"): {
        "description": "Execute multiple async queries",
        "required": ["queries"],
        "optional": {
            "full_text": "string - full text search",
            "row_block_size": "integer - row block size (default 1000)",
            "max_rows": "integer - max rows (default 2147483647)",
            "auto_fetch_all": "boolean - auto fetch all rows (default true)",
        },
    },
    ("therefore_query", "search_fulltext"): {
        "description": "Execute a full text search over document content. A zero-hit result doesn't necessarily mean the term isn't in any document - a category having full-text enabled doesn't guarantee every document in it is actually indexed (see therefore_knowledge quirks). Returns a flat Results array with MatchedWords/Relevance per hit.",
        "required": ["search"],
        "optional": {
            "categories": "array - category numbers",
            "max_rows": "integer - max rows (default 100)",
            "include_index_data": "boolean - include index data",
            "case_no": "integer - case number",
        },
    },
    ("therefore_query", "get_next_rows"): {
        "description": "Get next rows from an async query",
        "required": ["query_id", "row_block_size"],
        "optional": {},
    },
    ("therefore_query", "get_next_multi_rows"): {
        "description": "Get next rows from a multi query",
        "required": ["query_id", "row_block_size"],
        "optional": {},
    },
    ("therefore_query", "release"): {
        "description": "Release an async query session",
        "required": ["query_id"],
        "optional": {},
    },
    ("therefore_query", "release_multi"): {
        "description": "Release a multi query session",
        "required": ["query_id"],
        "optional": {},
    },
    # therefore_workflow operations
    ("therefore_workflow", "query_all"): {
        "description": "Query all workflow instances",
        "required": [],
        "optional": {
            "workflow_flags": "integer or string - workflow flags",
            "max_rows": "integer - max rows",
            "include_instance_details": "boolean - include instance details",
            "instance_detail_mode": "string - detail mode (summary/full)",
            "max_instance_workers": "integer - max workers for details",
            "is_access_mask_needed": "boolean - include access mask",
            "load_history": "boolean - load history",
            "debug": "boolean - enable debug mode",
            "debug_log_path": "string - debug log file path",
            "debug_progress_every": "integer - debug progress interval",
        },
    },
    ("therefore_workflow", "query_process"): {
        "description": "Query workflow instances for a specific process",
        "required": ["process_no"],
        "optional": {
            "workflow_flags": "integer or string - workflow flags",
            "max_rows": "integer - max rows",
            "include_instance_details": "boolean - include instance details",
            "instance_detail_mode": "string - detail mode",
            "max_instance_workers": "integer - max workers",
            "is_access_mask_needed": "boolean - include access mask",
            "load_history": "boolean - load history",
            "debug": "boolean - enable debug",
            "debug_log_path": "string - debug log path",
            "debug_progress_every": "integer - debug progress interval",
        },
    },
    ("therefore_workflow", "get_my_tasks"): {
        "description": "Get my workflow tasks",
        "required": [],
        "optional": {
            "task_filter": "string - task filter",
            "filter_to_user": "boolean - filter to current user",
            "include_unfiltered": "boolean - include unfiltered",
            "include_overdue_summary": "boolean - include overdue summary",
            "assignee_values": "array - assignee values",
            "resolve_group_membership": "boolean - resolve groups",
            "user_query": "string - user query",
            "user_query_flags": "integer - user query flags",
            "two_phase": "boolean - two phase fetch",
            "fetch_details": "boolean - fetch details",
        },
    },
    ("therefore_workflow", "get_my_instances"): {
        "description": "Get my workflow instances",
        "required": [],
        "optional": {
            "workflow_flags": "integer - workflow flags",
            "max_rows": "integer - max rows",
            "include_instance_details": "boolean - include details",
            "instance_detail_mode": "string - detail mode",
        },
    },
    ("therefore_workflow", "get_all_instances"): {
        "description": "Get all workflow instances",
        "required": [],
        "optional": {
            "workflow_flags": "integer - workflow flags",
            "max_rows": "integer - max rows",
            "include_instance_details": "boolean - include details",
            "instance_detail_mode": "string - detail mode",
        },
    },
    ("therefore_workflow", "get_user_instances"): {
        "description": "Get workflow instances for a user",
        "required": [],
        "optional": {
            "workflow_flags": "integer - workflow flags",
            "max_rows": "integer - max rows",
            "include_instance_details": "boolean - include details",
            "instance_detail_mode": "string - detail mode",
        },
    },
    ("therefore_workflow", "get_instance"): {
        "description": "Get a specific workflow instance",
        "required": ["instance_no"],
        "optional": {
            "token_no": "integer - token number",
            "is_access_mask_needed": "boolean - include access mask",
            "load_history": "boolean - load history",
        },
    },
    ("therefore_workflow", "get_process"): {
        "description": "Get workflow process definition",
        "required": ["process_no"],
        "optional": {
            "version_no": "integer - version number",
            "load_tasks": "boolean - load tasks (default true)",
            "is_access_mask_needed": "boolean - include access mask",
        },
    },
    ("therefore_workflow", "get_task_settings"): {
        "description": "Get workflow task settings",
        "required": ["task_no", "process_no"],
        "optional": {
            "version_no": "integer - version number",
            "setting_names": "array - setting names to retrieve",
        },
    },
    ("therefore_workflow", "get_history"): {
        "description": "Get workflow instance history",
        "required": ["instance_no"],
        "optional": {
            "block_size": "integer - block size (default 1000)",
            "include_routing_info": "boolean - include routing info (default true)",
            "max_creation_date": "string - max creation date",
            "seq_pos": "integer - sequence position",
        },
    },
    ("therefore_workflow", "get_linked"): {
        "description": "Get workflows linked to a document",
        "required": ["doc_no"],
        "optional": {"wf_doc_link_type": "integer - link type"},
    },
    ("therefore_workflow", "complete_task"): {
        "description": "Complete a workflow task",
        "required": ["workflow_instance_token", "task_no"],
        "optional": {
            "user_decision": "string - user decision",
            "index_data_items": "array - index data updates",
        },
    },
    ("therefore_workflow", "claim"): {
        "description": "Claim a workflow instance",
        "required": ["workflow_instance_token"],
        "optional": {"task_no": "integer - task number"},
    },
    ("therefore_workflow", "disclaim"): {
        "description": "Disclaim a workflow instance",
        "required": ["workflow_instance_token"],
        "optional": {"task_no": "integer - task number"},
    },
    ("therefore_workflow", "delegate"): {
        "description": "Delegate a workflow instance",
        "required": ["workflow_instance_token", "user_id"],
        "optional": {"task_no": "integer - task number"},
    },
    ("therefore_workflow", "get_case_definition"): {
        "description": "Get a case definition's linked categories and index fields (needed to know a valid case_definition_no before calling create_case, and to build index_data_items)",
        "required": ["case_definition_no"],
        "optional": {},
    },
    ("therefore_workflow", "create_case"): {
        "description": "Create a case",
        "required": ["case_definition_no"],
        "optional": {"index_data_items": "array - index data"},
    },
    ("therefore_workflow", "get_case"): {
        "description": "Get case information",
        "required": ["case_no"],
        "optional": {},
    },
    ("therefore_workflow", "get_case_documents"): {
        "description": "Get documents in a case. This is the correct way to list a case's documents - therefore_query's search operations do NOT support filtering by case (a Query object's CaseDefinitionNo field is a no-op/errors, see therefore_knowledge quirks).",
        "required": ["case_no"],
        "optional": {"max_rows": "integer - max rows (default 1000)"},
    },
    ("therefore_workflow", "get_case_history"): {
        "description": "Get case history",
        "required": ["case_no"],
        "optional": {},
    },
    ("therefore_workflow", "execute_dependent_fields_query"): {
        "description": "List referenced-table rows valid for a field in the current case/category index-data context. ResultRows.FieldValues map positionally to QueryResult.Columns.",
        "required": ["field_no"],
        "optional": {
            "index_data_items": "array - current typed index data (default empty)",
            "case_definition_no": "integer - case definition context; mutually exclusive with category_no",
            "category_no": "integer - category context; mutually exclusive with case_definition_no",
            "max_rows": "integer - maximum rows (default 500)",
            "save_mode": "boolean - apply save-mode filtering (default false)",
        },
    },
    ("therefore_workflow", "fill_dependent_fields"): {
        "description": "Populate fields dependent on a selected referenced-table ID. Specify exactly one of doc_no, case_definition_no, or category_no; omitted contexts must not be sent as zero placeholders.",
        "required": ["primary_field_no", "index_data_items"],
        "optional": {
            "doc_no": "integer - document context",
            "case_definition_no": "integer - case definition context",
            "category_no": "integer - category context",
            "exclude_redundant": "boolean - omit redundant returned fields (default false)",
            "include_access_mask": "boolean - include access masks (default false)",
            "do_calculate_fields": "boolean - calculate fields (default true)",
        },
    },
    ("therefore_workflow", "save_case_index_data_quick"): {
        "description": "Save validated case index data without supplying concurrency timestamps. For referenced fields, pass FillDependentFields.UpdatedIndexDataItems.",
        "required": ["case_no", "index_data_items"],
        "optional": {"check_in_comments": "string - audit comment"},
    },
    ("therefore_workflow", "save_case_index_data"): {
        "description": "Save validated case index data with optimistic concurrency. If timestamps are omitted, the client fetches fresh values with GetCase.",
        "required": ["case_no", "index_data_items"],
        "optional": {
            "check_in_comments": "string - audit comment",
            "do_fill_dependent_fields": "boolean - fill dependent fields (default true)",
            "last_change_time": "string - WCF timestamp; fetched automatically if omitted",
            "last_change_time_iso": "string - ISO timestamp; fetched automatically if omitted",
        },
    },
    # therefore_users operations
    ("therefore_users", "search"): {
        "description": "Search for users",
        "required": ["query"],
        "optional": {
            "domain_names": "array - domain names to search",
            "flags": "integer - search flags (default 5)",
        },
    },
    ("therefore_users", "get_from_group"): {
        "description": "Get users from a group",
        "required": ["group_id_or_name"],
        "optional": {
            "group_id": "integer - group ID",
            "group_name": "string - group name",
            "domain_name": "string - domain name",
        },
    },
    ("therefore_users", "get_details"): {
        "description": "Get user details",
        "required": ["user_or_group_id"],
        "optional": {},
    },
    ("therefore_users", "create"): {
        "description": "Create a new user",
        "required": ["user_name", "full_name"],
        "optional": {
            "email": "string - email address",
            "password": "string - password",
            "domain_name": "string - domain name",
        },
    },
    ("therefore_users", "update_groups"): {
        "description": "Update user group assignments",
        "required": ["user_id"],
        "optional": {"group_ids": "array - group IDs"},
    },
    ("therefore_users", "get_groups"): {
        "description": "Get user group assignments",
        "required": ["user_id"],
        "optional": {},
    },
    ("therefore_users", "set_password"): {
        "description": "Set user password",
        "required": ["user_id", "new_password"],
        "optional": {},
    },
    ("therefore_users", "change_password"): {
        "description": "Change current user password",
        "required": ["old_password", "new_password"],
        "optional": {},
    },
    ("therefore_users", "reset_password"): {
        "description": "Reset user password",
        "required": ["user_id"],
        "optional": {"send_email": "boolean - send email (default true)"},
    },
    ("therefore_users", "delete_portal"): {
        "description": "Delete a portal user",
        "required": ["user_id"],
        "optional": {},
    },
    ("therefore_users", "save_portal"): {
        "description": "Save portal user settings",
        "required": ["user_id"],
        "optional": {
            "user_name": "string - username",
            "full_name": "string - full name",
            "email": "string - email",
            "is_active": "boolean - active status",
        },
    },
    ("therefore_users", "move_license"): {
        "description": "Move a license from one user to another",
        "required": ["source_user_id", "target_user_id"],
        "optional": {},
    },
    ("therefore_users", "get_settings"): {
        "description": "Get user settings",
        "required": ["user_id"],
        "optional": {},
    },
    ("therefore_users", "set_settings"): {
        "description": "Set user settings",
        "required": ["user_id", "settings"],
        "optional": {},
    },
    # therefore_keywords operations
    ("therefore_keywords", "get_by_field"): {
        "description": "Get keywords for a field",
        "required": ["field_no"],
        "optional": {
            "category_no": "integer - category number",
            "case_definition_no": "integer - case definition number",
            "dependent_field_filter_value": "string - dependent field filter",
            "show_deactivated_keywords": "boolean - show deactivated",
            "index_data_items": "array - index data for dependent fields",
            "skip_loading_keyword_nos": "boolean - skip loading keyword numbers",
            "max_rows": "integer - max rows",
        },
    },
    ("therefore_keywords", "get_by_dictionary"): {
        "description": "Get keywords from a dictionary by number",
        "required": ["key_dic_no"],
        "optional": {
            "filter_value": "string - filter value",
            "max_values": "integer - max values",
            "include_deactivated_keywords": "boolean - include deactivated",
        },
    },
    ("therefore_keywords", "get_by_name"): {
        "description": "Get keywords from a dictionary by name",
        "required": ["dictionary_name"],
        "optional": {
            "max_results": "integer - max results",
            "min_score": "number - min match score",
            "confirm_threshold": "number - confirmation threshold",
            "filter_value": "string - filter value",
            "max_values": "integer - max values",
            "include_deactivated_keywords": "boolean - include deactivated",
        },
    },
    ("therefore_keywords", "validate"): {
        "description": "Validate keywords for a field",
        "required": ["field_no"],
        "optional": {
            "keywords": "array - keywords to validate",
            "is_filter_mode": "boolean - filter mode",
        },
    },
    ("therefore_keywords", "add"): {
        "description": "Add a keyword to a dictionary",
        "required": ["keyword_name", "dictionary_no_or_name"],
        "optional": {
            "dictionary_no": "integer - dictionary number",
            "dictionary_name": "string - dictionary name",
            "dictionary_type_no": "integer - dictionary type",
            "is_keyword_deactivated": "boolean - deactivated status",
            "check_existing": "boolean - check if exists",
            "ignore_if_exists": "boolean - ignore if exists",
        },
    },
    ("therefore_keywords", "update"): {
        "description": "Update a keyword",
        "required": ["keyword_id"],
        "optional": {
            "new_keyword_name": "string - new keyword name",
            "is_keyword_deactivated": "boolean - deactivated status",
        },
    },
    ("therefore_keywords", "delete"): {
        "description": "Delete a keyword",
        "required": ["keyword_id"],
        "optional": {},
    },
    ("therefore_keywords", "deactivate"): {
        "description": "Deactivate a keyword",
        "required": ["keyword_id"],
        "optional": {},
    },
    # therefore_knowledge operations
    ("therefore_knowledge", "search"): {
        "description": (
            "Search the Therefore API knowledge base. "
            "If the local knowledge base does not have a satisfactory answer, "
            "the extended documentation is available on GitHub: "
            "https://github.com/Fybre/therefore-mcp/tree/main/docs — "
            "in particular PYTHON_EXAMPLES.md, PYTHON_QUICK_REFERENCE.md, and "
            "therefore-api-complete-guide.md."
        ),
        "required": ["query"],
        "optional": {"limit": "integer - max results (default 5)"},
    },
    ("therefore_knowledge", "get_workflow"): {
        "description": "Get a workflow guide",
        "required": ["workflow_name"],
        "optional": {},
    },
    ("therefore_knowledge", "get_field_types"): {
        "description": "Get field type information",
        "required": ["field_type"],
        "optional": {},
    },
    ("therefore_knowledge", "get_pattern"): {
        "description": "Get a common coding pattern",
        "required": ["pattern_name"],
        "optional": {},
    },
    ("therefore_knowledge", "get_quirks"): {
        "description": "Get API quirks and workarounds",
        "required": [],
        "optional": {"search_term": "string - search filter"},
    },
    ("therefore_knowledge", "list_all"): {
        "description": "List all available knowledge resources",
        "required": [],
        "optional": {},
    },
    ("therefore_knowledge", "get_api_help"): {
        "description": "Get live API documentation from Therefore server",
        "required": [],
        "optional": {
            "api_operation": "string - specific operation name",
            "format": "string - output format (text/html)",
        },
    },
}


def build_tools() -> List[Dict[str, Any]]:
    """
    Build the 9 grouped MCP tool definitions.
    Each tool (except ask_therefore_expert) uses an 'operation' enum to select sub-operations.
    All parameters across operations are flattened as optional properties.
    """
    return [
        {
            "name": "ask_therefore_expert",
            "description": """START HERE for any Therefore operation. Describe what you want to do and this returns the exact tool, operation, and parameters needed.

The expert routes your question to the right tool and operation, provides parameter details, and offers Therefore API guidance.

Supports multi-tenant: use 'tenant' parameter to target specific tenant.

Example: {"question": "how do I create a document?"}
Returns: {suggested_tool: "therefore_documents", suggested_operation: "create", call_with: {...}, all_parameters: {...}}""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Your question about Therefore API usage, operations, or troubleshooting",
                    },
                    "tenant": {
                        "type": "string",
                        "description": "Tenant key to target (e.g., 'demo'). If omitted, uses default tenant.",
                    },
                    "tenant_hint": {
                        "type": "string",
                        "description": "Free-text hint to auto-detect tenant (e.g., company name).",
                    },
                },
                "required": ["question"],
            },
        },
        {
            "name": "therefore_connect",
            "description": """Register a Therefore tenant/login at runtime, without editing server config or restarting.

Give it credentials for any Therefore Online tenant (or on-prem server) and it registers a new client under a tenant key you choose (or one derived automatically), verifies the login actually works (calls GetConnectionToken), and makes that key immediately usable as the 'tenant' argument on every other tool - including as the new default for calls that omit 'tenant' entirely.

Registration is in-memory only for the lifetime of this server process (not written to disk), and - when this server is running in multi-client HTTP mode - is scoped to the caller that registered it, not shared with other API keys.

Two ways to specify where to connect:
  - Therefore Online (cloud): just give 'tenant_name' (the subdomain, e.g. 'acme' for acme.thereforeonline.com) - base_url is derived automatically and TenantName header is set from it.
  - Any server (cloud or on-prem): give 'base_url' explicitly, e.g. 'https://acme.thereforeonline.com/theservice/v0001/restun' or an on-prem URL. Pass 'tenant_name' too if it's a Therefore Online host - it's required there or every call 500s with "Tenant name is required."

Example: {"tenant_name": "acme", "username": "jdoe", "password": "..."} then call other tools with {"tenant": "acme", ...}.""",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "tenant_key": {
                        "type": "string",
                        "description": "Key to register this login under for later 'tenant' arguments (e.g. 'acme_admin'). Defaults to a normalized form of tenant_name/base_url if omitted.",
                    },
                    "tenant_name": {
                        "type": "string",
                        "description": "Therefore Online subdomain (e.g. 'acme' for acme.thereforeonline.com). Also sets the required TenantName header for cloud tenants. Required unless base_url is given.",
                    },
                    "base_url": {
                        "type": "string",
                        "description": "Explicit REST base URL, e.g. 'https://acme.thereforeonline.com/theservice/v0001/restun' or an on-prem equivalent. Required unless tenant_name is given (cloud shorthand).",
                    },
                    "username": {"type": "string", "description": "Login username. Required."},
                    "password": {"type": "string", "description": "Login password. Required."},
                    "auth_method": {
                        "type": "string",
                        "description": "'Basic' (default) or 'Bearer'.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Human-readable display name for this tenant (shown in error messages listing available tenants). Defaults to tenant_key.",
                    },
                },
                "required": ["username", "password"],
            },
        },
        {
            "name": "therefore_system",
            "description": "Therefore system operations. Call ask_therefore_expert first to get the operation and parameters needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "get_customer_id",
                            "get_connected_user",
                            "get_version",
                            "get_connection_token",
                            "get_connection_token_from_adfs",
                            "get_domain_info",
                            "get_discovery_info",
                            "get_permission_constants",
                            "get_role_permission_constants",
                            "get_objects_list",
                            "get_objects",
                            "get_statistics",
                            "get_logfiles",
                            "get_login_history",
                            "call_endpoint",
                        ],
                    },
                    "tenant": {
                        "type": "string",
                        "description": "Tenant key (e.g., 'demo'). Required.",
                    },
                },
                "required": ["operation", "tenant"],
                "additionalProperties": True,
            },
        },
        {
            "name": "therefore_categories",
            "description": "Therefore category operations. Call ask_therefore_expert first to get the operation and parameters needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "get_tree",
                            "get_info",
                            "resolve",
                            "list_fields",
                            "resolve_field",
                            "get_referenced_table_info",
                            "execute_dependent_fields_query",
                            "fill_dependent_fields",
                            "resolve_referenced_field",
                            "query_referenced_table",
                            "generate_config",
                        ],
                    },
                    "tenant": {
                        "type": "string",
                        "description": "Tenant key (e.g., 'demo'). Required.",
                    },
                },
                "required": ["operation", "tenant"],
                "additionalProperties": True,
            },
        },
        {
            "name": "therefore_documents",
            "description": "Therefore document operations. Call ask_therefore_expert first to get the operation and parameters needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "get",
                            "get_index_data",
                            "get_properties",
                            "get_history",
                            "get_checkout_status",
                            "get_versions",
                            "get_converted_streams",
                            "get_stream",
                            "get_stream_raw",
                            "create",
                            "update",
                            "update_index_data",
                            "add_streams",
                            "delete",
                            "check_out",
                            "check_in",
                            "undo_check_out",
                            "add_comment",
                            "edit_comment",
                            "get_comments",
                        ],
                    },
                    "tenant": {
                        "type": "string",
                        "description": "Tenant key (e.g., 'demo'). Required.",
                    },
                },
                "required": ["operation", "tenant"],
                "additionalProperties": True,
            },
        },
        {
            "name": "therefore_query",
            "description": "Therefore query operations. Call ask_therefore_expert first to get the operation and parameters needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "search",
                            "search_async",
                            "search_multi",
                            "search_fulltext",
                            "get_next_rows",
                            "get_next_multi_rows",
                            "release",
                            "release_multi",
                        ],
                    },
                    "tenant": {
                        "type": "string",
                        "description": "Tenant key (e.g., 'demo'). Required.",
                    },
                },
                "required": ["operation", "tenant"],
                "additionalProperties": True,
            },
        },
        {
            "name": "therefore_workflow",
            "description": "Therefore workflow operations. Call ask_therefore_expert first to get the operation and parameters needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "query_all",
                            "query_process",
                            "get_my_tasks",
                            "get_my_instances",
                            "get_all_instances",
                            "get_user_instances",
                            "get_instance",
                            "get_process",
                            "get_task_settings",
                            "get_history",
                            "get_linked",
                            "complete_task",
                            "claim",
                            "disclaim",
                            "delegate",
                            "get_case_definition",
                            "create_case",
                            "get_case",
                            "get_case_documents",
                            "get_case_history",
                            "execute_dependent_fields_query",
                            "fill_dependent_fields",
                            "save_case_index_data_quick",
                            "save_case_index_data",
                        ],
                    },
                    "tenant": {
                        "type": "string",
                        "description": "Tenant key (e.g., 'demo'). Required.",
                    },
                },
                "required": ["operation", "tenant"],
                "additionalProperties": True,
            },
        },
        {
            "name": "therefore_users",
            "description": "Therefore user operations. Call ask_therefore_expert first to get the operation and parameters needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "search",
                            "get_from_group",
                            "get_details",
                            "create",
                            "update_groups",
                            "get_groups",
                            "set_password",
                            "change_password",
                            "reset_password",
                            "delete_portal",
                            "save_portal",
                            "move_license",
                            "get_settings",
                            "set_settings",
                        ],
                    },
                    "tenant": {
                        "type": "string",
                        "description": "Tenant key (e.g., 'demo'). Required.",
                    },
                },
                "required": ["operation", "tenant"],
                "additionalProperties": True,
            },
        },
        {
            "name": "therefore_keywords",
            "description": "Therefore keyword dictionary operations. Call ask_therefore_expert first to get the operation and parameters needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "get_by_field",
                            "get_by_dictionary",
                            "get_by_name",
                            "validate",
                            "add",
                            "update",
                            "delete",
                            "deactivate",
                        ],
                    },
                    "tenant": {
                        "type": "string",
                        "description": "Tenant key (e.g., 'demo'). Required.",
                    },
                },
                "required": ["operation", "tenant"],
                "additionalProperties": True,
            },
        },
        {
            "name": "therefore_knowledge",
            "description": "Therefore knowledge base operations. Call ask_therefore_expert first to get the operation and parameters needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "search",
                            "get_workflow",
                            "get_field_types",
                            "get_pattern",
                            "get_quirks",
                            "list_all",
                            "get_api_help",
                        ],
                    },
                    "tenant": {
                        "type": "string",
                        "description": "Tenant key (e.g., 'demo'). Required.",
                    },
                },
                "required": ["operation", "tenant"],
                "additionalProperties": True,
            },
        },
    ]
    return tools


def build_prompts() -> List[Dict[str, Any]]:
    """Build the list of MCP prompts exposed by this server."""
    return [
        {
            "name": "therefore-help",
            "description": (
                "Get help with Therefore API. Answers common questions like: "
                "How do I query documents? How to get customer ID? How to summarize logs? "
                "What's the structure for field types? Why isn't something working?"
            ),
            "arguments": [
                {
                    "name": "question",
                    "description": "Your question about Therefore API",
                    "required": False,
                },
            ],
        },
        {
            "name": "query-documents",
            "description": (
                "Step-by-step guide for querying Therefore documents with filters, "
                "paginating results, and accessing table data."
            ),
            "arguments": [
                {
                    "name": "category",
                    "description": "Category name or number to query",
                    "required": False,
                },
                {
                    "name": "filter_field",
                    "description": "Field to filter on",
                    "required": False,
                },
            ],
        },
        {
            "name": "create-document",
            "description": (
                "Step-by-step guide for creating a Therefore document using the "
                "4-step web-client flow (GetCategoryInfo → PreprocessIndexData → "
                "EvaluateConditionalProperties → CreateDocument)."
            ),
            "arguments": [
                {
                    "name": "category",
                    "description": "Category name or number to create document in",
                    "required": False,
                },
            ],
        },
        {
            "name": "troubleshoot",
            "description": (
                "Help troubleshoot common Therefore API issues. Searches known quirks "
                "and provides workarounds for: keyword fields, table data, user accounts, "
                "query sessions, and more."
            ),
            "arguments": [
                {
                    "name": "problem",
                    "description": "Description of the problem you're experiencing",
                    "required": False,
                },
            ],
        },
        {
            "name": "create-category",
            "description": (
                "Interactive guide for creating a new Therefore category configuration. "
                "Walks through gathering requirements, building a structured spec, and "
                "generating the delta XML via the generate_category_config tool."
            ),
            "arguments": [
                {
                    "name": "description",
                    "description": "Optional starting description of the category to create.",
                    "required": False,
                },
            ],
        },
    ]


class MCPServer:
    def __init__(
        self,
        clients: Dict[str, ThereforeClient],
        default_tenant: Optional[str],
        tenant_labels: Dict[str, str],
        tenant_assignee_aliases: Optional[Dict[str, List[str]]] = None,
        client_access: Optional[Dict[str, List[str]]] = None,
    ):
        self.clients = clients
        self.default_tenant = default_tenant
        self._last_tenant: Optional[str] = default_tenant
        self.tenant_labels = tenant_labels
        self.tenant_assignee_aliases = tenant_assignee_aliases or {}
        self.client_access = client_access or {}
        self._current_client_key: Optional[str] = None
        self._current_client_ip: Optional[str] = None
        self.tools = build_tools()
        self.prompts = build_prompts()
        cache_dir = os.environ.get("THEREFORE_CACHE_DIR") or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache"
        )
        os.makedirs(cache_dir, exist_ok=True)
        self._category_cache: Dict[str, Dict[str, Any]] = {}
        self._category_cache_ts: Dict[str, float] = {}
        self._category_cache_ttl: int = 300
        self._category_cache_path = os.path.join(
            cache_dir, "category_cache_{tenant}.json"
        )
        self._field_cache: Dict[str, Dict[int, Dict[str, Any]]] = {}
        self._field_cache_ts: Dict[str, Dict[int, float]] = {}
        self._field_cache_ttl: int = 300
        self._field_cache_path = os.path.join(cache_dir, "field_cache_{tenant}.json")
        self._keyword_dict_cache: Dict[str, Dict[str, Any]] = {}
        self._keyword_dict_cache_ts: Dict[str, float] = {}
        self._keyword_dict_cache_ttl: int = 300
        self._keyword_dict_cache_path = os.path.join(
            cache_dir, "keyword_dictionary_cache_{tenant}.json"
        )

    def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = msg.get("method")
        msg_id = msg.get("id")
        params = msg.get("params") or {}

        if method == "initialize":
            return _result_response(
                msg_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {"name": "therefore-mcp", "version": "0.1.0"},
                },
            )
        if method in ("initialized", "notifications/initialized"):
            return None
        if method == "tools/list":
            return _result_response(msg_id, {"tools": self.tools})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                result = self._call_tool(name, args)
                return _result_response(msg_id, _tool_content(result))
            except Exception as e:
                return _result_response(
                    msg_id,
                    {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {"error": str(e), "trace": traceback.format_exc()},
                                    indent=2,
                                ),
                            }
                        ],
                        "isError": True,
                    },
                )
        if method == "ping":
            return _result_response(msg_id, {})
        if method == "prompts/list":
            return _result_response(msg_id, {"prompts": self.prompts})
        if method == "prompts/get":
            try:
                result = self._get_prompt(params)
                return _result_response(msg_id, result)
            except Exception as e:
                return _error_response(msg_id, -32602, str(e))

        # Silently ignore any other notifications (no id → no response expected).
        if msg_id is None:
            return None

        return _error_response(msg_id, -32601, f"Method not found: {method}")

    def _get_prompt(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "create-category":
            return self._prompt_create_category(arguments)
        raise ValueError(f"Unknown prompt: {name}")

    def _prompt_create_category(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        description = arguments.get("description", "")
        prompt_text = """\
You are helping the user create a new Therefore™ category configuration.

## Supported Field Types
- **text**: Free-text string field. Optional `length` (default 50).
- **number**: Integer field. Optional `length` (default 10).
- **decimal**: Decimal/currency field. Optional `length` (default 10), `scale` (default 2).
- **date**: Date field.
- **keyword_single**: Single-select keyword dropdown backed by a dictionary.
- **table**: Table/grid with typed columns.

## JSON Spec Format
```json
{
  "category": {
    "name": "Invoice",
    "folder": "Accounting",
    "description": "Supplier invoices",
    "folder_conflict_policy": "use-existing",
    "dictionary_conflict_policy": "use-existing"
  },
  "fields": [
    {"name": "Invoice Number", "type": "text", "length": 30},
    {"name": "Invoice Date", "type": "date"},
    {"name": "Amount", "type": "decimal", "length": 12, "scale": 2},
    {"name": "Quantity", "type": "number"},
    {
      "name": "Status",
      "type": "keyword_single",
      "dictionary": {
        "mode": "create",
        "name": "Invoice Status",
        "keywords": ["Draft", "Pending", "Approved", "Paid"]
      }
    },
    {
      "name": "Line Items",
      "type": "table",
      "columns": [
        {"name": "Description", "type": "text", "length": 100},
        {"name": "Qty", "type": "number"},
        {"name": "Unit Price", "type": "decimal", "scale": 2},
        {"name": "Total", "type": "decimal", "scale": 2}
      ]
    }
  ]
}
```

## Dictionary Modes
- `"create"`: Create a new keyword dictionary with the given keywords.
- `"existing"`: Reference an existing dictionary by name (must exist in baseline or tenant).

## Conflict Policies
- `folder_conflict_policy`: What to do when a folder with the same name exists.
  - `"use-existing"` (default): Reuse the existing folder.
  - `"unique"`: Create with a unique suffix.
  - `"error"`: Fail if exists.
- `dictionary_conflict_policy`: Same options, for keyword dictionaries.

## Workflow
1. **Gather requirements**: Ask the user what category they want, what fields it needs, which folder it belongs in.
2. **Build the spec**: Construct the JSON spec object with all fields, types, and dictionaries.
3. **Generate**: Call `therefore_categories` with `operation: "generate_config"` and the `spec` parameter.
4. **Review**: Present the result to the user — the generated XML content and output file path.

Keep it conversational. Ask clarifying questions if the user's requirements are ambiguous.
"""
        if description:
            prompt_text += f"\n## Starting Point\nThe user provided this initial description:\n\n{description}\n"

        return {
            "description": "Interactive guide for creating a Therefore category configuration.",
            "messages": [
                {
                    "role": "user",
                    "content": {
                        "type": "text",
                        "text": prompt_text,
                    },
                },
            ],
        }

    def _audit_log(self, tool_name: str, tenant: str, args: Dict[str, Any]) -> None:
        """Log tool execution for security auditing."""
        client_label = "global_admin"
        if self._current_client_key:
            # Mask the token for the log (show only last 4 chars)
            client_label = f"client_key(...{self._current_client_key[-4:]})"
        
        ip_label = self._current_client_ip or "unknown_ip"
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Scrub sensitive fields from args before logging
        safe_args = args.copy()
        for secret_key in ["password", "token", "security_token", "payload", "file_data_base64", "FileDataBase64JSON"]:
             if secret_key in safe_args:
                 safe_args[secret_key] = "[REDACTED]"
        
        # Handle nested streams in create_document
        if "streams" in safe_args and isinstance(safe_args["streams"], list):
             scrubbed_streams = []
             for s in safe_args["streams"]:
                 s_copy = s.copy()
                 for sk in ["FileDataBase64JSON", "file_data_base64"]:
                     if sk in s_copy:
                         s_copy[sk] = "[REDACTED]"
                 scrubbed_streams.append(s_copy)
             safe_args["streams"] = scrubbed_streams

        audit_msg = (
            f"[AUDIT] {timestamp} | Client: {client_label} | IP: {ip_label} | "
            f"Tenant: {tenant} | Tool: {tool_name} | Args: {json.dumps(safe_args)}"
        )
        print(audit_msg, file=sys.stderr, flush=True)

    def _call_tool(self, name: str, args: Dict[str, Any]) -> Any:
        if name == "therefore_connect":
            # No tenant to resolve yet - this operation is what CREATES one.
            client_label = "global_admin"
            if self._current_client_key:
                client_label = f"client_key(...{self._current_client_key[-4:]})"
            safe_args = {k: ("[REDACTED]" if k == "password" else v) for k, v in args.items()}
            print(
                f"[AUDIT] {datetime.now(timezone.utc).isoformat()} | Client: {client_label} | "
                f"IP: {self._current_client_ip or 'unknown_ip'} | Tool: therefore_connect | "
                f"Args: {json.dumps(safe_args)}",
                file=sys.stderr, flush=True,
            )
            return self._connect_tenant(args)

        if name == "ask_therefore_expert":
            # The router is pure routing logic - it never touches `client` - so it must
            # not hard-fail just because no tenant is configured/allowed yet. A brand
            # new caller with zero registered tenants needs to be able to ask "how do
            # I connect" and get pointed at therefore_connect, not an error. But if the
            # caller DID name a specific tenant and it's not one we recognize, that's
            # worth surfacing rather than silently discarding - it's very likely a typo
            # or a tenant that still needs therefore_connect, not "no tenant given".
            tenant_arg = args.get("tenant") or args.get("tenant_name") or args.get("tenantName")
            tenant_warning = None
            try:
                tenant = self._resolve_tenant(args)
            except ValueError as e:
                tenant = None
                if tenant_arg:
                    tenant_warning = str(e)
            self._audit_log(name, tenant or "(none)", args)
            client = self.clients.get(tenant) if tenant else None
            return self._ask_therefore_expert(args, tenant, client, tenant_warning=tenant_warning)

        tenant = self._resolve_tenant(args)
        self._audit_log(name, tenant, args)
        client = self.clients[tenant]
        if name == "therefore_system":
            return self._dispatch_system(args, tenant, client)
        if name == "therefore_categories":
            return self._dispatch_categories(args, tenant, client)
        if name == "therefore_documents":
            return self._dispatch_documents(args, tenant, client)
        if name == "therefore_query":
            return self._dispatch_query(args, tenant, client)
        if name == "therefore_workflow":
            return self._dispatch_workflow(args, tenant, client)
        if name == "therefore_users":
            return self._dispatch_users(args, tenant, client)
        if name == "therefore_keywords":
            return self._dispatch_keywords(args, tenant, client)
        if name == "therefore_knowledge":
            return self._dispatch_knowledge(args, tenant, client)
        raise ValueError(f"Unknown tool: {name}")

    def _connect_tenant(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new tenant/login at runtime (see therefore_connect's tool
        description for the full contract)."""
        username = args.get("username")
        password = args.get("password")
        if not username or not password:
            raise ValueError("therefore_connect requires 'username' and 'password'.")

        tenant_name = (args.get("tenant_name") or "").strip() or None
        base_url = (args.get("base_url") or "").strip() or None
        if not base_url and not tenant_name:
            raise ValueError(
                "therefore_connect requires either 'tenant_name' (Therefore Online "
                "subdomain shorthand) or an explicit 'base_url'."
            )
        if not base_url:
            base_url = f"https://{tenant_name}.thereforeonline.com/theservice/v0001/restun"

        key = normalize_tenant_key(str(args.get("tenant_key") or tenant_name or base_url))
        if not key:
            raise ValueError("Could not derive a tenant_key - pass one explicitly.")

        label = str(args.get("label") or args.get("tenant_key") or tenant_name or key)
        auth_method = str(args.get("auth_method") or "Basic")

        cfg = ThereforeConfig(
            base_url=base_url,
            auth_method=auth_method,
            username=str(username),
            password=str(password),
            tenant_name=tenant_name,
        )
        client = ThereforeClient(cfg)

        # Fail fast on bad credentials/URL instead of registering a client that will
        # 401/500 on first real use.
        try:
            client.get_connection_token()
        except Exception as e:
            raise ValueError(
                f"Could not connect to '{base_url}' as '{username}': {e}"
            ) from None

        self.clients[key] = client
        self.tenant_labels[key] = label

        # In multi-client HTTP mode, scope a dynamically-registered tenant to the caller
        # that registered it rather than exposing it to every other API key on this
        # server - matches the existing client_access allowlist model.
        if self._current_client_key and self.client_access:
            self.client_access.setdefault(self._current_client_key, [])
            if key not in self.client_access[self._current_client_key]:
                self.client_access[self._current_client_key].append(key)

        self._last_tenant = key
        return {
            "connected": True,
            "tenant_key": key,
            "label": label,
            "base_url": base_url,
            "message": (
                f"Connected and verified. Use \"tenant\": \"{key}\" on subsequent tool "
                f"calls (or omit it - '{key}' is now the default for this session)."
            ),
        }

    def _dispatch_system(self, args, tenant, client):
        op = args.get("operation")
        if op == "get_customer_id":
            return client.get_system_customer_id()
        if op == "get_connected_user":
            return client.get_connected_user(bool(args.get("create", False)))
        if op == "get_version":
            return client.get_web_api_server_version()
        if op == "get_connection_token":
            return client.get_connection_token()
        if op == "get_connection_token_from_adfs":
            return client.get_connection_token_from_adfs(
                security_token=str(args["security_token"]),
                connect_mode=args.get("connect_mode"),
            )
        if op == "get_domain_info":
            return client.get_domain_info()
        if op == "get_discovery_info":
            return client.get_client_discovery_info()
        if op == "get_permission_constants":
            return client.get_permission_constants()
        if op == "get_role_permission_constants":
            return client.get_role_permission_constants()
        if op == "get_objects_list":
            return client.get_objects_list(args["load_items_list"])
        if op == "get_objects":
            resp = client.get_objects(
                flags=int(args["flags"]), obj_type=int(args["obj_type"])
            )
            resp["items"] = self._extract_object_items(resp)
            return resp
        if op == "get_statistics":
            query_type = self._normalize_statistics_query_type(args.get("query_type"))
            return client.execute_statistics_query(
                query_type=query_type,
                restrict_to_obj_no=args.get("restrict_to_obj_no"),
                restrict_to_user=args.get("restrict_to_user"),
            )
        if op == "get_logfiles":
            return self._get_logfiles(args, tenant, client)
        if op == "get_login_history":
            return self._get_login_history(args, tenant, client)
        if op == "call_endpoint":
            return client.call_endpoint(
                endpoint=args["endpoint"], payload=args.get("payload")
            )
        raise ValueError(f"Unknown operation '{op}' for therefore_system")

    def _dispatch_categories(self, args, tenant, client):
        op = args.get("operation")
        if op == "get_tree":
            return client.get_categories_tree(args.get("payload"))
        if op == "get_info":
            return client.get_category_info(int(args["category_no"]))
        if op == "resolve":
            return self._resolve_category(args, tenant, client)
        if op == "list_fields":
            return self._list_category_fields(int(args["category_no"]), tenant, client)
        if op == "resolve_field":
            return self._resolve_field(args, tenant, client)
        if op == "get_referenced_table_info":
            return client.get_referenced_table_info(int(args["data_type_no"]))
        if op == "execute_dependent_fields_query":
            return self._execute_dependent_fields_query(args, client)
        if op == "fill_dependent_fields":
            return self._fill_dependent_fields(args, client)
        if op == "resolve_referenced_field":
            return self._resolve_referenced_field(args, client)
        if op == "query_referenced_table":
            return self._query_referenced_table(args, tenant, client)
        if op == "generate_config":
            return self._generate_category_config(args, tenant, client)
        raise ValueError(f"Unknown operation '{op}' for therefore_categories")

    def _dispatch_documents(self, args, tenant, client):
        op = args.get("operation")
        if op == "get":
            return client.get_document(
                doc_no=int(args["doc_no"]),
                include_index_data=bool(args.get("include_index_data", True)),
                include_streams_info=bool(args.get("include_streams_info", True)),
                include_streams_data=bool(args.get("include_streams_data", False)),
                include_checkout_status=bool(
                    args.get("include_checkout_status", False)
                ),
                include_access_mask=bool(args.get("include_access_mask", False)),
            )
        if op == "get_index_data":
            return client.get_document_index_data(int(args["doc_no"]))
        if op == "get_properties":
            return client.get_document_properties(
                doc_no=int(args["doc_no"]),
                version_no=int(args.get("version_no", 0)),
                is_doc_title_needed=bool(args.get("is_doc_title_needed", False)),
            )
        if op == "get_history":
            return client.get_document_history(int(args["doc_no"]))
        if op == "get_checkout_status":
            return client.get_document_checkout_status(int(args["doc_no"]))
        if op == "get_versions":
            return client.get_document_versions(int(args["doc_no"]))
        if op == "get_converted_streams":
            return self._get_converted_doc_streams(args, tenant, client)
        if op == "get_stream":
            return client.get_document_stream(
                doc_no=int(args["doc_no"]),
                stream_no=int(args["stream_no"]),
                version_no=args.get("version_no"),
                retrieve_reason=args.get("retrieve_reason"),
            )
        if op == "get_stream_raw":
            raw_bytes = client.get_document_stream_raw(
                doc_no=int(args["doc_no"]),
                stream_no=int(args["stream_no"]),
                version_no=args.get("version_no"),
                retrieve_reason=args.get("retrieve_reason"),
                timeout_override=args.get("timeout_override"),
            )
            # Return base64-encoded for JSON serialization
            return {
                "file_data_base64": base64.b64encode(raw_bytes).decode('ascii'),
                "file_size_bytes": len(raw_bytes),
                "note": "File content returned as base64-encoded string. Use get_stream for structured JSON response with metadata.",
            }
        if op == "create":
            category_no = int(args["category_no"])
            check_in_comments = args.get("check_in_comments", "")
            with_auto_append_mode = int(args.get("with_auto_append_mode", 0))
            do_fill_dependent_fields = bool(args.get("do_fill_dependent_fields", True))
            run_webclient_flow = bool(args.get("run_webclient_flow", True))
            index_data_items = args.get("index_data_items") or []
            streams = []
            for s in args.get("streams") or []:
                file_name = s.get("file_name")
                file_data_base64 = s.get("file_data_base64")
                file_data_text = s.get("file_data_text")
                if file_data_text and not file_data_base64:
                    file_data_base64 = base64.b64encode(
                        file_data_text.encode("utf-8")
                    ).decode("ascii")
                if not file_name:
                    raise ValueError("stream missing file_name")
                if not file_data_base64:
                    raise ValueError(
                        "stream missing file_data_base64 or file_data_text"
                    )
                streams.append(
                    {
                        "FileName": file_name,
                        "FileDataBase64JSON": file_data_base64,
                        "NewStreamInsertMode": 0,
                    }
                )
            if not streams:
                content_text = args.get("content_text")
                if content_text is None:
                    raise ValueError("Either streams or content_text must be provided")
                filename = args.get("content_filename") or "document.txt"
                streams.append(
                    ThereforeClient.make_stream_from_text(filename, content_text)
                )
            return client.create_document(
                category_no=category_no,
                streams=streams,
                index_data_items=index_data_items,
                check_in_comments=check_in_comments,
                with_auto_append_mode=with_auto_append_mode,
                do_fill_dependent_fields=do_fill_dependent_fields,
                run_webclient_flow=run_webclient_flow,
            )
        if op == "update":
            return self._update_document(args, tenant, client)
        if op == "update_index_data":
            return self._update_document_index_data(args, tenant, client)
        if op == "add_streams":
            return self._add_streams_to_document(args, tenant, client)
        if op == "delete":
            return client.delete_document(int(args["doc_no"]))
        if op == "check_out":
            return client.check_out_document(
                doc_no=int(args["doc_no"]), version_no=int(args.get("version_no", 0))
            )
        if op == "check_in":
            return client.check_in_document(
                doc_no=int(args["doc_no"]),
                check_in_comments=args.get("check_in_comments"),
                version_no=int(args.get("version_no", 0)),
            )
        if op == "undo_check_out":
            return client.undo_check_out_document(
                doc_no=int(args["doc_no"]), version_no=int(args.get("version_no", 0))
            )
        if op == "add_comment":
            return client.add_comment(
                doc_no=int(args["doc_no"]),
                comment_text=str(args["comment_text"]),
                obj_type=int(args.get("obj_type", 2)),
            )
        if op == "edit_comment":
            return client.edit_comment(
                doc_no=int(args["doc_no"]),
                comment_id=str(args["comment_id"]),
                comment_text=str(args["comment_text"]),
                obj_type=int(args.get("obj_type", 2)),
            )
        if op == "get_comments":
            return client.get_comments(
                doc_no=int(args["doc_no"]), obj_type=int(args.get("obj_type", 2))
            )
        raise ValueError(f"Unknown operation '{op}' for therefore_documents")

    def _dispatch_query(self, args, tenant, client):
        op = args.get("operation")
        if op == "search":
            query = args["query"]
            categories = self._extract_category_list(query)
            if categories and len(categories) > 1:
                base_query = dict(query)
                for key in (
                    "CategoryNos",
                    "CategoryIDs",
                    "CategoryIds",
                    "Categories",
                    "CategoryList",
                ):
                    base_query.pop(key, None)
                if isinstance(base_query.get("CategoryNo"), (list, tuple, set, str)):
                    base_query.pop("CategoryNo", None)
                row_block_size = int(base_query.get("RowBlockSize") or 1000)
                max_rows = int(base_query.get("MaxRows") or 2147483647)
                queries = []
                for cat in categories:
                    q = dict(base_query)
                    q["CategoryNo"] = int(cat)
                    queries.append(q)
                return client.execute_async_multi_query_all(
                    queries=queries,
                    full_text=args.get("full_text"),
                    row_block_size=row_block_size,
                    max_rows=max_rows,
                )
            return client.execute_single_query(
                query=query, full_text=args.get("full_text")
            )
        if op == "search_async":
            row_block_size = int(args.get("row_block_size", 1000))
            max_rows = int(args.get("max_rows", 2147483647))
            auto_fetch_all = bool(args.get("auto_fetch_all", True))
            if auto_fetch_all:
                return client.execute_async_single_query_all(
                    query=args["query"],
                    full_text=args.get("full_text"),
                    row_block_size=row_block_size,
                    max_rows=max_rows,
                )
            return client.execute_async_single_query(
                query=args["query"], full_text=args.get("full_text")
            )
        if op == "search_multi":
            row_block_size = int(args.get("row_block_size", 1000))
            max_rows = int(args.get("max_rows", 2147483647))
            auto_fetch_all = bool(args.get("auto_fetch_all", True))
            if auto_fetch_all:
                return client.execute_async_multi_query_all(
                    queries=args["queries"],
                    full_text=args.get("full_text"),
                    row_block_size=row_block_size,
                    max_rows=max_rows,
                )
            return client.execute_async_multi_query(
                queries=args["queries"], full_text=args.get("full_text")
            )
        if op == "search_fulltext":
            return client.execute_full_text_query(
                search=args["search"],
                categories=args.get("categories"),
                max_rows=int(args.get("max_rows", 100)),
                include_index_data=bool(args.get("include_index_data", False)),
                case_no=int(args.get("case_no", 0)),
            )
        if op == "get_next_rows":
            return client.get_next_single_query_rows(
                query_id=int(args["query_id"]),
                row_block_size=int(args["row_block_size"]),
            )
        if op == "get_next_multi_rows":
            return client.get_next_multi_query_rows(
                query_id=int(args["query_id"]),
                row_block_size=int(args["row_block_size"]),
            )
        if op == "release":
            return client.release_single_query(int(args["query_id"]))
        if op == "release_multi":
            return client.release_multi_query(int(args["query_id"]))
        raise ValueError(f"Unknown operation '{op}' for therefore_query")

    def _dispatch_workflow(self, args, tenant, client):
        op = args.get("operation")
        if op == "query_all":
            return self._execute_workflow_query_for_all(args, tenant, client)
        if op == "query_process":
            return self._execute_workflow_query_for_process(args, tenant, client)
        if op == "get_my_tasks":
            return self._get_my_workflow_tasks(args, tenant, client)
        if op == "get_my_instances":
            a = dict(args or {})
            a["filter_to_user"] = True
            output = self._get_workflow_instances_core(a, tenant, client)
            output["tasks"] = output.get("instances", [])
            return output
        if op == "get_all_instances":
            a = dict(args or {})
            a["filter_to_user"] = False
            output = self._get_workflow_instances_core(a, tenant, client)
            output["tasks"] = output.get("instances", [])
            return output
        if op == "get_user_instances":
            a = dict(args or {})
            a["filter_to_user"] = True
            output = self._get_workflow_instances_core(a, tenant, client)
            output["tasks"] = output.get("instances", [])
            return output
        if op == "get_instance":
            return client.get_workflow_instance(
                instance_no=int(args["instance_no"]),
                token_no=int(args.get("token_no", 0)),
                is_access_mask_needed=bool(args.get("is_access_mask_needed", False)),
                load_history=bool(args.get("load_history", False)),
            )
        if op == "get_process":
            return client.get_workflow_process(
                process_no=int(args["process_no"]),
                version_no=int(args.get("version_no", 0)),
                load_tasks=bool(args.get("load_tasks", True)),
                is_access_mask_needed=bool(args.get("is_access_mask_needed", False)),
            )
        if op == "get_task_settings":
            return client.get_workflow_task_settings(
                task_no=int(args["task_no"]),
                process_no=int(args["process_no"]),
                version_no=int(args.get("version_no", 0)),
                setting_names=args.get("setting_names"),
            )
        if op == "get_history":
            return client.get_workflow_history(
                instance_no=int(args["instance_no"]),
                block_size=int(args.get("block_size", 1000)),
                include_routing_info=bool(args.get("include_routing_info", True)),
                max_creation_date=args.get("max_creation_date"),
                seq_pos=int(args.get("seq_pos", 0)),
            )
        if op == "get_linked":
            return client.get_linked_workflows_for_doc(
                doc_no=int(args["doc_no"]),
                wf_doc_link_type=int(args.get("wf_doc_link_type", 0)),
            )
        if op == "complete_task":
            return client.complete_task(
                workflow_instance_token=str(args["workflow_instance_token"]),
                task_no=int(args["task_no"]),
                user_decision=args.get("user_decision"),
                index_data_items=args.get("index_data_items"),
            )
        if op == "claim":
            return client.claim_workflow_instance(
                workflow_instance_token=str(args["workflow_instance_token"]),
                task_no=int(args["task_no"])
                if args.get("task_no") is not None
                else None,
            )
        if op == "disclaim":
            return client.disclaim_workflow_instance(
                workflow_instance_token=str(args["workflow_instance_token"]),
                task_no=int(args["task_no"])
                if args.get("task_no") is not None
                else None,
            )
        if op == "delegate":
            return client.delegate_workflow_instance(
                workflow_instance_token=str(args["workflow_instance_token"]),
                user_id=int(args["user_id"]),
                task_no=int(args["task_no"])
                if args.get("task_no") is not None
                else None,
            )
        if op == "get_case_definition":
            return client.get_case_definition(int(args["case_definition_no"]))
        if op == "create_case":
            return client.create_case(
                case_definition_no=int(args["case_definition_no"]),
                index_data_items=args.get("index_data_items"),
            )
        if op == "get_case":
            return client.get_case(int(args["case_no"]))
        if op == "get_case_documents":
            return client.get_case_documents(
                case_no=int(args["case_no"]), max_rows=int(args.get("max_rows", 1000))
            )
        if op == "get_case_history":
            return client.get_case_history(int(args["case_no"]))
        if op == "execute_dependent_fields_query":
            return self._execute_dependent_fields_query(args, client)
        if op == "fill_dependent_fields":
            return self._fill_dependent_fields(args, client)
        if op == "save_case_index_data_quick":
            return client.save_case_index_data_quick(
                case_no=int(args["case_no"]),
                index_data_items=args["index_data_items"],
                check_in_comments=str(args.get("check_in_comments", "")),
            )
        if op == "save_case_index_data":
            return client.save_case_index_data(
                case_no=int(args["case_no"]),
                index_data_items=args["index_data_items"],
                check_in_comments=str(args.get("check_in_comments", "")),
                do_fill_dependent_fields=bool(args.get("do_fill_dependent_fields", True)),
                last_change_time=args.get("last_change_time"),
                last_change_time_iso=args.get("last_change_time_iso"),
            )
        raise ValueError(f"Unknown operation '{op}' for therefore_workflow")

    def _dispatch_users(self, args, tenant, client):
        op = args.get("operation")
        if op == "search":
            domain_names = args.get("domain_names")
            if domain_names is None:
                try:
                    domain_info = client.get_domain_info() or {}
                    domain_names = domain_info.get("DomainNames") or []
                except Exception:
                    domain_names = None
            return client.execute_users_query(
                query=args["query"],
                domain_names=domain_names,
                flags=int(args.get("flags", 5)),
            )
        if op == "get_from_group":
            return client.get_users_from_group(
                group_id=args.get("group_id"),
                group_name=args.get("group_name"),
                domain_name=args.get("domain_name"),
            )
        if op == "get_details":
            return client.get_user_details(int(args["user_or_group_id"]))
        if op == "create":
            return client.create_user(
                user_name=str(args["user_name"]),
                full_name=str(args["full_name"]),
                email=args.get("email"),
                password=args.get("password"),
                domain_name=args.get("domain_name"),
            )
        if op == "update_groups":
            return client.update_user_group_assignment(
                user_id=int(args["user_id"]), group_ids=args.get("group_ids")
            )
        if op == "get_groups":
            return client.get_user_group_assignment(int(args["user_id"]))
        if op == "set_password":
            return client.set_user_password(
                user_id=int(args["user_id"]), new_password=str(args["new_password"])
            )
        if op == "change_password":
            return client.change_user_password(
                old_password=str(args["old_password"]),
                new_password=str(args["new_password"]),
            )
        if op == "reset_password":
            return client.reset_user_password(
                user_id=int(args["user_id"]),
                send_email=bool(args.get("send_email", True)),
            )
        if op == "delete_portal":
            return client.delete_portal_user(int(args["user_id"]))
        if op == "save_portal":
            return client.save_portal_user(
                user_id=int(args["user_id"]),
                user_name=args.get("user_name"),
                full_name=args.get("full_name"),
                email=args.get("email"),
                is_active=args.get("is_active"),
            )
        if op == "move_license":
            return client.move_user_license(
                source_user_id=int(args["source_user_id"]),
                target_user_id=int(args["target_user_id"]),
            )
        if op == "get_settings":
            return client.get_user_settings(int(args["user_id"]))
        if op == "set_settings":
            return client.set_user_settings(
                user_id=int(args["user_id"]), settings=args["settings"]
            )
        raise ValueError(f"Unknown operation '{op}' for therefore_users")

    def _dispatch_keywords(self, args, tenant, client):
        op = args.get("operation")
        if op == "get_by_field":
            return client.get_keywords_by_field_no(
                field_no=int(args["field_no"]),
                category_no=args.get("category_no"),
                case_definition_no=args.get("case_definition_no"),
                dependent_field_filter_value=args.get("dependent_field_filter_value"),
                show_deactivated_keywords=args.get("show_deactivated_keywords"),
                index_data_items=args.get("index_data_items"),
                skip_loading_keyword_nos=args.get("skip_loading_keyword_nos"),
                max_rows=args.get("max_rows"),
            )
        if op == "get_by_dictionary":
            return client.get_keywords_by_key_dic(
                key_dic_no=int(args["key_dic_no"]),
                filter_value=args.get("filter_value"),
                max_values=args.get("max_values"),
                include_deactivated_keywords=args.get("include_deactivated_keywords"),
            )
        if op == "get_by_name":
            return self._get_keywords_by_dictionary_name(args, tenant, client)
        if op == "validate":
            return client.validate_keywords(
                field_no=int(args["field_no"]),
                keywords=args.get("keywords") or [],
                is_filter_mode=args.get("is_filter_mode"),
            )
        if op == "add":
            return self._add_dictionary_keyword(args, tenant, client)
        if op == "update":
            return self._update_dictionary_keyword(args, tenant, client)
        if op == "delete":
            return self._delete_dictionary_keyword(args, tenant, client)
        if op == "deactivate":
            return self._deactivate_dictionary_keyword(args, tenant, client)
        raise ValueError(f"Unknown operation '{op}' for therefore_keywords")

    def _dispatch_knowledge(self, args, tenant, client):
        op = args.get("operation")
        if op == "search":
            return self._search_therefore_knowledge(args)
        if op == "get_workflow":
            return self._get_therefore_workflow(args)
        if op == "get_field_types":
            return self._get_therefore_field_type_info(args)
        if op == "get_pattern":
            return self._get_therefore_common_pattern(args)
        if op == "get_quirks":
            # Remap search_term to search
            args["search"] = args.get("search_term")
            return self._get_therefore_api_quirks(args)
        if op == "list_all":
            return self._list_therefore_knowledge()
        if op == "get_api_help":
            # Remap api_operation to operation
            args["operation"] = args.get("api_operation")
            return self._get_therefore_api_help(args, tenant, client)
        raise ValueError(f"Unknown operation '{op}' for therefore_knowledge")

    def _execute_workflow_query_for_all(self, args, tenant, client):
        debug_enabled = bool(args.get("debug", False))
        debug_log_path = args.get("debug_log_path")
        debug_progress_every = int(args.get("debug_progress_every") or 500)
        debug_info: Dict[str, Any] = (
            {
                "workflow_query": {},
                "instance_details": {},
            }
            if debug_enabled
            else {}
        )
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "start",
                    "workflow_flags": args.get("workflow_flags"),
                    "max_rows": args.get("max_rows"),
                    "detail_mode": args.get("instance_detail_mode"),
                },
            )
        if args.get("max_rows") is None:
            max_rows = self._default_workflow_max_rows(client)
        else:
            max_rows = int(args.get("max_rows", 1000))
        workflow_flags = self._normalize_workflow_flags(args.get("workflow_flags", 0))
        start = time.time()
        try:
            resp = client.execute_workflow_query_for_all(
                workflow_flags=workflow_flags,
                max_rows=max_rows,
            )
        except Exception as exc:
            if debug_enabled:
                debug_info["workflow_query"] = {
                    "workflow_flags": workflow_flags,
                    "max_rows": max_rows,
                    "duration_ms": int((time.time() - start) * 1000),
                    "error": str(exc),
                }
                if debug_log_path:
                    self._debug_log(
                        debug_log_path,
                        {
                            "event": "workflow_query_error",
                            "workflow_flags": workflow_flags,
                            "max_rows": max_rows,
                            "error": str(exc),
                        },
                    )
                return {"error": str(exc), "debug": debug_info}
            raise
        if debug_enabled:
            debug_info["workflow_query"] = {
                "workflow_flags": workflow_flags,
                "max_rows": max_rows,
                "duration_ms": int((time.time() - start) * 1000),
            }
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "workflow_query_done",
                    "workflow_flags": workflow_flags,
                    "max_rows": max_rows,
                    "duration_ms": int((time.time() - start) * 1000),
                },
            )
        if not args.get("include_instance_details"):
            output = (
                {"workflow_query": resp, "debug": debug_info} if debug_enabled else resp
            )
            if debug_log_path:
                self._debug_log(debug_log_path, {"event": "done"})
            return output
        detail_mode = str(args.get("instance_detail_mode") or "summary").strip().lower()
        if detail_mode == "none":
            detail_mode = "summary"
        tasks, user_field_labels, _ = self._extract_workflow_tasks(resp)
        max_rows_reached = len(tasks) == max_rows
        details_start = time.time()
        details, detail_errors = self._fetch_workflow_instance_details(
            client,
            tasks,
            max_workers=int(args.get("max_instance_workers") or 4),
            is_access_mask_needed=bool(args.get("is_access_mask_needed", False)),
            load_history=bool(args.get("load_history", False)),
            debug_log_path=debug_log_path,
            debug_progress_every=debug_progress_every,
        )
        if debug_enabled:
            debug_info["instance_details"] = {
                "mode": detail_mode,
                "requested": len(tasks),
                "loaded": len(details),
                "failed": len(detail_errors),
                "duration_ms": int((time.time() - details_start) * 1000),
                "errors_sample": detail_errors[:10],
            }
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "instance_details_done",
                    "requested": len(tasks),
                    "loaded": len(details),
                    "failed": len(detail_errors),
                    "duration_ms": int((time.time() - details_start) * 1000),
                },
            )
        self._attach_instance_details(tasks, details, detail_errors, detail_mode)
        output = {
            "workflow_query": resp,
            "instances": tasks,
            "user_field_labels": user_field_labels,
            "max_rows": max_rows,
            "max_rows_reached": max_rows_reached,
            "total_count": len(tasks),
            "note": "Reached max_rows; results may be truncated. Increase max_rows to fetch more."
            if max_rows_reached
            else None,
            "instance_detail_mode": detail_mode,
            "instance_details_loaded": len(details),
            "instance_details_failed": len(detail_errors),
            "instance_detail_errors": detail_errors,
            "debug": debug_info if debug_enabled else None,
        }
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "done",
                    "total_count": len(tasks),
                    "max_rows_reached": max_rows_reached,
                },
            )
        return output

    def _execute_workflow_query_for_process(self, args, tenant, client):
        debug_enabled = bool(args.get("debug", False))
        debug_log_path = args.get("debug_log_path")
        debug_progress_every = int(args.get("debug_progress_every") or 500)
        debug_info: Dict[str, Any] = (
            {
                "workflow_query": {},
                "instance_details": {},
            }
            if debug_enabled
            else {}
        )
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "start",
                    "process_no": args.get("process_no"),
                    "workflow_flags": args.get("workflow_flags"),
                    "max_rows": args.get("max_rows"),
                    "detail_mode": args.get("instance_detail_mode"),
                },
            )
        if args.get("max_rows") is None:
            max_rows = self._default_workflow_max_rows(client)
        else:
            max_rows = int(args.get("max_rows", 1000))
        workflow_flags = self._normalize_workflow_flags(args.get("workflow_flags", 0))
        process_no = int(args["process_no"])
        start = time.time()
        try:
            resp = client.execute_workflow_query_for_process(
                process_no=process_no,
                workflow_flags=workflow_flags,
                max_rows=max_rows,
            )
        except Exception as exc:
            if debug_enabled:
                debug_info["workflow_query"] = {
                    "process_no": process_no,
                    "workflow_flags": workflow_flags,
                    "max_rows": max_rows,
                    "duration_ms": int((time.time() - start) * 1000),
                    "error": str(exc),
                }
                if debug_log_path:
                    self._debug_log(
                        debug_log_path,
                        {
                            "event": "workflow_query_error",
                            "process_no": process_no,
                            "workflow_flags": workflow_flags,
                            "max_rows": max_rows,
                            "error": str(exc),
                        },
                    )
                return {"error": str(exc), "debug": debug_info}
            raise
        if debug_enabled:
            debug_info["workflow_query"] = {
                "process_no": process_no,
                "workflow_flags": workflow_flags,
                "max_rows": max_rows,
                "duration_ms": int((time.time() - start) * 1000),
            }
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "workflow_query_done",
                    "process_no": process_no,
                    "workflow_flags": workflow_flags,
                    "max_rows": max_rows,
                    "duration_ms": int((time.time() - start) * 1000),
                },
            )
        if not args.get("include_instance_details"):
            output = (
                {"workflow_query": resp, "debug": debug_info} if debug_enabled else resp
            )
            if debug_log_path:
                self._debug_log(debug_log_path, {"event": "done"})
            return output
        detail_mode = str(args.get("instance_detail_mode") or "summary").strip().lower()
        if detail_mode == "none":
            detail_mode = "summary"
        tasks, user_field_labels, _ = self._extract_workflow_tasks(resp)
        max_rows_reached = len(tasks) == max_rows
        details_start = time.time()
        details, detail_errors = self._fetch_workflow_instance_details(
            client,
            tasks,
            max_workers=int(args.get("max_instance_workers") or 4),
            is_access_mask_needed=bool(args.get("is_access_mask_needed", False)),
            load_history=bool(args.get("load_history", False)),
            debug_log_path=debug_log_path,
            debug_progress_every=debug_progress_every,
        )
        if debug_enabled:
            debug_info["instance_details"] = {
                "mode": detail_mode,
                "requested": len(tasks),
                "loaded": len(details),
                "failed": len(detail_errors),
                "duration_ms": int((time.time() - details_start) * 1000),
                "errors_sample": detail_errors[:10],
            }
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "instance_details_done",
                    "requested": len(tasks),
                    "loaded": len(details),
                    "failed": len(detail_errors),
                    "duration_ms": int((time.time() - details_start) * 1000),
                },
            )
        self._attach_instance_details(tasks, details, detail_errors, detail_mode)
        output = {
            "workflow_query": resp,
            "instances": tasks,
            "user_field_labels": user_field_labels,
            "max_rows": max_rows,
            "max_rows_reached": max_rows_reached,
            "total_count": len(tasks),
            "note": "Reached max_rows; results may be truncated. Increase max_rows to fetch more."
            if max_rows_reached
            else None,
            "instance_detail_mode": detail_mode,
            "instance_details_loaded": len(details),
            "instance_details_failed": len(detail_errors),
            "instance_detail_errors": detail_errors,
            "debug": debug_info if debug_enabled else None,
        }
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "done",
                    "total_count": len(tasks),
                    "max_rows_reached": max_rows_reached,
                },
            )
        return output

    def _generate_category_config(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        import xml.etree.ElementTree as ET
        from generate import spec_from_mapping, parse_description, build_delta_xml

        spec_obj = args.get("spec")
        description = args.get("description")
        if spec_obj and description:
            raise ValueError("Provide either 'spec' or 'description', not both.")
        if not spec_obj and not description:
            raise ValueError(
                "Provide either 'spec' (structured JSON) or 'description' (natural language text)."
            )

        if spec_obj:
            spec = spec_from_mapping(spec_obj)
        else:
            spec = parse_description(description)

        # Baseline is only used when explicitly provided for diff-mode collision checks
        baseline_path = args.get("baseline_path")
        if baseline_path and not os.path.isfile(baseline_path):
            raise ValueError(f"Baseline file not found: {baseline_path}")

        # API check: reuse the existing authenticated client by default
        api_check = args.get("api_check", True)
        api_client = client if api_check else None

        tree = build_delta_xml(
            spec, baseline_path, api_client=api_client, interactive=False
        )
        xml_content = ET.tostring(tree.getroot(), encoding="unicode")

        # Write output file
        output_path = args.get("output_path")
        if not output_path:
            slug = re.sub(r"[^A-Za-z0-9]+", "_", spec.name).strip("_").lower()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = os.path.join(_REPO_ROOT, "docs", "notes", "generated_configs")
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f"{slug}-{timestamp}-delta.xml")

        with open(output_path, "w") as f:
            f.write(xml_content)

        field_names = [fld.name for fld in spec.fields]
        return {
            "xml_content": xml_content,
            "category_name": spec.name,
            "folder": spec.folder or "(auto-generated)",
            "fields_count": len(spec.fields),
            "field_names": field_names,
            "output_file": output_path,
            "note": (
                "Delta XML generated successfully. Import this file into Therefore using "
                "Administration > Configuration > Import to create the category."
            ),
        }

    # Knowledge base tool handlers
    def _ask_therefore_expert(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
        tenant_warning: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Expert router that returns the exact tool, operation, and parameters needed.
        Uses OPERATION_REGISTRY for comprehensive parameter information.
        """
        question = args["question"].lower()

        # A specific tenant was named but isn't configured/allowed. Don't silently fall
        # back and answer as if nothing was wrong - point straight at therefore_connect
        # so the caller can register it, since that's very likely what's actually needed.
        if tenant_warning:
            requested = args.get("tenant") or args.get("tenant_name") or args.get("tenantName")
            return {
                "question": args["question"],
                "warning": tenant_warning,
                "suggested_tool": "therefore_connect",
                "description": (
                    f"'{requested}' isn't a configured/accessible tenant yet. Register it "
                    "with therefore_connect, then ask your original question again."
                ),
                "call_with": {
                    "tenant_name": requested,
                    "username": "<required>",
                    "password": "<required>",
                },
                "all_parameters": {
                    "required": ["username", "password", "tenant_name (or base_url)"],
                    "optional": {
                        "tenant_key": "string - key to use as 'tenant' on later calls (defaults to a normalized tenant_name/base_url)",
                        "base_url": "string - explicit REST base URL, for on-prem or non-standard hosts",
                        "auth_method": "string - 'Basic' (default) or 'Bearer'",
                        "label": "string - display name",
                    },
                },
                "answer": (
                    f"{tenant_warning} Call therefore_connect with tenant_name (or "
                    f"base_url), username, and password to register '{requested}', then "
                    "retry your question with \"tenant\": \"<the returned tenant_key>\"."
                ),
            }

        # therefore_connect is a standalone tool with no "operation" - it can't live in
        # OPERATION_REGISTRY/tool_suggestions below, so handle connect-flavored
        # questions as a special case before the registry-driven matching.
        connect_phrases = (
            "connect to", "new tenant", "add tenant", "add a tenant", "different tenant",
            "another tenant", "switch tenant", "register tenant", "login as", "log in as",
            "different login", "different user", "different account", "new login",
            "new credentials", "different credentials",
        )
        if any(p in question for p in connect_phrases) or (
            "connect" in question and "tenant" in question
        ):
            return {
                "question": args["question"],
                "suggested_tool": "therefore_connect",
                "description": (
                    "Register a new tenant/login at runtime - no config file edits or "
                    "server restart needed. Works for any Therefore Online tenant or "
                    "on-prem server."
                ),
                "call_with": {
                    "tenant_name": "<Therefore Online subdomain, e.g. 'acme' - or use base_url for on-prem>",
                    "username": "<required>",
                    "password": "<required>",
                },
                "all_parameters": {
                    "required": ["username", "password", "tenant_name (or base_url)"],
                    "optional": {
                        "tenant_key": "string - key to use as 'tenant' on later calls (defaults to a normalized tenant_name/base_url)",
                        "base_url": "string - explicit REST base URL, for on-prem or non-standard hosts",
                        "auth_method": "string - 'Basic' (default) or 'Bearer'",
                        "label": "string - display name",
                    },
                },
                "answer": (
                    "Call therefore_connect with tenant_name (or base_url), username, and "
                    "password. It verifies the login and registers it under a tenant key "
                    "you can then pass as \"tenant\" on every other tool call."
                ),
            }

        # Expanded keyword -> tool+operation mapping
        tool_suggestions = {
            # System operations
            "customer id": {"tool": "therefore_system", "operation": "get_customer_id"},
            "client id": {"tool": "therefore_system", "operation": "get_customer_id"},
            "system id": {"tool": "therefore_system", "operation": "get_customer_id"},
            "tenant id": {"tool": "therefore_system", "operation": "get_customer_id"},
            "connected user": {"tool": "therefore_system", "operation": "get_connected_user"},
            "version": {"tool": "therefore_system", "operation": "get_version"},
            "logs": {"tool": "therefore_system", "operation": "get_logfiles"},
            "log files": {"tool": "therefore_system", "operation": "get_logfiles"},
            "login history": {"tool": "therefore_system", "operation": "get_login_history"},
            "statistics": {"tool": "therefore_system", "operation": "get_statistics"},
            "objects": {"tool": "therefore_system", "operation": "get_objects"},
            "adfs": {"tool": "therefore_system", "operation": "get_connection_token_from_adfs"},
            "sso": {"tool": "therefore_system", "operation": "get_connection_token_from_adfs"},
            "entra": {"tool": "therefore_system", "operation": "get_connection_token_from_adfs"},
            "azure ad": {"tool": "therefore_system", "operation": "get_connection_token_from_adfs"},
            "token exchange": {"tool": "therefore_system", "operation": "get_connection_token_from_adfs"},

            # Category operations
            "categories": {"tool": "therefore_categories", "operation": "get_tree"},
            "category tree": {"tool": "therefore_categories", "operation": "get_tree"},
            "list categories": {"tool": "therefore_categories", "operation": "get_tree"},
            "category info": {"tool": "therefore_categories", "operation": "get_info"},
            "fields": {"tool": "therefore_categories", "operation": "list_fields"},
            "list fields": {"tool": "therefore_categories", "operation": "list_fields"},
            "generate config": {"tool": "therefore_categories", "operation": "generate_config"},

            # Document operations
            "get document": {"tool": "therefore_documents", "operation": "get"},
            "create document": {"tool": "therefore_documents", "operation": "create"},
            "update document": {"tool": "therefore_documents", "operation": "update"},
            "delete document": {"tool": "therefore_documents", "operation": "delete"},
            "document history": {"tool": "therefore_documents", "operation": "get_history"},
            "download stream": {"tool": "therefore_documents", "operation": "get_stream"},
            "document stream": {"tool": "therefore_documents", "operation": "get_stream"},
            "get stream": {"tool": "therefore_documents", "operation": "get_stream"},
            "download file": {"tool": "therefore_documents", "operation": "get_stream_raw"},
            "raw stream": {"tool": "therefore_documents", "operation": "get_stream_raw"},
            "checkout": {"tool": "therefore_documents", "operation": "check_out"},
            "checkin": {"tool": "therefore_documents", "operation": "check_in"},
            "check out": {"tool": "therefore_documents", "operation": "check_out"},
            "check in": {"tool": "therefore_documents", "operation": "check_in"},

            # Query operations
            "search": {"tool": "therefore_query", "operation": "search"},
            "query": {"tool": "therefore_query", "operation": "search"},
            "query documents": {"tool": "therefore_query", "operation": "search"},
            "search documents": {"tool": "therefore_query", "operation": "search"},
            "full text search": {"tool": "therefore_query", "operation": "search_fulltext"},

            # Workflow operations
            "workflow": {"tool": "therefore_workflow", "operation": "query_all"},
            "my tasks": {"tool": "therefore_workflow", "operation": "get_my_tasks"},
            "workflow tasks": {"tool": "therefore_workflow", "operation": "get_my_tasks"},
            "complete task": {"tool": "therefore_workflow", "operation": "complete_task"},
            "workflow instances": {"tool": "therefore_workflow", "operation": "get_all_instances"},
            "valid referenced values": {"tool": "therefore_categories", "operation": "resolve_referenced_field"},
            "referenced field values": {"tool": "therefore_categories", "operation": "resolve_referenced_field"},
            "resolve referenced field": {"tool": "therefore_categories", "operation": "resolve_referenced_field"},
            "fill dependent fields": {"tool": "therefore_categories", "operation": "fill_dependent_fields"},
            "save case index": {"tool": "therefore_workflow", "operation": "save_case_index_data"},

            # User operations
            "users": {"tool": "therefore_users", "operation": "search"},
            "user list": {"tool": "therefore_users", "operation": "search"},
            "search users": {"tool": "therefore_users", "operation": "search"},
            "create user": {"tool": "therefore_users", "operation": "create"},
            "user details": {"tool": "therefore_users", "operation": "get_details"},

            # Keyword operations
            "keywords": {"tool": "therefore_keywords", "operation": "get_by_field"},
            "dictionary": {"tool": "therefore_keywords", "operation": "get_by_dictionary"},
            "add keyword": {"tool": "therefore_keywords", "operation": "add"},
        }

        # Check for direct keyword match. Match the LONGEST matching keyword, not the
        # first one in dict order - otherwise a short keyword (e.g. "search") shadows
        # a more specific one that contains it (e.g. "search users", "full text search").
        suggested_tool = None
        suggested_operation = None
        matched_keyword = None
        for keyword, suggestion in tool_suggestions.items():
            # Word-boundary match, not raw substring - otherwise short keywords like
            # "search" false-positive inside unrelated words like "research".
            if re.search(r"\b" + re.escape(keyword) + r"\b", question):
                if matched_keyword is None or len(keyword) > len(matched_keyword):
                    matched_keyword = keyword
                    suggested_tool = suggestion["tool"]
                    suggested_operation = suggestion["operation"]

        # If no keyword match, try fuzzy matching operation names in the registry
        if not suggested_tool:
            best_match = None
            best_score = 0
            for (tool, op), info in OPERATION_REGISTRY.items():
                # Check if any word in the question matches the operation name
                op_words = op.replace("_", " ").lower()
                desc_words = info["description"].lower()

                # Simple word matching
                question_words = set(question.split())
                op_match_words = set(op_words.split())
                desc_match_words = set(desc_words.split())

                matches = len(question_words & op_match_words) + (len(question_words & desc_match_words) * 0.5)
                if matches > best_score:
                    best_score = matches
                    best_match = (tool, op)

            if best_match and best_score >= 1.0:
                suggested_tool, suggested_operation = best_match

        # Search knowledge base
        from knowledge_tools import search_knowledge
        results = search_knowledge(question, limit=3)

        # Build response with comprehensive parameter info
        response = {"question": args["question"]}

        if suggested_tool and suggested_operation:
            # Get parameter info from registry
            registry_key = (suggested_tool, suggested_operation)
            param_info = OPERATION_REGISTRY.get(registry_key, {})

            # Build call_with dict with required params (tenant is now required for all grouped tools)
            call_with = {
                "operation": suggested_operation,
                "tenant": tenant or "<tenant-key>",  # Use current tenant or placeholder
            }
            for req_param in param_info.get("required", []):
                call_with[req_param] = f"<required - {req_param}>"

            response.update({
                "suggested_tool": suggested_tool,
                "suggested_operation": suggested_operation,
                "description": param_info.get("description", ""),
                "call_with": call_with,
                "all_parameters": {
                    "required": ["tenant"] + param_info.get("required", []),  # tenant is always required
                    "optional": param_info.get("optional", {}),
                },
                "answer": (
                    f"Call {suggested_tool} with:\n"
                    f"  operation: {suggested_operation}\n"
                    f"  tenant: {tenant or '<tenant-key>'}\n"
                    f"  description: {param_info.get('description', '')}\n\n"
                    f"Required params: tenant, {', '.join(param_info.get('required', [])) or 'none'}\n"
                    f"Optional params: {len(param_info.get('optional', {}))} available"
                )
            })

        # Add knowledge base results if found
        if results:
            top_result = results[0]
            response["documentation"] = self._format_knowledge_result(top_result)
            response["result_type"] = top_result["type"]

            if not suggested_tool:
                response["answer"] = self._format_knowledge_result(top_result)

        # Fallback if nothing found
        if not suggested_tool and not results:
            response.update({
                "answer": (
                    "No direct match found. Common starting points:\n"
                    "• Get categories: therefore_categories → get_tree\n"
                    "• Get customer ID: therefore_system → get_customer_id\n"
                    "• Search documents: therefore_query → search\n"
                    "• Get my tasks: therefore_workflow → get_my_tasks"
                ),
                "suggested_tool": "therefore_categories",
                "suggested_operation": "get_tree",
                "call_with": {
                    "operation": "get_tree",
                    "tenant": tenant or "<tenant-key>",
                },
                "all_parameters": {"required": ["tenant"], "optional": {"payload": "object - optional request payload"}},
            })

        return response

    def _format_knowledge_result(self, result: Dict[str, Any]) -> str:
        """Format a knowledge search result into a clear answer."""
        result_type = result.get("type")

        if result_type == "workflow":
            workflow = result.get("data", {})
            steps = workflow.get("steps", [])
            steps_summary = "\n".join(
                [
                    f"{step['step']}. {step['operation']}"
                    for step in steps[:4]  # First 4 steps
                ]
            )
            return f"{workflow.get('name')}\n\n{steps_summary}\n\nUse get_therefore_workflow for full details."

        elif result_type == "pattern":
            pattern = result.get("data", {})
            return f"{pattern.get('description')}\n\nPattern: {pattern.get('pattern')}\n\nUse get_therefore_common_pattern for code examples."

        elif result_type == "quirk":
            quirk = result.get("data", {})
            return f"Issue: {quirk.get('issue')}\nWorkaround: {quirk.get('workaround')}"

        else:
            return result.get("description", "See search results for details")

    def _search_therefore_knowledge(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Search the Therefore API knowledge base."""
        try:
            from knowledge_tools import search_knowledge

            query = args["query"]
            limit = args.get("limit", 5)
            results = search_knowledge(query, limit)

            return {
                "query": query,
                "results_count": len(results),
                "results": results,
                "note": (
                    "Found relevant Therefore API documentation. Each result includes "
                    "type (workflow/pattern/quirk), description, and full data."
                ),
            }
        except Exception as e:
            return {
                "error": f"Knowledge search failed: {str(e)}",
                "query": args.get("query"),
                "note": "Ensure knowledge_tools.py and knowledge-base.json are present in the project.",
            }

    def _get_therefore_workflow(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed workflow guide."""
        try:
            from knowledge_tools import get_workflow_guide

            workflow_name = args["workflow_name"]
            workflow = get_workflow_guide(workflow_name)

            if "error" in workflow:
                return workflow

            return {
                "workflow_name": workflow_name,
                "name": workflow.get("name"),
                "description": workflow.get("description"),
                "use_cases": workflow.get("use_cases", []),
                "steps": workflow.get("steps", []),
                "code_examples": workflow.get("code_examples", {}),
                "common_errors": workflow.get("common_errors", []),
                "note": (
                    f"Complete {len(workflow.get('steps', []))}-step workflow guide. "
                    "Each step includes operation, endpoint, request template, and response fields."
                ),
            }
        except Exception as e:
            return {
                "error": f"Workflow retrieval failed: {str(e)}",
                "workflow_name": args.get("workflow_name"),
            }

    def _get_therefore_field_type_info(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get field type information."""
        try:
            from knowledge_tools import get_field_type_info

            field_type = args["field_type"]
            info = get_field_type_info(field_type)

            if "error" in info:
                return info

            return {
                "field_type": field_type,
                "name": info.get("name"),
                "index_data_type": info.get("index_data_type"),
                "structure": info.get("structure", {}),
                "example": info.get("example", {}),
                "validation": info.get("validation", {}),
                "note": (
                    f"Field type details for {info.get('name')}. Use the provided structure "
                    "when creating or updating documents with this field type."
                ),
            }
        except Exception as e:
            return {
                "error": f"Field type info retrieval failed: {str(e)}",
                "field_type": args.get("field_type"),
            }

    def _get_therefore_common_pattern(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get common coding pattern."""
        try:
            from knowledge_tools import get_common_pattern

            pattern_name = args["pattern_name"]
            pattern = get_common_pattern(pattern_name)

            if "error" in pattern:
                return pattern

            return {
                "pattern_name": pattern_name,
                "description": pattern.get("description"),
                "pattern": pattern.get("pattern"),
                "example_python": pattern.get("example_python"),
                "example_javascript": pattern.get("example_javascript"),
                "note": (
                    f"Common pattern guide for {pattern_name}. Includes examples in "
                    "multiple programming languages."
                ),
            }
        except Exception as e:
            return {
                "error": f"Pattern retrieval failed: {str(e)}",
                "pattern_name": args.get("pattern_name"),
            }

    def _get_therefore_api_quirks(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Get API quirks and workarounds."""
        try:
            from knowledge_tools import get_api_quirks

            search = args.get("search")
            quirks = get_api_quirks(search)

            return {
                "search": search,
                "quirks_count": len(quirks),
                "quirks": quirks,
                "note": (
                    f"Found {len(quirks)} API quirk(s). Each includes issue description, "
                    "explanation, affected operations, and workaround."
                ),
            }
        except Exception as e:
            return {
                "error": f"Quirks retrieval failed: {str(e)}",
                "search": args.get("search"),
            }

    def _list_therefore_knowledge(self) -> Dict[str, Any]:
        """List all available knowledge resources."""
        try:
            from knowledge_tools import list_available_knowledge

            knowledge = list_available_knowledge()

            return {
                "available_knowledge": knowledge,
                "note": (
                    "Available Therefore API knowledge resources. Use the specific tools "
                    "(get_therefore_workflow, etc.) to retrieve detailed information."
                ),
            }
        except Exception as e:
            return {
                "error": f"Knowledge listing failed: {str(e)}",
                "note": "Ensure knowledge_tools.py and knowledge-base.json are present in the project.",
            }

    def _get_therefore_api_help(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        """Fetch live Therefore API help documentation."""
        import urllib.request
        import urllib.parse
        from html.parser import HTMLParser

        operation = args.get("operation")
        format_type = args.get("format", "text")

        # Build help URL
        base_url = client.base_url
        if operation:
            # Specific operation help
            help_url = f"{base_url}/help/operations/{urllib.parse.quote(operation)}"
        else:
            # Main help index
            help_url = f"{base_url}/help"

        try:
            # Fetch help content
            req = urllib.request.Request(help_url, headers=client._headers())
            with urllib.request.urlopen(
                req, context=client.ctx, timeout=30
            ) as response:
                content = response.read().decode("utf-8")

            if format_type == "html":
                return {
                    "url": help_url,
                    "operation": operation,
                    "content": content,
                    "format": "html",
                    "note": "Raw HTML content from Therefore API help endpoint",
                }

            elif format_type == "text":
                # Parse HTML to extract text
                class TextExtractor(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.text = []
                        self.in_script = False
                        self.in_style = False

                    def handle_starttag(self, tag, attrs):
                        if tag == "script":
                            self.in_script = True
                        elif tag == "style":
                            self.in_style = True

                    def handle_endtag(self, tag):
                        if tag == "script":
                            self.in_script = False
                        elif tag == "style":
                            self.in_style = False

                    def handle_data(self, data):
                        if not self.in_script and not self.in_style:
                            text = data.strip()
                            if text:
                                self.text.append(text)

                parser = TextExtractor()
                parser.feed(content)
                text_content = "\n".join(parser.text)

                return {
                    "url": help_url,
                    "operation": operation,
                    "content": text_content,
                    "format": "text",
                    "note": (
                        "Parsed text from Therefore API help. For structured data, "
                        'see docs/export/tenant_operations.json or use format="html".'
                    ),
                }

            else:  # json format
                # Try to extract JSON examples from HTML
                import re

                json_blocks = re.findall(r"<pre[^>]*>(.*?)</pre>", content, re.DOTALL)

                return {
                    "url": help_url,
                    "operation": operation,
                    "json_blocks": json_blocks[:5] if json_blocks else [],
                    "format": "json",
                    "note": (
                        f"Found {len(json_blocks)} code blocks. For complete structured API docs, "
                        "see docs/export/tenant_operations.json"
                    ),
                }

        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {
                    "error": f"Help not found for operation: {operation}"
                    if operation
                    else "Help endpoint not found",
                    "url": help_url,
                    "status_code": 404,
                    "note": (
                        "Operation may not exist or help may not be available. "
                        "Try list_therefore_knowledge to see documented operations."
                    ),
                }
            else:
                return {
                    "error": f"HTTP error {e.code}: {e.reason}",
                    "url": help_url,
                    "status_code": e.code,
                }
        except Exception as e:
            return {
                "error": f"Failed to fetch help: {str(e)}",
                "url": help_url,
                "note": "Check that the Therefore server is accessible and the operation name is correct.",
            }

    def _resolve_tenant(self, args: Dict[str, Any]) -> str:
        tenant_raw = (
            args.get("tenant") or args.get("tenant_name") or args.get("tenantName")
        )
        
        # 1. Determine the scope of tenants this client is allowed to see
        allowed_keys = list(self.clients.keys())
        if self._current_client_key and self.client_access:
            allowed_keys = self.client_access.get(self._current_client_key, [])
        
        if not allowed_keys:
             raise ValueError("This client has no allowed tenants configured.")

        key: Optional[str] = None
        
        # 2. If the user provided a tenant, validate it against their allowed list
        if tenant_raw:
            key = normalize_tenant_key(str(tenant_raw))
            if key not in allowed_keys:
                available = ", ".join(self.tenant_labels.get(k, k) for k in allowed_keys)
                raise ValueError(f"Access to tenant '{tenant_raw}' is not allowed. Your available tenants: {available}")
        
        # 3. If no tenant was provided, try to infer it
        else:
            # First, try to infer from other arguments (e.g. document ID hints)
            inferred = self._infer_tenant_from_args(args)
            if inferred and inferred in allowed_keys:
                key = inferred
            
            # Second, if the client ONLY has access to one tenant, use it as the "Smart Default"
            elif len(allowed_keys) == 1:
                key = allowed_keys[0]
            
            # Third, fallback to the last used tenant (if it's still in their allowed list)
            elif self._last_tenant and self._last_tenant in allowed_keys:
                key = self._last_tenant

        # 4. If we still don't have a key, we must ask the user to be explicit
        if not key:
             available = ", ".join(self.tenant_labels.get(k, k) for k in allowed_keys)
             raise ValueError(f"Multiple tenants available. Please specify one: {available}")

        if key not in self.clients:
            # This should theoretically not happen if the allowed_keys are valid
            raise ValueError(f"Tenant configuration for '{key}' is missing on this server.")
        
        self._last_tenant = key
        return key

    def _infer_tenant_from_args(self, args: Dict[str, Any]) -> Optional[str]:
        if not args or not self.clients or len(self.clients) == 1:
            return None

        texts: List[str] = []

        hint = args.get("tenant_hint")
        if isinstance(hint, str) and hint.strip():
            texts.append(hint.strip())

        def add_text(value: Any) -> None:
            if isinstance(value, str):
                val = value.strip()
                if not val:
                    return
                # Skip likely base64 blobs / huge payloads.
                if len(val) > 2000:
                    return
                if len(val) > 64 and re.fullmatch(r"[A-Za-z0-9+/=]+", val):
                    return
                texts.append(val)
                return
            if isinstance(value, dict):
                for v in value.values():
                    add_text(v)
                return
            if isinstance(value, (list, tuple)):
                for v in value:
                    add_text(v)
                return

        add_text(args)

        if not texts:
            return None

        def tokens_for(name: str) -> Tuple[str, List[str]]:
            norm_key = normalize_tenant_key(name)
            norm_tokens = self._normalize_text(name).split()
            return norm_key, norm_tokens

        candidates: Dict[str, int] = {}
        tenant_data: Dict[str, Tuple[str, List[str], str]] = {}
        for key, label in self.tenant_labels.items():
            label_key, label_tokens = tokens_for(label)
            tenant_key = normalize_tenant_key(key)
            tenant_data[key] = (tenant_key, label_tokens, label_key)

        for text in texts:
            text_key = normalize_tenant_key(text)
            text_tokens = set(self._normalize_text(text).split())
            for key, (tenant_key, label_tokens, label_key) in tenant_data.items():
                score = 0
                if tenant_key and tenant_key in text_key:
                    score += 2
                if label_key and label_key in text_key:
                    score += 2
                for tok in label_tokens:
                    if tok and tok in text_tokens:
                        score += 1
                if score:
                    candidates[key] = candidates.get(key, 0) + score

        if not candidates:
            return None

        best_key = None
        best_score = None
        for key, score in candidates.items():
            if best_score is None or score > best_score:
                best_key = key
                best_score = score
            elif score == best_score:
                best_key = None

        return best_key

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.lower()
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return " ".join(text.split())

    def _score(self, query: str, candidate: str) -> float:
        q = self._normalize_text(query)
        c = self._normalize_text(candidate)
        if not q or not c:
            return 0.0
        if q == c:
            return 1.0
        if q in c or c in q:
            return 0.9
        q_tokens = set(q.split())
        c_tokens = set(c.split())
        union = q_tokens | c_tokens
        inter = q_tokens & c_tokens
        jaccard = len(inter) / len(union) if union else 0.0
        seq = difflib.SequenceMatcher(None, q, c).ratio()
        return max(jaccard, seq * 0.85)

    @staticmethod
    def _coerce_int_list(value: Any) -> Optional[List[int]]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return [int(value)]
        if isinstance(value, (list, tuple, set)):
            out: List[int] = []
            for v in value:
                try:
                    out.append(int(v))
                except (TypeError, ValueError):
                    continue
            return out or None
        if isinstance(value, str):
            parts = [p for p in re.split(r"[\\s,;]+", value.strip()) if p]
            out = []
            for p in parts:
                try:
                    out.append(int(p))
                except ValueError:
                    continue
            return out or None
        return None

    @staticmethod
    def _coerce_str_list(value: Any) -> Optional[List[str]]:
        if value is None:
            return None
        if isinstance(value, str):
            parts = [p.strip() for p in re.split(r"[;,]+", value) if p.strip()]
            return parts or None
        if isinstance(value, (list, tuple, set)):
            out = []
            for v in value:
                if v is None:
                    continue
                text = str(v).strip()
                if text:
                    out.append(text)
            return out or None
        return None

    def _extract_category_list(self, query: Dict[str, Any]) -> Optional[List[int]]:
        if not isinstance(query, dict):
            return None
        for key in (
            "CategoryNo",
            "CategoryNos",
            "CategoryIDs",
            "CategoryIds",
            "Categories",
            "CategoryList",
        ):
            if key in query:
                return self._coerce_int_list(query.get(key))
        return None

    def _flatten_tree(
        self, items: List[Dict[str, Any]], parent_path: str = ""
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in items:
            name = item.get("Name") or ""
            path = f"{parent_path}/{name}" if parent_path else name
            out.append(
                {
                    "name": name,
                    "path": path,
                    "item_no": item.get("ItemNo"),
                    "item_type": item.get("ItemType"),
                    "folder_type": item.get("FolderType"),
                    "parent_case_def_no": item.get("ParentCaseDefNo"),
                    "parent_folder_no": item.get("ParentFolderNo"),
                    "guid": item.get("Guid"),
                }
            )
            children = item.get("ChildItems") or []
            if children:
                out.extend(self._flatten_tree(children, path))
        return out

    def _extract_object_items(self, payload: Any) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        seen: set = set()

        def record(obj: Dict[str, Any]) -> None:
            name = obj.get("Name") or obj.get("name") or ""
            if not name:
                return
            item_no = obj.get("ItemNo")
            if item_no is None:
                item_no = obj.get("ID")
            if item_no is None:
                item_no = obj.get("Id")
            if item_no is None:
                item_no = obj.get("Number")
            key = (name, str(item_no))
            if key in seen:
                return
            seen.add(key)
            items.append(
                {
                    "name": name,
                    "item_no": item_no,
                    "id": obj.get("ID") or obj.get("Id"),
                    "item_type": obj.get("ItemType")
                    or obj.get("Type")
                    or obj.get("TypeNo"),
                    "folder_type": obj.get("FolderType"),
                    "parent_case_def_no": obj.get("ParentCaseDefNo"),
                    "parent_folder_no": obj.get("ParentFolderNo"),
                    "guid": obj.get("Guid"),
                    "data": obj.get("Data"),
                }
            )

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if ("Name" in node or "name" in node) and any(
                    k in node for k in ("ItemNo", "ID", "Id", "Number")
                ):
                    record(node)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(payload)
        return items

    def _resolve_category(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        query = args["query"]
        max_results = int(args.get("max_results", 5))
        min_score = float(args.get("min_score", 0.35))
        include_non_categories = bool(args.get("include_non_categories", False))
        confirm_threshold = float(args.get("confirm_threshold", 0.75))

        flat = self._get_cached_categories(tenant, client)

        if not include_non_categories:
            flat = [c for c in flat if c.get("item_type") == 2]

        # if query is numeric, try exact match on item_no
        numeric_match = None
        if str(query).isdigit():
            qno = int(query)
            numeric_match = [c for c in flat if c.get("item_no") == qno]

        candidates = []
        for c in flat:
            name = c.get("name") or ""
            path = c.get("path") or ""
            score = max(self._score(query, name), self._score(query, path))
            if score >= min_score:
                candidates.append({**c, "score": round(score, 4)})

        candidates.sort(key=lambda x: x["score"], reverse=True)
        if numeric_match:
            for c in numeric_match:
                if all(c["item_no"] != m["item_no"] for m in candidates):
                    candidates.insert(0, {**c, "score": 1.0})

        needs_confirmation = True
        if candidates:
            if candidates[0]["score"] >= confirm_threshold and (
                len(candidates) == 1
                or candidates[0]["score"] - candidates[1]["score"] >= 0.15
            ):
                needs_confirmation = False

        return {
            "query": query,
            "count": len(candidates[:max_results]),
            "candidates": candidates[:max_results],
            "needs_confirmation": needs_confirmation,
        }

    def _resolve_keyword_dictionary(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        query = args.get("dictionary_name") or args.get("query") or args.get("name")
        if not query:
            raise ValueError("dictionary_name is required")
        max_results = int(args.get("max_results", 5))
        min_score = float(args.get("min_score", 0.35))
        confirm_threshold = float(args.get("confirm_threshold", 0.75))

        items = self._get_cached_keyword_dictionaries(tenant, client)

        numeric_match: List[Dict[str, Any]] = []
        if str(query).isdigit():
            qno = int(query)
            for item in items:
                try:
                    item_no = item.get("item_no")
                    if item_no is not None and int(item_no) == qno:
                        numeric_match.append(item)
                except Exception:
                    continue

        candidates: List[Dict[str, Any]] = []
        for item in items:
            name = item.get("name") or ""
            score = self._score(str(query), name)
            if score >= min_score:
                candidates.append(
                    {
                        "dictionary_no": item.get("item_no"),
                        "name": name,
                        "id": item.get("id"),
                        "data": item.get("data"),
                        "score": round(score, 4),
                    }
                )

        candidates.sort(key=lambda x: x["score"], reverse=True)
        if numeric_match:
            for item in numeric_match:
                if all(c["dictionary_no"] != item.get("item_no") for c in candidates):
                    candidates.insert(
                        0,
                        {
                            "dictionary_no": item.get("item_no"),
                            "name": item.get("name"),
                            "id": item.get("id"),
                            "data": item.get("data"),
                            "score": 1.0,
                        },
                    )

        needs_confirmation = True
        if candidates:
            if candidates[0]["score"] >= confirm_threshold and (
                len(candidates) == 1
                or candidates[0]["score"] - candidates[1]["score"] >= 0.15
            ):
                needs_confirmation = False

        return {
            "query": query,
            "count": len(candidates[:max_results]),
            "candidates": candidates[:max_results],
            "needs_confirmation": needs_confirmation,
        }

    def _get_keywords_by_dictionary_name(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
    ) -> Dict[str, Any]:
        resolution = self._resolve_keyword_dictionary(args, tenant, client)
        if resolution.get("needs_confirmation") or not resolution.get("candidates"):
            return resolution

        top = resolution["candidates"][0]
        dictionary_no = top.get("dictionary_no")
        if dictionary_no is None:
            raise ValueError("Resolved dictionary does not include a dictionary number")

        resp = client.get_keywords_by_key_dic(
            key_dic_no=int(dictionary_no),
            filter_value=args.get("filter_value"),
            max_values=args.get("max_values"),
            include_deactivated_keywords=args.get("include_deactivated_keywords"),
        )
        return {
            **resolution,
            "needs_confirmation": False,
            "dictionary_no": dictionary_no,
            "dictionary_name": top.get("name"),
            "keywords": resp.get("Keywords") or [],
            "keyword_nos": resp.get("KeywordNos") or [],
            "all_rows_returned": resp.get("AllRowsReturned"),
        }

    def _add_dictionary_keyword(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
    ) -> Dict[str, Any]:
        keyword_name = str(args.get("keyword_name") or "").strip()
        if not keyword_name:
            raise ValueError("keyword_name is required")

        dictionary_no = args.get("dictionary_no")
        dictionary_type_no = args.get("dictionary_type_no")
        dictionary_name = args.get("dictionary_name")

        if dictionary_no is None and dictionary_name:
            resolution = self._resolve_keyword_dictionary(
                {"dictionary_name": dictionary_name}, tenant, client
            )
            if resolution.get("needs_confirmation") or not resolution.get("candidates"):
                return {
                    "keyword_name": keyword_name,
                    "needs_confirmation": True,
                    "resolution": resolution,
                }
            dictionary_no = resolution["candidates"][0].get("dictionary_no")
        if dictionary_no is None and dictionary_type_no is None:
            raise ValueError(
                "dictionary_no, dictionary_name, or dictionary_type_no is required"
            )

        check_existing = bool(args.get("check_existing", True))
        ignore_if_exists = bool(args.get("ignore_if_exists", True))
        include_deactivated = bool(args.get("include_deactivated_keywords", True))

        existing = []
        if check_existing and dictionary_no is not None:
            try:
                existing_resp = client.get_keywords_by_key_dic(
                    key_dic_no=int(dictionary_no),
                    include_deactivated_keywords=include_deactivated,
                    max_values=100000,
                )
                existing = existing_resp.get("Keywords") or []
            except Exception:
                existing = []

        existing_match = None
        if existing:
            target = keyword_name.strip().lower()
            for kw in existing:
                if str(kw).strip().lower() == target:
                    existing_match = kw
                    break

        if existing_match is not None:
            if ignore_if_exists:
                return {
                    "status": "exists",
                    "keyword_name": keyword_name,
                    "matched_keyword": existing_match,
                    "dictionary_no": dictionary_no,
                    "dictionary_type_no": dictionary_type_no,
                }
            raise ValueError(
                f'Keyword "{keyword_name}" already exists in dictionary {dictionary_no}'
            )

        resp = client.add_dictionary_keyword(
            dictionary_no=int(dictionary_no) if dictionary_no is not None else None,
            dictionary_type_no=int(dictionary_type_no)
            if dictionary_type_no is not None
            else None,
            keyword_name=keyword_name,
            is_keyword_deactivated=args.get("is_keyword_deactivated"),
        )
        return {
            "status": "added",
            "keyword_name": keyword_name,
            "dictionary_no": dictionary_no,
            "dictionary_type_no": dictionary_type_no,
            "response": resp,
        }

    def _update_dictionary_keyword(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
    ) -> Dict[str, Any]:
        new_keyword_name = str(args.get("new_keyword_name") or "").strip()
        if not new_keyword_name:
            raise ValueError("new_keyword_name is required")

        dictionary_no = args.get("dictionary_no")
        dictionary_type_no = args.get("dictionary_type_no")
        dictionary_name = args.get("dictionary_name")
        keyword_id = args.get("keyword_id")
        keyword_name = args.get("keyword_name")

        if dictionary_no is None and dictionary_name:
            resolution = self._resolve_keyword_dictionary(
                {"dictionary_name": dictionary_name}, tenant, client
            )
            if resolution.get("needs_confirmation") or not resolution.get("candidates"):
                return {
                    "keyword_name": keyword_name,
                    "new_keyword_name": new_keyword_name,
                    "needs_confirmation": True,
                    "resolution": resolution,
                }
            dictionary_no = resolution["candidates"][0].get("dictionary_no")

        if dictionary_no is None and dictionary_type_no is None:
            raise ValueError(
                "dictionary_no, dictionary_name, or dictionary_type_no is required"
            )

        include_deactivated = bool(args.get("include_deactivated_keywords", True))
        existing = []
        existing_resp = None
        if dictionary_no is not None:
            try:
                existing_resp = client.get_keywords_by_key_dic(
                    key_dic_no=int(dictionary_no),
                    include_deactivated_keywords=include_deactivated,
                    max_values=100000,
                )
                existing = existing_resp.get("Keywords") or []
            except Exception:
                existing = []

        if keyword_id is None:
            if not keyword_name:
                raise ValueError("keyword_id or keyword_name is required")
            target = str(keyword_name).strip().lower()
            if not existing_resp:
                raise ValueError(
                    "Unable to resolve keyword_id without dictionary keywords."
                )
            keyword_nos = existing_resp.get("KeywordNos") or []
            found_id = None
            for idx, kw in enumerate(existing):
                if str(kw).strip().lower() == target:
                    if idx < len(keyword_nos):
                        found_id = keyword_nos[idx]
                    break
            if found_id is None:
                raise ValueError(
                    f'Keyword "{keyword_name}" not found in dictionary {dictionary_no}'
                )
            keyword_id = found_id

        check_existing = bool(args.get("check_existing", True))
        ignore_if_exists = bool(args.get("ignore_if_exists", True))
        if check_existing and existing:
            target = new_keyword_name.strip().lower()
            for kw in existing:
                if str(kw).strip().lower() == target:
                    if ignore_if_exists:
                        return {
                            "status": "exists",
                            "keyword_name": new_keyword_name,
                            "dictionary_no": dictionary_no,
                            "dictionary_type_no": dictionary_type_no,
                        }
                    raise ValueError(
                        f'Keyword "{new_keyword_name}" already exists in dictionary {dictionary_no}'
                    )

        resp = client.update_dictionary_keyword(
            dictionary_no=int(dictionary_no) if dictionary_no is not None else None,
            dictionary_type_no=int(dictionary_type_no)
            if dictionary_type_no is not None
            else None,
            keyword_id=int(keyword_id),
            keyword_name=new_keyword_name,
            is_keyword_deactivated=args.get("is_keyword_deactivated"),
        )
        return {
            "status": "updated",
            "dictionary_no": dictionary_no,
            "dictionary_type_no": dictionary_type_no,
            "keyword_id": keyword_id,
            "keyword_name": new_keyword_name,
            "response": resp,
        }

    def _delete_dictionary_keyword(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
    ) -> Dict[str, Any]:
        dictionary_no = args.get("dictionary_no")
        dictionary_type_no = args.get("dictionary_type_no")
        dictionary_name = args.get("dictionary_name")
        keyword_id = args.get("keyword_id")
        keyword_name = args.get("keyword_name")

        if dictionary_no is None and dictionary_name:
            resolution = self._resolve_keyword_dictionary(
                {"dictionary_name": dictionary_name}, tenant, client
            )
            if resolution.get("needs_confirmation") or not resolution.get("candidates"):
                return {
                    "keyword_name": keyword_name,
                    "needs_confirmation": True,
                    "resolution": resolution,
                }
            dictionary_no = resolution["candidates"][0].get("dictionary_no")

        if dictionary_no is None and dictionary_type_no is None:
            raise ValueError(
                "dictionary_no, dictionary_name, or dictionary_type_no is required"
            )

        include_deactivated = bool(args.get("include_deactivated_keywords", True))
        if keyword_id is None:
            if not keyword_name:
                raise ValueError("keyword_id or keyword_name is required")
            existing_resp = client.get_keywords_by_key_dic(
                key_dic_no=int(dictionary_no),
                include_deactivated_keywords=include_deactivated,
                max_values=100000,
            )
            keywords = existing_resp.get("Keywords") or []
            keyword_nos = existing_resp.get("KeywordNos") or []
            target = str(keyword_name).strip().lower()
            found_id = None
            for idx, kw in enumerate(keywords):
                if str(kw).strip().lower() == target:
                    if idx < len(keyword_nos):
                        found_id = keyword_nos[idx]
                    break
            if found_id is None:
                raise ValueError(
                    f'Keyword "{keyword_name}" not found in dictionary {dictionary_no}'
                )
            keyword_id = found_id

        resp = client.delete_dictionary_keyword(
            dictionary_no=int(dictionary_no) if dictionary_no is not None else None,
            dictionary_type_no=int(dictionary_type_no)
            if dictionary_type_no is not None
            else None,
            keyword_id=int(keyword_id),
        )
        return {
            "status": "deleted",
            "dictionary_no": dictionary_no,
            "dictionary_type_no": dictionary_type_no,
            "keyword_id": keyword_id,
            "response": resp,
        }

    def _deactivate_dictionary_keyword(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
    ) -> Dict[str, Any]:
        dictionary_no = args.get("dictionary_no")
        dictionary_type_no = args.get("dictionary_type_no")
        dictionary_name = args.get("dictionary_name")
        keyword_id = args.get("keyword_id")
        keyword_name = args.get("keyword_name")

        if dictionary_no is None and dictionary_name:
            resolution = self._resolve_keyword_dictionary(
                {"dictionary_name": dictionary_name}, tenant, client
            )
            if resolution.get("needs_confirmation") or not resolution.get("candidates"):
                return {
                    "keyword_name": keyword_name,
                    "needs_confirmation": True,
                    "resolution": resolution,
                }
            dictionary_no = resolution["candidates"][0].get("dictionary_no")

        if dictionary_no is None and dictionary_type_no is None:
            raise ValueError(
                "dictionary_no, dictionary_name, or dictionary_type_no is required"
            )

        include_deactivated = bool(args.get("include_deactivated_keywords", True))
        if keyword_id is None:
            if not keyword_name:
                raise ValueError("keyword_id or keyword_name is required")
            existing_resp = client.get_keywords_by_key_dic(
                key_dic_no=int(dictionary_no),
                include_deactivated_keywords=include_deactivated,
                max_values=100000,
            )
            keywords = existing_resp.get("Keywords") or []
            keyword_nos = existing_resp.get("KeywordNos") or []
            target = str(keyword_name).strip().lower()
            found_id = None
            for idx, kw in enumerate(keywords):
                if str(kw).strip().lower() == target:
                    if idx < len(keyword_nos):
                        found_id = keyword_nos[idx]
                    break
            if found_id is None:
                raise ValueError(
                    f'Keyword "{keyword_name}" not found in dictionary {dictionary_no}'
                )
            keyword_id = found_id

        resp = client.update_dictionary_keyword(
            dictionary_no=int(dictionary_no) if dictionary_no is not None else None,
            dictionary_type_no=int(dictionary_type_no)
            if dictionary_type_no is not None
            else None,
            keyword_id=int(keyword_id),
            is_keyword_deactivated=True,
        )
        return {
            "status": "deactivated",
            "dictionary_no": dictionary_no,
            "dictionary_type_no": dictionary_type_no,
            "keyword_id": keyword_id,
            "response": resp,
        }

    def _list_category_fields(
        self, category_no: int, tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        fields = self._get_cached_fields(tenant, category_no, client)
        simplified = []
        for f in fields:
            simplified.append(
                {
                    "field_no": f.get("FieldNo"),
                    "field_id": f.get("FieldID"),
                    "caption": f.get("Caption"),
                    "index_name": f.get("IndexDataFieldName"),
                    "field_type": f.get("FieldType"),
                    "type_no": f.get("TypeNo"),
                    "mandatory": f.get("Mandatory"),
                    "visible": f.get("Visible"),
                    "regular_expr": f.get("RegularExpr"),
                    "regex_sample": f.get("RegExSample"),
                    "is_auto_append": f.get("IsAutoAppendField"),
                    "counter_mode": f.get("CounterMode"),
                }
            )
        return {
            "category_no": category_no,
            "field_count": len(simplified),
            "fields": simplified,
        }

    def _resolve_field(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        category_no = int(args["category_no"])
        query = args["query"]
        max_results = int(args.get("max_results", 5))
        min_score = float(args.get("min_score", 0.35))
        field_type_hint = args.get("field_type_hint")
        confirm_threshold = float(args.get("confirm_threshold", 0.75))

        fields = self._get_cached_fields(tenant, category_no, client)

        candidates = []
        for f in fields:
            caption = f.get("Caption") or ""
            field_id = f.get("FieldID") or ""
            index_name = f.get("IndexDataFieldName") or ""

            score = max(
                self._score(query, caption),
                self._score(query, field_id),
                self._score(query, index_name),
            )

            if field_type_hint is not None and f.get("FieldType") == field_type_hint:
                score = min(1.0, score + 0.05)

            if score >= min_score:
                candidates.append(
                    {
                        "field_no": f.get("FieldNo"),
                        "field_id": field_id,
                        "caption": caption,
                        "index_name": index_name,
                        "field_type": f.get("FieldType"),
                        "type_no": f.get("TypeNo"),
                        "mandatory": f.get("Mandatory"),
                        "visible": f.get("Visible"),
                        "score": round(score, 4),
                    }
                )

        candidates.sort(key=lambda x: x["score"], reverse=True)
        needs_confirmation = True
        if candidates:
            if candidates[0]["score"] >= confirm_threshold and (
                len(candidates) == 1
                or candidates[0]["score"] - candidates[1]["score"] >= 0.15
            ):
                needs_confirmation = False

        return {
            "category_no": category_no,
            "query": query,
            "count": len(candidates[:max_results]),
            "candidates": candidates[:max_results],
            "needs_confirmation": needs_confirmation,
        }

    def _discover_referenced_table_by_name(
        self, name: str, client: ThereforeClient
    ) -> Optional[int]:
        """
        Find the DataTypeNo (TypeNo) for a referenced table by name.
        Strategy:
          1. GetObjects(obj_type=5) — works on some tenants.
          2. Scan category fields for IsForeignDatatype TypeNos.
          3. Parallel range probe GetReferencedTableInfo(n) for n in 1..256.
        Returns the DataTypeNo or None if not found.
        """
        import concurrent.futures as _cf

        name_lower = name.lower()

        # --- Strategy 1: GetObjects ---
        try:
            resp = client.get_objects(flags=0, obj_type=5)
            for item in resp.get("Items") or []:
                if (item.get("Name") or "").lower() == name_lower:
                    return int(item["ID"])
        except Exception:
            pass

        # --- Strategy 2: collect TypeNos from category fields ---
        candidate_type_nos: set = set()
        try:
            cats_tree = client.get_categories_tree()
            def _flatten(items):
                nos = []
                for it in items:
                    if it.get("ItemType") == 2:
                        nos.append(it["ItemNo"])
                    nos.extend(_flatten(it.get("ChildItems") or []))
                return nos
            for cat_no in _flatten(cats_tree.get("TreeItems") or []):
                try:
                    info = client.get_category_info(cat_no)
                    for field in info.get("CategoryFields") or []:
                        if field.get("IsForeignDatatype"):
                            tn = field.get("TypeNo")
                            if tn is not None:
                                candidate_type_nos.add(int(tn))
                except Exception:
                    pass
        except Exception:
            pass

        for tn in sorted(candidate_type_nos):
            try:
                info = client.get_referenced_table_info(tn)
                if (info.get("Name") or "").lower() == name_lower:
                    return tn
            except Exception:
                pass

        # --- Strategy 3: parallel range probe 1..256 ---
        probe_range = [n for n in range(1, 257) if n not in candidate_type_nos]

        def _probe(n):
            try:
                info = client.get_referenced_table_info(n)
                return n, (info.get("Name") or "")
            except Exception:
                return n, None

        with _cf.ThreadPoolExecutor(max_workers=20) as pool:
            for tn, tname in pool.map(_probe, probe_range):
                if tname and tname.lower() == name_lower:
                    return tn

        return None

    def _query_referenced_table(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        import re as _re

        data_type_no = args.get("data_type_no")
        name = args.get("name")
        conditions = args.get("conditions") or []
        max_rows = int(args.get("max_rows", 5000))

        if data_type_no is None:
            if not name:
                raise ValueError("Either data_type_no or name must be provided")
            data_type_no = self._discover_referenced_table_by_name(name, client)
            if data_type_no is None:
                raise ValueError(f"Referenced table '{name}' not found")

        data_type_no = int(data_type_no)
        table_info = client.get_referenced_table_info(data_type_no)

        # The actual category number is embedded in TableName (e.g. "TheCat43" → 43)
        table_name_str = table_info.get("TableName") or ""
        m = _re.search(r"\d+$", table_name_str)
        category_no = int(m.group()) if m else data_type_no

        query = {"CategoryNo": category_no, "Conditions": conditions}
        result = client.execute_async_single_query_all(query, max_rows=max_rows)
        rows = (result.get("QueryResult") or {}).get("ResultRows") or []

        return {
            "data_type_no": data_type_no,
            "category_no": category_no,
            "name": table_info.get("Name"),
            "columns": table_info.get("Columns"),
            "row_count": len(rows),
            "rows": rows,
        }

    def _execute_dependent_fields_query(
        self, args: Dict[str, Any], client: ThereforeClient
    ) -> Dict[str, Any]:
        case_definition_no = args.get("case_definition_no")
        category_no = args.get("category_no")
        return client.execute_dependent_fields_query(
            field_no=int(args["field_no"]),
            index_data_items=args.get("index_data_items") or [],
            case_definition_no=int(case_definition_no)
            if case_definition_no is not None
            else None,
            category_no=int(category_no) if category_no is not None else None,
            max_rows=int(args.get("max_rows", 500)),
            save_mode=bool(args.get("save_mode", False)),
        )

    def _fill_dependent_fields(
        self, args: Dict[str, Any], client: ThereforeClient
    ) -> Dict[str, Any]:
        return client.fill_dependent_fields(
            index_data_items=args["index_data_items"],
            primary_field_no=int(args["primary_field_no"]),
            doc_no=int(args["doc_no"]) if args.get("doc_no") is not None else None,
            case_definition_no=int(args["case_definition_no"])
            if args.get("case_definition_no") is not None
            else None,
            category_no=int(args["category_no"])
            if args.get("category_no") is not None
            else None,
            exclude_redundant=bool(args.get("exclude_redundant", False)),
            include_access_mask=bool(args.get("include_access_mask", False)),
            do_calculate_fields=bool(args.get("do_calculate_fields", True)),
        )

    def _resolve_referenced_field(
        self, args: Dict[str, Any], client: ThereforeClient
    ) -> Dict[str, Any]:
        """Discover valid referenced rows and optionally fill a selected row."""
        doc_no = args.get("doc_no")
        category_no = args.get("category_no")
        case_definition_no = args.get("case_definition_no")
        if sum(v is not None for v in (doc_no, category_no, case_definition_no)) != 1:
            raise ValueError(
                "Specify exactly one of doc_no, category_no, or case_definition_no"
            )

        field_no = int(args["field_no"])
        index_data_items = args.get("index_data_items")
        definition_fields: List[Dict[str, Any]] = []
        query_category_no: Optional[int] = None
        query_case_definition_no: Optional[int] = None

        if doc_no is not None:
            doc_no = int(doc_no)
            current = client.get_document_index_data(doc_no)
            current_index = current.get("IndexData") or {}
            if index_data_items is None:
                index_data_items = current_index.get("IndexDataItems") or []
            query_category_no = current_index.get("CategoryNo") or current.get("CategoryNo")
            if query_category_no is None:
                raise ValueError("GetDocumentIndexData did not return CategoryNo")
            query_category_no = int(query_category_no)
            info = client.get_category_info(query_category_no)
            definition_fields = info.get("CategoryFields") or []
        elif category_no is not None:
            query_category_no = int(category_no)
            info = client.get_category_info(query_category_no)
            definition_fields = info.get("CategoryFields") or []
            if index_data_items is None:
                preprocessed = client.preprocess_index_data(query_category_no, [])
                index_data_items = (
                    (preprocessed.get("IndexData") or {}).get("IndexDataItems") or []
                )
            else:
                preprocessed = client.preprocess_index_data(
                    query_category_no, index_data_items
                )
                index_data_items = (
                    (preprocessed.get("IndexData") or {}).get("IndexDataItems")
                    or index_data_items
                )
        else:
            query_case_definition_no = int(case_definition_no)
            info = client.get_case_definition(query_case_definition_no)
            definition_fields = (info.get("CaseDefinition") or {}).get("Fields") or []
            if index_data_items is None:
                index_data_items = []

        field = next(
            (f for f in definition_fields if int(f.get("FieldNo", -1)) == field_no),
            None,
        )
        if field is None:
            raise ValueError(f"FieldNo {field_no} was not found in the selected context")
        if not field.get("IsForeignDatatype"):
            raise ValueError(f"FieldNo {field_no} is not a referenced-table field")

        data_type_no = int(field["TypeNo"])
        table_info = client.get_referenced_table_info(data_type_no)
        index_column = table_info.get("IndexColumn")
        index_column_info = next(
            (
                col
                for col in table_info.get("Columns") or []
                if col.get("ColumnName") == index_column
            ),
            None,
        )
        query_result = client.execute_dependent_fields_query(
            field_no=field_no,
            index_data_items=index_data_items or [],
            case_definition_no=query_case_definition_no,
            category_no=query_category_no,
            max_rows=int(args.get("max_rows", 500)),
            save_mode=bool(args.get("save_mode", False)),
        )

        result: Dict[str, Any] = {
            "context": {
                "DocNo": doc_no,
                "CategoryNo": query_category_no,
                "CaseDefinitionNo": query_case_definition_no,
            },
            "field": field,
            "referenced_table": table_info,
            "index_column": index_column_info,
            "index_data_items": index_data_items,
            "valid_values": query_result,
        }
        selected_row_index = args.get("selected_row_index")
        if selected_row_index is None:
            return result

        query = query_result.get("QueryResult") or {}
        rows = query.get("ResultRows") or []
        selected_row_index = int(selected_row_index)
        if selected_row_index < 0 or selected_row_index >= len(rows):
            raise ValueError(
                f"selected_row_index {selected_row_index} is outside 0..{len(rows) - 1}"
            )
        columns = query.get("Columns") or []
        value_index = next(
            (i for i, col in enumerate(columns) if int(col.get("FieldNo", -1)) == field_no),
            0,
        )
        values = rows[selected_row_index].get("FieldValues") or []
        if value_index >= len(values):
            raise ValueError("The selected row does not contain the referenced field value")
        selected_value = values[value_index]

        kind = args.get("index_data_kind")
        if not kind:
            type_code = (index_column_info or {}).get("Type")
            kind = {1: "StringIndexData", 2: "IntIndexData", 3: "DateIndexData"}.get(
                type_code
            )
        allowed_kinds = {
            "StringIndexData",
            "IntIndexData",
            "MoneyIndexData",
            "DateIndexData",
            "DateTimeIndexData",
            "LogicalIndexData",
        }
        if kind not in allowed_kinds:
            raise ValueError(
                "Could not infer the referenced ID type; pass index_data_kind explicitly"
            )
        if kind == "IntIndexData" and selected_value is not None:
            selected_value = int(selected_value)

        selected_item = {kind: {"FieldNo": field_no, "DataValue": selected_value}}
        type_keys = allowed_kinds | {
            "SingleKeywordData",
            "MultipleKeywordData",
            "TableIndexData",
        }
        fill_items: List[Dict[str, Any]] = []
        replaced = False
        for item in index_data_items or []:
            typed = next(
                ((key, item.get(key)) for key in type_keys if isinstance(item.get(key), dict)),
                None,
            )
            if typed is None:
                continue
            key, data = typed
            if int(data.get("FieldNo", -1)) == field_no:
                fill_items.append(selected_item)
                replaced = True
            else:
                fill_items.append({key: data})
        if not replaced:
            fill_items.append(selected_item)

        filled = client.fill_dependent_fields(
            index_data_items=fill_items,
            primary_field_no=field_no,
            doc_no=doc_no,
            category_no=query_category_no if doc_no is None else None,
            case_definition_no=query_case_definition_no,
            exclude_redundant=bool(args.get("exclude_redundant", False)),
            include_access_mask=bool(args.get("include_access_mask", False)),
            do_calculate_fields=bool(args.get("do_calculate_fields", True)),
        )
        result["selected"] = {
            "row_index": selected_row_index,
            "row": rows[selected_row_index],
            "index_data_item": selected_item,
        }
        result["filled"] = filled
        return result

    def _prepare_index_update(
        self,
        doc_no: int,
        updates: List[Dict[str, Any]],
        index_data_items_override: Optional[List[Dict[str, Any]]] = None,
        tenant: str = "",
        client: Optional[ThereforeClient] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str], Optional[int]]:
        if client is None:
            raise ValueError("client is required for index update preparation")
        current = client.get_document_index_data(doc_no)
        idx = current.get("IndexData") or {}
        last_change_time = idx.get("LastChangeTime")
        last_change_time_iso = idx.get("LastChangeTimeISO8601")
        category_no = idx.get("CategoryNo")

        if index_data_items_override is not None:
            return (
                index_data_items_override,
                last_change_time,
                last_change_time_iso,
                category_no,
            )

        if not updates:
            return [], last_change_time, last_change_time_iso, category_no

        type_keys = [
            "StringIndexData",
            "IntIndexData",
            "MoneyIndexData",
            "DateIndexData",
            "DateTimeIndexData",
            "LogicalIndexData",
            "SingleKeywordData",
            "MultipleKeywordData",
            "TableIndexData",
        ]

        existing_map: Dict[int, Tuple[str, Optional[str]]] = {}
        for item in idx.get("IndexDataItems") or []:
            for key in type_keys:
                data = item.get(key)
                if data and data.get("FieldNo") is not None:
                    try:
                        fno = int(data.get("FieldNo"))
                    except Exception:
                        continue
                    existing_map[fno] = (key, data.get("FieldName"))
                    break

        category_fields: Optional[List[Dict[str, Any]]] = None

        def find_field_meta(field_no: int) -> Optional[Dict[str, Any]]:
            nonlocal category_fields
            if category_no and category_fields is None:
                category_fields = self._get_cached_fields(
                    tenant, int(category_no), client
                )
            if not category_fields:
                return None
            for f in category_fields:
                try:
                    if int(f.get("FieldNo")) == field_no:
                        return f
                except Exception:
                    continue
            return None

        def resolve_field_no(query: str) -> Tuple[int, Dict[str, Any]]:
            nonlocal category_fields
            if category_no and category_fields is None:
                category_fields = self._get_cached_fields(int(category_no))
            if not category_fields:
                raise ValueError("Category fields not available to resolve field name")

            candidates = []
            for f in category_fields:
                caption = f.get("Caption") or ""
                field_id = f.get("FieldID") or ""
                index_name = f.get("IndexDataFieldName") or ""
                score = max(
                    self._score(query, caption),
                    self._score(query, field_id),
                    self._score(query, index_name),
                )
                if score >= 0.35:
                    candidates.append((score, f))

            if not candidates:
                raise ValueError(f"No field matches query: {query}")

            candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_field = candidates[0]
            needs_confirmation = True
            if best_score >= 0.75 and (
                len(candidates) == 1 or best_score - candidates[1][0] >= 0.15
            ):
                needs_confirmation = False
            if needs_confirmation:
                top = [
                    {
                        "field_no": c[1].get("FieldNo"),
                        "caption": c[1].get("Caption"),
                        "field_id": c[1].get("FieldID"),
                        "index_name": c[1].get("IndexDataFieldName"),
                        "score": round(c[0], 4),
                    }
                    for c in candidates[:5]
                ]
                raise ValueError(f'Ambiguous field name "{query}". Candidates: {top}')

            return int(best_field.get("FieldNo")), best_field

        def infer_type_key(field_type: Optional[int]) -> Optional[str]:
            mapping = {
                1: "StringIndexData",
                2: "IntIndexData",
                3: "DateIndexData",
                5: "MoneyIndexData",
                6: "LogicalIndexData",
                9: "StringIndexData",
            }
            return mapping.get(field_type)

        index_data_items: List[Dict[str, Any]] = []
        for upd in updates:
            field_no = upd.get("field_no")
            if field_no is None:
                query = (
                    upd.get("field_name")
                    or upd.get("field_id")
                    or upd.get("caption")
                    or upd.get("index_name")
                    or upd.get("query")
                )
                if not query:
                    raise ValueError(
                        "Update item must include field_no or field_name/query"
                    )
                field_no, meta = resolve_field_no(str(query))
            else:
                field_no = int(field_no)
            value = upd.get("value")

            if field_no in existing_map:
                type_key, field_name = existing_map[field_no]
            else:
                meta = find_field_meta(field_no)
                if not meta:
                    raise ValueError(
                        f"Field {field_no} not found for document {doc_no}"
                    )
                field_type = meta.get("FieldType")
                if field_type == 4:
                    raise ValueError(
                        f"Field {field_no} is label-only and cannot hold a value"
                    )
                type_key = infer_type_key(field_type)
                if not type_key:
                    raise ValueError(
                        f"Field {field_no} has unsupported FieldType {field_type}; provide index_data_items explicitly"
                    )
                field_name = (
                    meta.get("IndexDataFieldName")
                    or meta.get("FieldID")
                    or meta.get("Caption")
                )

            if type_key == "MultipleKeywordData":
                if value is None:
                    data_value = []
                elif isinstance(value, list):
                    data_value = value
                else:
                    data_value = [value]
                data = {
                    "FieldNo": field_no,
                    "DataValue": data_value,
                    "FieldName": field_name,
                }
            else:
                data = {
                    "FieldNo": field_no,
                    "DataValue": value,
                    "FieldName": field_name,
                }

            index_data_items.append({type_key: data})

        return index_data_items, last_change_time, last_change_time_iso, category_no

    def _update_document_index_data(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        doc_no = int(args["doc_no"])
        check_in_comments = args.get("check_in_comments", "")
        do_fill_dependent_fields = bool(args.get("do_fill_dependent_fields", True))

        index_data_items, last_change_time, last_change_time_iso, _ = (
            self._prepare_index_update(
                doc_no=doc_no,
                updates=args.get("updates") or [],
                index_data_items_override=args.get("index_data_items"),
                tenant=tenant,
                client=client,
            )
        )

        update_resp = client.save_document_index_data(
            doc_no=doc_no,
            index_data_items=index_data_items,
            check_in_comments=check_in_comments,
            do_fill_dependent_fields=do_fill_dependent_fields,
            last_change_time=last_change_time,
            last_change_time_iso=last_change_time_iso,
        )
        updated = client.get_document_index_data(doc_no)
        return {
            "update_response": update_resp,
            "updated_index_data": updated.get("IndexData"),
        }

    def _update_document(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        doc_no = int(args["doc_no"])
        check_in_comments = args.get("check_in_comments", "")
        do_fill_dependent_fields = bool(args.get("do_fill_dependent_fields", True))

        index_data_items, last_change_time, last_change_time_iso, _ = (
            self._prepare_index_update(
                doc_no=doc_no,
                updates=args.get("updates") or [],
                index_data_items_override=args.get("index_data_items"),
                tenant=tenant,
                client=client,
            )
        )

        streams_to_update = []
        for s in args.get("streams") or []:
            file_name = s.get("file_name")
            file_data_base64 = s.get("file_data_base64")
            file_data_text = s.get("file_data_text")
            if file_data_text and not file_data_base64:
                file_data_base64 = base64.b64encode(
                    file_data_text.encode("utf-8")
                ).decode("ascii")
            if not file_name:
                raise ValueError("stream missing file_name")
            if not file_data_base64:
                raise ValueError("stream missing file_data_base64 or file_data_text")
            entry = {
                "FileName": file_name,
                "FileDataBase64JSON": file_data_base64,
                "NewStreamInsertMode": self._normalize_stream_insert_mode(
                    s.get("new_stream_insert_mode", 0)
                ),
            }
            if s.get("stream_no") is not None:
                entry["StreamNo"] = int(s.get("stream_no"))
            streams_to_update.append(entry)

        streams_to_rename = []
        for r in args.get("streams_to_rename") or []:
            streams_to_rename.append(
                {
                    "StreamNo": int(r["stream_no"]),
                    "FileName": r["file_name"],
                }
            )

        update_resp = client.update_document(
            doc_no=doc_no,
            index_data_items=index_data_items,
            streams_to_update=streams_to_update or None,
            stream_nos_to_delete=args.get("stream_nos_to_delete"),
            streams_to_rename=streams_to_rename or None,
            check_in_comments=check_in_comments,
            do_fill_dependent_fields=do_fill_dependent_fields,
            last_change_time=last_change_time,
            last_change_time_iso=last_change_time_iso,
            conversion_options=self._normalize_conversion_options(
                args.get("conversion_options")
            ),
        )
        updated = client.get_document(
            doc_no, include_index_data=True, include_streams_info=True
        )
        return {
            "update_response": update_resp,
            "updated_document": updated,
        }

    def _add_streams_to_document(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        doc_no = int(args["doc_no"])
        check_in_comments = args.get("check_in_comments", "")
        conversion_options = self._normalize_conversion_options(
            args.get("conversion_options")
        )

        streams_to_upload = []
        for s in args.get("streams") or []:
            file_name = s.get("file_name")
            file_data_base64 = s.get("file_data_base64")
            file_data_text = s.get("file_data_text")
            if file_data_text and not file_data_base64:
                file_data_base64 = base64.b64encode(
                    file_data_text.encode("utf-8")
                ).decode("ascii")
            if not file_name:
                raise ValueError("stream missing file_name")
            if not file_data_base64:
                raise ValueError("stream missing file_data_base64 or file_data_text")
            entry = {
                "FileName": file_name,
                "FileDataBase64JSON": file_data_base64,
                "NewStreamInsertMode": self._normalize_stream_insert_mode(
                    s.get("new_stream_insert_mode", 0)
                ),
            }
            if s.get("stream_no") is not None:
                entry["StreamNo"] = int(s.get("stream_no"))
            streams_to_upload.append(entry)

        add_resp = client.add_streams_to_document(
            doc_no=doc_no,
            streams=streams_to_upload,
            conversion_options=conversion_options,
            check_in_comments=check_in_comments,
        )
        updated = client.get_document(
            doc_no, include_index_data=False, include_streams_info=True
        )
        return {
            "add_streams_response": add_resp,
            "updated_streams_info": updated.get("StreamsInfo"),
        }

    def _get_converted_doc_streams(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        doc_no = int(args["doc_no"])
        conversion_options: Dict[str, Any] = {}
        if args.get("convert_to") is not None:
            conversion_options["ConvertTo"] = args["convert_to"]
        if args.get("annotation_mode") is not None:
            conversion_options["AnnotationMode"] = args["annotation_mode"]
        if args.get("signature_mode") is not None:
            conversion_options["SignatureMode"] = args["signature_mode"]
        if args.get("certificate_name") is not None:
            conversion_options["CertificateName"] = args["certificate_name"]
        if args.get("time_stamp_server") is not None:
            conversion_options["TimeStampServer"] = args["time_stamp_server"]
        if args.get("time_stamp_user") is not None:
            conversion_options["TimeStampUser"] = args["time_stamp_user"]
        if args.get("time_stamp_pwd") is not None:
            conversion_options["TimeStampPwd"] = args["time_stamp_pwd"]
        if args.get("multipage_stream_name") is not None:
            conversion_options["MultipageStreamName"] = args["multipage_stream_name"]
        conversion_options = (
            self._normalize_conversion_options(conversion_options) or {}
        )
        return client.get_converted_doc_streams(
            doc_no=doc_no,
            conversion_options=conversion_options,
            stream_nos=args.get("stream_nos"),
            version_no=args.get("version_no"),
            is_file_data_base64_json_needed=True,
            retrieve_reason=args.get("retrieve_reason"),
            archive_converted_files=args.get("archive_converted_files"),
            custom_archive_file_name=args.get("custom_archive_file_name"),
        )

    def _get_logfiles(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        from datetime import timedelta

        days_back = int(args.get("days_back", 7))
        application_filter = args.get("application_filter")
        max_docs = int(args.get("max_docs", 10))
        include_raw = bool(args.get("include_raw", False))
        output_mode = args.get("output_mode", "analysis")
        severity_filter = args.get("severity_filter", "all")

        # Validate category 1 is the Logfiles category
        cat_info = client.get_category_info(1)
        cat_name = (cat_info.get("Name") or "").strip()

        # Discover field numbers from data fields (skip labels with TypeNo=4)
        generated_field_no = None
        application_field_no = None
        data_field_types = {1, 2, 3, 7}  # text, number, date, datetime

        for f in cat_info.get("CategoryFields") or []:
            type_no = f.get("TypeNo")
            if type_no not in data_field_types:
                continue
            caption = (f.get("Caption") or "").lower()
            fno = f.get("FieldNo")
            if caption == "generated" and type_no == 7:
                generated_field_no = fno
            elif caption == "application" and type_no == 1:
                application_field_no = fno

        if generated_field_no is None:
            raise ValueError(
                f"Category 1 ('{cat_name}') does not have a 'Generated' datetime data field. "
                "Expected the Logfiles category."
            )

        # Build query conditions
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days_back)
        cutoff_str = cutoff.strftime("%Y-%m-%dT00:00:00")

        conditions = [
            {"FieldNo": generated_field_no, "Condition": f">= {cutoff_str}"},
        ]
        if application_filter:
            if application_field_no is None:
                raise ValueError(
                    "Cannot filter by application: 'Application' text field not found in category 1."
                )
            conditions.append(
                {"FieldNo": application_field_no, "Condition": application_filter}
            )

        query = {
            "CategoryNo": 1,
            "Condition": conditions,
            "MaxRows": max_docs,
        }

        query_result = client.execute_single_query(query)
        qr = query_result.get("QueryResult") or {}
        rows = qr.get("ResultRows") or []

        documents = []
        fetch_errors = []
        all_entries: List[Dict[str, Any]] = []
        doc_meta: List[Dict[str, Any]] = []

        for row in rows:
            doc_no = row.get("DocNo")
            if not doc_no:
                continue
            try:
                doc = client.get_document(
                    int(doc_no), include_streams_data=True, include_index_data=False
                )
                streams_info = doc.get("StreamsInfo") or []
                doc_entries: List[Dict[str, Any]] = []
                raw_texts: List[str] = []
                doc_application = ""
                doc_server = ""

                for stream in streams_info:
                    b64_data = stream.get("StreamDataBase64JSON") or stream.get(
                        "FileDataBase64JSON"
                    )
                    if not b64_data:
                        continue
                    raw_bytes = base64.b64decode(b64_data)
                    if raw_bytes.startswith(b"\xef\xbb\xbf"):
                        raw_bytes = raw_bytes[3:]
                    text = raw_bytes.decode("utf-8", errors="replace")
                    if include_raw:
                        raw_texts.append(text)
                    parsed = MCPServer._parse_log_text(text, include_raw=include_raw)
                    header = parsed.get("header", {})
                    if not doc_application and header.get("application"):
                        doc_application = header["application"]
                    if not doc_server and header.get("server"):
                        doc_server = header["server"]
                    doc_entries.extend(parsed.get("entries", []))

                if severity_filter == "errors_only":
                    doc_entries = [
                        e
                        for e in doc_entries
                        if e.get("error_code", "0").strip() not in ("", "0")
                    ]
                all_entries.extend(doc_entries)

                # Extract first date from entries for doc metadata
                doc_date = ""
                for e in doc_entries:
                    ts = e.get("timestamp", "")
                    if ts:
                        doc_date = ts.split(",")[0].split("T")[0].strip()
                        break

                doc_meta.append(
                    {
                        "doc_no": int(doc_no),
                        "application": doc_application,
                        "server": doc_server,
                        "date": doc_date,
                        "entry_count": len(doc_entries),
                    }
                )

                if output_mode == "full":
                    doc_result: Dict[str, Any] = {
                        "doc_no": doc_no,
                        "metadata": {k: row.get(k) for k in row if k != "DocNo"},
                        "entry_count": len(doc_entries),
                        "entries": doc_entries,
                    }
                    if include_raw:
                        doc_result["raw_streams"] = raw_texts
                    documents.append(doc_result)

            except Exception as exc:
                fetch_errors.append({"doc_no": doc_no, "error": str(exc)})

        # Branch on output mode
        if output_mode == "full":
            result: Dict[str, Any] = {
                "status": "ok",
                "documents": documents,
                "summary": {
                    "total_documents": len(documents),
                    "total_entries": len(all_entries),
                    "days_back": days_back,
                    "query_rows_returned": len(rows),
                },
            }
            if fetch_errors:
                result["fetch_errors"] = fetch_errors
            return result

        # Summary or analysis mode
        summary = MCPServer._summarize_log_entries(
            all_entries, doc_meta, compact=(output_mode == "analysis")
        )
        result = {
            "status": "ok",
            **summary,
        }
        if fetch_errors:
            result["fetch_errors"] = fetch_errors
        return result

    def _get_login_history(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        days_back = int(args.get("days_back", 7))
        username = args.get("username")
        max_entries = int(args.get("max_entries", 1000))
        output_mode = args.get("output_mode", "analysis")

        # Compute TimestampFrom
        from datetime import datetime, timedelta, timezone

        ts_from = datetime.now(timezone.utc) - timedelta(days=days_back)
        timestamp_from = ts_from.strftime("%Y-%m-%dT%H:%M:%S")

        if username:
            # --- Single-user mode: resolve and fetch for one user ---
            best_user, candidates, needs_confirmation = self._resolve_user_from_query(
                username, tenant, client
            )
            if not best_user:
                return {
                    "status": "error",
                    "error": f'No user found matching "{username}".',
                }
            if needs_confirmation:
                return {
                    "status": "needs_confirmation",
                    "message": f'Ambiguous username "{username}". Please confirm or be more specific.',
                    "candidates": candidates,
                }
            user_no = best_user.get("UserId")
            resolved_user_info = {
                "UserId": best_user.get("UserId"),
                "UserName": best_user.get("UserName"),
                "DisplayName": best_user.get("DisplayName"),
                "SMTP": best_user.get("SMTP"),
            }

            # Domain/AD accounts resolve to UserId 0 — login history is not available for them
            if user_no == 0:
                return {
                    "status": "ok",
                    "warning": (
                        f'User "{resolved_user_info["DisplayName"]}" is a domain account (UserId=0). '
                        "Login history is only available for native Therefore accounts, not domain/AD accounts."
                    ),
                    "tenant": tenant,
                    "days_back": days_back,
                    "total_entries": 0,
                    "resolved_user": resolved_user_info,
                }

            resp = client.get_login_history(
                max_entries=max_entries, timestamp_from=timestamp_from, user_no=user_no
            )
            entries = resp.get("Entries") or []
            # Tag entries with user identity
            for entry in entries:
                entry["_UserNo"] = user_no
                entry["_DisplayName"] = resolved_user_info["DisplayName"]
                entry["_UserName"] = resolved_user_info["UserName"]

            result = self._build_login_history_result(
                entries, tenant, days_back, output_mode, all_users_mode=False
            )
            result["resolved_user"] = resolved_user_info
            return result
        else:
            # --- All-users mode: enumerate users and fetch per-user ---
            users_resp = client.execute_users_query(query="", flags=5)
            all_users = users_resp.get("Users") or []
            # Filter out service accounts
            active_users = [u for u in all_users if not u.get("ServiceAccount", False)]

            entries: List[Dict[str, Any]] = []
            users_queried = 0
            users_with_logins = 0
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def fetch_user_history(user: Dict[str, Any]) -> List[Dict[str, Any]]:
                uid = user.get("UserId")
                try:
                    resp = client.get_login_history(
                        max_entries=max_entries,
                        timestamp_from=timestamp_from,
                        user_no=uid,
                    )
                    user_entries = resp.get("Entries") or []
                    for entry in user_entries:
                        entry["_UserNo"] = uid
                        entry["_DisplayName"] = (
                            user.get("DisplayName") or user.get("UserName") or str(uid)
                        )
                        entry["_UserName"] = user.get("UserName") or str(uid)
                    return user_entries
                except Exception:
                    return []

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(fetch_user_history, u): u for u in active_users
                }
                for future in as_completed(futures):
                    users_queried += 1
                    user_entries = future.result()
                    if user_entries:
                        users_with_logins += 1
                        entries.extend(user_entries)

            # Sort combined entries by timestamp descending
            entries.sort(key=lambda e: e.get("Timestamp", ""), reverse=True)

            result = self._build_login_history_result(
                entries, tenant, days_back, output_mode, all_users_mode=True
            )
            result["users_queried"] = users_queried
            result["users_with_logins"] = users_with_logins
            return result

    def _build_login_history_result(
        self,
        entries: List[Dict[str, Any]],
        tenant: str,
        days_back: int,
        output_mode: str,
        all_users_mode: bool,
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "status": "ok",
            "tenant": tenant,
            "days_back": days_back,
            "total_entries": len(entries),
        }

        # Full mode — return raw entries
        if output_mode == "full":
            result["entries"] = entries
            return result

        # Analysis mode
        successes = 0
        failures = 0
        daily: Dict[str, Dict[str, int]] = {}  # date -> {success, failure}
        by_client: Dict[str, int] = {}  # client+version -> count
        by_ip: Dict[str, int] = {}  # IP -> count
        by_node: Dict[str, int] = {}  # node -> count
        by_error: Dict[int, Dict[str, Any]] = {}  # error_code -> {count, examples}
        by_user: Dict[
            str, Dict[str, Any]
        ] = {}  # display_name -> {user_no, username, success, failure}

        for entry in entries:
            error_code = entry.get("ErrorCode", 0)
            is_success = error_code == 0
            if is_success:
                successes += 1
            else:
                failures += 1

            # Daily breakdown
            ts = entry.get("Timestamp") or ""
            day = ts[:10] if len(ts) >= 10 else "unknown"
            if day not in daily:
                daily[day] = {"success": 0, "failure": 0}
            daily[day]["success" if is_success else "failure"] += 1

            # Client breakdown
            client_name = entry.get("Client") or "unknown"
            version_str = entry.get("ClientVersionString") or ""
            client_key = (
                f"{client_name} {version_str}".strip() if version_str else client_name
            )
            by_client[client_key] = by_client.get(client_key, 0) + 1

            # IP breakdown
            ip = entry.get("IPAddress") or "unknown"
            by_ip[ip] = by_ip.get(ip, 0) + 1

            # Node breakdown
            node = entry.get("NodeName") or "unknown"
            by_node[node] = by_node.get(node, 0) + 1

            # Per-user breakdown
            display_name = entry.get("_DisplayName") or "unknown"
            if display_name not in by_user:
                by_user[display_name] = {
                    "user_no": entry.get("_UserNo"),
                    "username": entry.get("_UserName") or "",
                    "success": 0,
                    "failure": 0,
                }
            by_user[display_name]["success" if is_success else "failure"] += 1

            # Error breakdown
            if not is_success:
                if error_code not in by_error:
                    by_error[error_code] = {"count": 0, "examples": []}
                by_error[error_code]["count"] += 1
                if len(by_error[error_code]["examples"]) < 3:
                    by_error[error_code]["examples"].append(
                        {
                            "timestamp": ts,
                            "client": client_key,
                            "ip": ip,
                            "node": node,
                            "user": display_name,
                        }
                    )

        total = successes + failures
        result["summary"] = {
            "total_logins": total,
            "successes": successes,
            "failures": failures,
            "failure_rate_pct": round(failures / total * 100, 1) if total else 0,
        }

        # Daily activity sorted by date
        result["daily_activity"] = [
            {"date": d, **counts} for d, counts in sorted(daily.items())
        ]

        # Per-user breakdown sorted by total logins desc
        if all_users_mode:
            result["by_user"] = [
                {
                    "display_name": name,
                    "user_no": info["user_no"],
                    "username": info["username"],
                    "success": info["success"],
                    "failure": info["failure"],
                    "total": info["success"] + info["failure"],
                }
                for name, info in sorted(
                    by_user.items(),
                    key=lambda x: x[1]["success"] + x[1]["failure"],
                    reverse=True,
                )
            ]

        # Client breakdown sorted by count desc
        result["by_client"] = [
            {"client": k, "count": v}
            for k, v in sorted(by_client.items(), key=lambda x: x[1], reverse=True)
        ]

        # IP breakdown top 20
        result["by_ip"] = [
            {"ip": k, "count": v}
            for k, v in sorted(by_ip.items(), key=lambda x: x[1], reverse=True)[:20]
        ]

        # Node breakdown sorted by count desc
        result["by_node"] = [
            {"node": k, "count": v}
            for k, v in sorted(by_node.items(), key=lambda x: x[1], reverse=True)
        ]

        # Error breakdown sorted by count desc
        if by_error:
            result["errors"] = [
                {
                    "error_code": code,
                    "count": info["count"],
                    "examples": info["examples"],
                }
                for code, info in sorted(
                    by_error.items(), key=lambda x: x[1]["count"], reverse=True
                )
            ]

        return result

    # Semantic names for pipe-delimited log fields (by positional index)
    _LOG_FIELD_NAMES = {
        0: "timestamp",
        1: "user",
        2: "source",
        3: "action",
        4: "error_code",
        5: "doc_no",
        6: "version_no",
        7: "category",
        # 8 is variable/unused — kept as f8
        9: "detail",
        10: "extra_info",
    }

    @staticmethod
    def _parse_log_text(text: str, include_raw: bool = False) -> Dict[str, Any]:
        lines = text.splitlines()

        # Parse header block (lines before first blank line)
        header: Dict[str, str] = {}
        data_start = 0
        found_blank = False
        for i, line in enumerate(lines):
            if not line.strip():
                data_start = i + 1
                found_blank = True
                break
            if ":" in line:
                key, _, val = line.partition(":")
                header[key.strip().lower()] = val.strip()
        if not found_blank:
            # No blank separator — treat everything as data, discard partial header
            header = {}
            data_start = 0

        entries = []
        for line in lines[data_start:]:
            line = line.strip()
            if not line:
                continue

            parts = line.split("|")

            # Pipe-delimited log line (6+ fields)
            if len(parts) >= 6:
                entry: Dict[str, Any] = {}
                if include_raw:
                    entry["raw"] = line
                for idx, val in enumerate(parts):
                    name = MCPServer._LOG_FIELD_NAMES.get(idx, f"f{idx}")
                    entry[name] = val.strip()
                if header:
                    entry["application"] = header.get("application", "")
                    entry["server"] = header.get("server", "")
                entries.append(entry)

            # Non-pipe line starting with a timestamp (e.g. Content Connector logs)
            elif len(parts) <= 2:
                match = re.match(
                    r"^(\d{4}[-/]\d{2}[-/]\d{2}[,T]?\s*\d{2}:\d{2}:\d{2})\s+(.*)$", line
                )
                if match:
                    entries.append(
                        {
                            "timestamp": match.group(1),
                            "message": match.group(2),
                        }
                    )
                else:
                    # Unparsed line — include raw so the calling LLM can still use it
                    entries.append({"raw": line})

        return {
            "header": header,
            "entries": entries,
        }

    @staticmethod
    def _summarize_log_entries(
        all_entries: List[Dict[str, Any]],
        doc_metadata: List[Dict[str, Any]],
        compact: bool = False,
    ) -> Dict[str, Any]:
        """Aggregate parsed log entries into an analysis summary.

        When compact=True (analysis mode), errors are grouped by
        (error_code, action) with counts and representative examples
        instead of listing every individual error. User activity is
        capped at top 20.
        """
        from collections import Counter, defaultdict

        action_counts: Counter = Counter()
        daily_events: Dict[str, int] = {}
        daily_errors: Dict[str, int] = {}
        error_entries: List[Dict[str, Any]] = []
        error_by_code: Counter = Counter()
        error_by_action: Counter = Counter()
        user_counts: Counter = Counter()
        service_events: List[Dict[str, Any]] = []

        # For compact mode: group errors by (error_code, action)
        error_groups: Dict[tuple, Dict[str, Any]] = defaultdict(
            lambda: {
                "count": 0,
                "users": set(),
                "first_seen": "",
                "last_seen": "",
                "daily_distribution": Counter(),
                "example_detail": "",
            }
        )

        service_actions = {
            "Server Start",
            "Server Stop",
            "Content Connector Start",
            "Content Connector Stop",
            "Migrate Start",
            "Migrate Stop",
        }

        for entry in all_entries:
            action = entry.get("action", "")
            error_code = entry.get("error_code", "0")
            timestamp = entry.get("timestamp", "")
            user = entry.get("user", "")

            # Count actions
            if action:
                action_counts[action] += 1

            # Extract date from timestamp (format: "YYYY-MM-DD, HH:MM:SS" or similar)
            date_str = ""
            if timestamp:
                date_str = timestamp.split(",")[0].split("T")[0].strip()
            if date_str:
                daily_events[date_str] = daily_events.get(date_str, 0) + 1

            # Track errors (non-zero, non-empty error_code)
            is_error = error_code and error_code.strip() and error_code.strip() != "0"
            if is_error:
                code = error_code.strip()
                if date_str:
                    daily_errors[date_str] = daily_errors.get(date_str, 0) + 1
                error_by_code[code] += 1
                if action:
                    error_by_action[action] += 1

                # Build detail string
                detail_parts = []
                if entry.get("detail"):
                    detail_parts.append(entry["detail"])
                if entry.get("extra_info"):
                    detail_parts.append(entry["extra_info"])
                detail = "; ".join(detail_parts) if detail_parts else ""

                if compact:
                    # Accumulate into group
                    group_key = (code, action)
                    grp = error_groups[group_key]
                    grp["count"] += 1
                    if user and user.strip():
                        grp["users"].add(user.strip())
                    if not grp["first_seen"] or timestamp < grp["first_seen"]:
                        grp["first_seen"] = timestamp
                    if not grp["last_seen"] or timestamp > grp["last_seen"]:
                        grp["last_seen"] = timestamp
                    if date_str:
                        grp["daily_distribution"][date_str] += 1
                    if detail and not grp["example_detail"]:
                        grp["example_detail"] = detail
                else:
                    error_entries.append(
                        {
                            "timestamp": timestamp,
                            "application": entry.get("application", ""),
                            "action": action,
                            "error_code": code,
                            "user": user,
                            "detail": detail,
                        }
                    )

            # Track user activity (skip empty/system users)
            if user and user.strip():
                user_counts[user.strip()] += 1

            # Track service events
            if action in service_actions:
                service_events.append(
                    {
                        "timestamp": timestamp,
                        "application": entry.get("application", ""),
                        "server": entry.get("server", ""),
                        "event": action,
                    }
                )

        # Build daily_activity sorted descending by date
        all_dates = sorted(
            set(list(daily_events.keys()) + list(daily_errors.keys())), reverse=True
        )
        daily_activity = [
            {
                "date": d,
                "events": daily_events.get(d, 0),
                "errors": daily_errors.get(d, 0),
            }
            for d in all_dates
        ]

        # Determine period
        period_from = all_dates[-1] if all_dates else ""
        period_to = all_dates[0] if all_dates else ""

        # User activity — capped at top 20 in compact mode
        top_n = 20 if compact else None
        user_activity = [
            {"user": u, "actions": c} for u, c in user_counts.most_common(top_n)
        ]

        # Action counts — capped at top 20 in compact mode
        action_counts_dict = dict(action_counts.most_common(20 if compact else None))

        total_errors = sum(error_by_code.values())

        result_analysis: Dict[str, Any] = {
            "period": {"from": period_from, "to": period_to},
            "total_entries": len(all_entries),
            "total_errors": total_errors,
            "action_counts": action_counts_dict,
            "daily_activity": daily_activity,
            "error_summary": {
                "by_code": dict(error_by_code.most_common()),
                "by_action": dict(error_by_action.most_common()),
            },
            "user_activity": user_activity,
            "service_events": service_events,
        }

        result: Dict[str, Any] = {
            "analysis": result_analysis,
            "documents": doc_metadata,
        }

        if compact:
            # Build grouped error list sorted by count descending
            grouped_errors = []
            for (code, action), grp in sorted(
                error_groups.items(), key=lambda x: x[1]["count"], reverse=True
            ):
                grouped_errors.append(
                    {
                        "error_code": code,
                        "action": action,
                        "count": grp["count"],
                        "example_detail": grp["example_detail"],
                        "users": sorted(grp["users"]) if grp["users"] else ["(none)"],
                        "first_seen": grp["first_seen"],
                        "last_seen": grp["last_seen"],
                        "daily_distribution": dict(
                            sorted(grp["daily_distribution"].items())
                        ),
                    }
                )
            result["grouped_errors"] = grouped_errors
        else:
            result["errors"] = error_entries

        return result

    @staticmethod
    def _normalize_enum_value(
        value: Any, mapping: Dict[str, int], field_name: str
    ) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if text.isdigit():
                return int(text)
            key = re.sub(r"[^a-z0-9]+", "", text.lower())
            if key in mapping:
                return mapping[key]
        raise ValueError(f"Invalid {field_name} value: {value}")

    def _normalize_conversion_options(
        self, options: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        if not options:
            return None

        # Normalize keys to expected WebAPI names.
        key_map = {
            "annotationmode": "AnnotationMode",
            "convertto": "ConvertTo",
            "signaturemode": "SignatureMode",
            "certificatename": "CertificateName",
            "timestamppwd": "TimeStampPwd",
            "timestampserver": "TimeStampServer",
            "timestampuser": "TimeStampUser",
            "multipagestreamname": "MultipageStreamName",
        }

        normalized: Dict[str, Any] = {}
        for k, v in options.items():
            key = re.sub(r"[^a-z0-9]+", "", str(k).lower())
            out_key = key_map.get(key, k)
            normalized[out_key] = v

        convert_to_map = {
            "original": 0,
            "singletiff": 1,
            "singlepdf": 2,
            "multipagetiff": 3,
            "multipagepdf": 4,
            "searchablepdf": 5,
            "searchablepdfa": 6,
            "jpeg": 50,
            "jpg": 50,
        }
        annotation_mode_map = {
            "default": 0,
            "merge": 1,
            "hide": 2,
        }
        signature_mode_map = {
            "nosignature": 0,
            "signatureonly": 1,
            "signatureandtimestamp": 2,
        }

        if "ConvertTo" in normalized:
            normalized["ConvertTo"] = self._normalize_enum_value(
                normalized.get("ConvertTo"), convert_to_map, "ConvertTo"
            )
        if "AnnotationMode" in normalized:
            normalized["AnnotationMode"] = self._normalize_enum_value(
                normalized.get("AnnotationMode"), annotation_mode_map, "AnnotationMode"
            )
        if "SignatureMode" in normalized:
            normalized["SignatureMode"] = self._normalize_enum_value(
                normalized.get("SignatureMode"), signature_mode_map, "SignatureMode"
            )

        return normalized

    @staticmethod
    def _normalize_stream_insert_mode(value: Any) -> int:
        mapping = {
            "append": 0,
            "prepend": 1,
        }
        normalized = MCPServer._normalize_enum_value(
            value, mapping, "NewStreamInsertMode"
        )
        return int(normalized) if normalized is not None else 0

    def _normalize_workflow_flags(self, value: Any) -> int:
        mapping = {
            "defaultinstances": 0,
            "runninginstances": 1,
            "finishedinstances": 2,
            "allinstances": 3,
            "errorinstances": 4,
            "overdueinstances": 8,
            "running": 1,
            "finished": 2,
            "all": 3,
            "error": 4,
            "overdue": 8,
            "default": 0,
        }
        normalized = self._normalize_enum_value(value, mapping, "WorkflowFlags")
        if normalized is None:
            return 0
        return int(normalized)

    @staticmethod
    def _default_workflow_max_rows(client: ThereforeClient) -> int:
        try:
            value = int(client.config.workflow_max_rows or 10000)
        except (TypeError, ValueError):
            value = 10000
        return value if value >= 10000 else 10000

    @staticmethod
    def _get_local_tz() -> timezone:
        tz_name = os.environ.get("THEREFORE_LOCAL_TZ")
        if tz_name:
            if ZoneInfo is not None:
                try:
                    return ZoneInfo(tz_name)
                except Exception:
                    return timezone.utc
        return timezone.utc

    @staticmethod
    def _parse_datetime_value(value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        text = str(value).strip()
        if not text:
            return None
        if text.startswith("/Date(") and text.endswith(")/"):
            try:
                ms = int(text[6:-2])
            except ValueError:
                return None
            if ms == -2209161600000:
                return None
            try:
                return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
            except (OSError, OverflowError):
                return None
        # ISO8601 variants
        pattern = re.compile(
            r"^(?P<date>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
            r"(?P<frac>\.\d+)?"
            r"(?P<tz>Z|[+-]\d{2}:\d{2})?$"
        )
        match = pattern.match(text)
        if not match:
            return None
        base = match.group("date")
        frac = match.group("frac") or ""
        tz = match.group("tz") or "+00:00"
        if tz == "Z":
            tz = "+00:00"
        if frac:
            # trim to microseconds (6 digits)
            frac_digits = frac[1:]
            if len(frac_digits) > 6:
                frac_digits = frac_digits[:6]
            frac = "." + frac_digits
        iso = f"{base}{frac}{tz}"
        try:
            return datetime.fromisoformat(iso)
        except ValueError:
            return None

    def _format_local_datetime(self, value: Any) -> Optional[str]:
        dt = self._parse_datetime_value(value)
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_tz = self._get_local_tz()
        try:
            local_dt = dt.astimezone(local_tz)
        except Exception:
            local_dt = dt.astimezone(timezone.utc)
        tz_name = local_dt.tzname() or ""
        if not tz_name:
            offset = local_dt.utcoffset()
            if offset is None:
                tz_name = "UTC"
            else:
                total_seconds = int(offset.total_seconds())
                sign = "+" if total_seconds >= 0 else "-"
                total_seconds = abs(total_seconds)
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                tz_name = f"UTC{sign}{hours:02d}:{minutes:02d}"
        return local_dt.strftime("%Y-%m-%d %H:%M:%S ") + tz_name

    @staticmethod
    def _debug_log(path: Optional[str], payload: Dict[str, Any]) -> None:
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        try:
            payload = dict(payload)
            payload.setdefault("ts", datetime.now(timezone.utc).isoformat())
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:
            # best-effort logging only
            return

    def _extract_user_values(self, user: Dict[str, Any]) -> List[str]:
        values = []
        for key in ("UserName", "DisplayName", "SMTP"):
            val = user.get(key)
            if isinstance(val, str) and val.strip():
                values.append(val.strip())
        return values

    def _resolve_user_from_query(
        self, query: str, tenant: str, client: ThereforeClient, flags: int = 5
    ) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], bool]:
        query = (query or "").strip()
        if not query:
            return None, [], False
        domain_names = []
        try:
            domain_info = client.get_domain_info() or {}
            domain_names = domain_info.get("DomainNames") or []
        except Exception:
            domain_names = []

        try:
            resp = client.execute_users_query(
                query=query, domain_names=domain_names, flags=flags
            )
        except Exception:
            resp = client.execute_users_query(
                query=query, domain_names=None, flags=flags
            )

        users = resp.get("Users") or []
        if not users:
            return None, [], False

        # Score candidates by query vs user fields.
        scored = []
        for u in users:
            candidate = {
                "UserId": u.get("UserId"),
                "UserName": u.get("UserName"),
                "DisplayName": u.get("DisplayName"),
                "SMTP": u.get("SMTP"),
                "DomainName": u.get("DomainName"),
            }
            score = max(
                self._score(query, str(u.get("DisplayName") or "")),
                self._score(query, str(u.get("UserName") or "")),
                self._score(query, str(u.get("SMTP") or "")),
            )
            scored.append((score, candidate, u))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_candidate, best_full = scored[0]
        needs_confirmation = True
        if best_score >= 0.75 and (
            len(scored) == 1 or best_score - scored[1][0] >= 0.15
        ):
            needs_confirmation = False

        candidates = []
        for score, candidate, _ in scored[:5]:
            cand = dict(candidate)
            cand["score"] = round(score, 4)
            candidates.append(cand)

        return (
            (best_full if not needs_confirmation else best_full),
            candidates,
            needs_confirmation,
        )

    def _resolve_group_membership(
        self,
        client: ThereforeClient,
        group_names: List[str],
        match_values: List[str],
        domain_name: Optional[str] = None,
    ) -> Dict[str, bool]:
        membership: Dict[str, bool] = {}
        for name in group_names:
            if not name:
                continue
            if name in membership:
                continue
            try:
                resp = client.get_users_from_group(
                    group_name=name, domain_name=domain_name
                )
            except Exception:
                membership[name] = False
                continue
            users = resp.get("Users") or []
            found = False
            for user in users:
                if self._match_user_value(user.get("UserName"), match_values):
                    found = True
                    break
                if self._match_user_value(user.get("DisplayName"), match_values):
                    found = True
                    break
                if self._match_user_value(user.get("SMTP"), match_values):
                    found = True
                    break
            membership[name] = found
        return membership

    def _match_user_value(self, value: Any, user_values: List[str]) -> bool:
        if value is None:
            return False
        text = str(value).strip()
        if not text:
            return False
        lower = text.lower()
        for uv in user_values:
            if not uv:
                continue
            uv_lower = uv.lower()
            if lower == uv_lower:
                return True
            if uv_lower in lower:
                return True
        return False

    @staticmethod
    def _coerce_int_list(value: Any) -> List[int]:
        if value is None:
            return []
        items: List[int] = []
        if isinstance(value, (list, tuple, set)):
            for v in value:
                try:
                    items.append(int(v))
                except (TypeError, ValueError):
                    continue
            return items
        try:
            return [int(value)]
        except (TypeError, ValueError):
            return []

    @staticmethod
    def _group_name_candidates(name: str) -> List[str]:
        if not name:
            return []
        candidates = [name]
        if "\\" in name:
            candidates.append(name.split("\\", 1)[1])
        if "/" in name:
            candidates.append(name.split("/", 1)[1])
        # Deduplicate while preserving order.
        seen = set()
        result = []
        for cand in candidates:
            if cand and cand not in seen:
                seen.add(cand)
                result.append(cand)
        return result

    def _summarize_workflow_instance(self, resp: Dict[str, Any]) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        wf = resp.get("WorkflowInstance") or {}
        linked_docs = resp.get("LinkedDocuments") or []
        current_task = wf.get("CurrentTask") or {}

        summary["InstanceNo"] = wf.get("InstanceNo")
        summary["TokenNo"] = wf.get("TokenNo")
        summary["WorkflowNo"] = wf.get("WorkflowNo")
        summary["ProcessNo"] = wf.get("ProcessNo")
        summary["ProcessName"] = wf.get("ProcessName")
        summary["VersionNo"] = wf.get("VersionNo")

        summary["AssignedTo"] = wf.get("AssignedTo")
        summary["AssignedToUsers"] = wf.get("AssignedToUsers")
        summary["OriginallyAssignedToUsers"] = wf.get("OriginallyAssignedToUsers")
        summary["Claimed"] = wf.get("Claimed")
        summary["IsAssignedToUser"] = wf.get("IsAssignedToUser")
        summary["IsProcessOwner"] = wf.get("IsProcessOwner")

        summary["CurrTaskName"] = wf.get("CurrTaskName") or current_task.get("Name")
        summary["CurrTaskNo"] = wf.get("CurrTaskNo") or current_task.get("TaskNo")
        summary["CurrTaskType"] = wf.get("CurrTaskType") or current_task.get("Type")
        summary["CurrTaskId"] = wf.get("CurrTaskId") or current_task.get("CurrTaskId")
        summary["CurrTaskGUID"] = wf.get("CurrTaskGUID") or current_task.get(
            "CurrTaskGUID"
        )

        summary["TaskStartDate"] = wf.get("TaskStartDateISO8601") or wf.get(
            "TaskStartDate"
        )
        summary["TaskDueDate"] = wf.get("TaskDueDateISO8601") or wf.get("TaskDueDate")
        summary["ProcessStartDate"] = wf.get("ProcessStartDateISO8601") or wf.get(
            "ProcessStartDate"
        )
        summary["ProcessDueDate"] = wf.get("ProcessDueDateISO8601") or wf.get(
            "ProcessDueDate"
        )

        summary["TaskStartLocal"] = self._format_local_datetime(
            summary["TaskStartDate"]
        )
        summary["TaskDueLocal"] = self._format_local_datetime(summary["TaskDueDate"])
        summary["ProcessStartLocal"] = self._format_local_datetime(
            summary["ProcessStartDate"]
        )
        summary["ProcessDueLocal"] = self._format_local_datetime(
            summary["ProcessDueDate"]
        )

        summary["LinkedDocumentsCount"] = len(linked_docs)
        summary["LinkedDocNos"] = [
            doc.get("DocNo")
            for doc in linked_docs
            if isinstance(doc, dict) and doc.get("DocNo") is not None
        ][:10]

        summary["ErrorString"] = wf.get("ErrorString")
        summary["ErrorInfo"] = wf.get("ErrorInfo")
        summary["ErrorTimestamp"] = wf.get("ErrorTimestampISO8601") or wf.get(
            "ErrorTimestamp"
        )
        summary["ErrorTimestampLocal"] = self._format_local_datetime(
            summary["ErrorTimestamp"]
        )

        return summary

    def _fetch_workflow_instance_details(
        self,
        client: ThereforeClient,
        tasks: List[Dict[str, Any]],
        max_workers: int = 4,
        is_access_mask_needed: bool = False,
        load_history: bool = False,
        debug_log_path: Optional[str] = None,
        debug_progress_every: int = 500,
    ) -> Tuple[Dict[Tuple[int, int], Dict[str, Any]], List[Dict[str, Any]]]:
        thread_local = threading.local()

        def get_worker_client() -> ThereforeClient:
            worker = getattr(thread_local, "client", None)
            if worker is None:
                # Reuse a client per thread to avoid repeated SSL/context setup.
                worker = ThereforeClient(client.config)
                thread_local.client = worker
            return worker

        def fetch_with_timing(
            key: Tuple[int, int],
        ) -> Tuple[
            Tuple[int, int], Optional[Dict[str, Any]], float, Optional[Exception]
        ]:
            instance_no, token_no = key
            worker = get_worker_client()
            start = time.time()
            try:
                resp = worker.get_workflow_instance(
                    instance_no=instance_no,
                    token_no=token_no,
                    is_access_mask_needed=is_access_mask_needed,
                    load_history=load_history,
                )
                return key, resp, time.time() - start, None
            except Exception as exc:
                return key, None, time.time() - start, exc

        keys = []
        seen = set()
        for task in tasks:
            instance_no = task.get("InstanceNo")
            if instance_no is None:
                continue
            token_no = int(task.get("TokenNo") or 0)
            key = (int(instance_no), token_no)
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)

        details: Dict[Tuple[int, int], Dict[str, Any]] = {}
        errors: List[Dict[str, Any]] = []
        self._debug_log(
            debug_log_path,
            {
                "event": "instance_details_start",
                "requested": len(keys),
                "max_workers": max_workers,
            },
        )

        if not keys:
            return details, errors

        use_workers = max(1, int(max_workers or 1))
        if use_workers <= 1 or len(keys) == 1:
            for key in keys:
                k, resp, elapsed, err = fetch_with_timing(key)
                if err:
                    errors.append(
                        {"instance_no": key[0], "token_no": key[1], "error": str(err)}
                    )
                elif resp is not None:
                    details[k] = resp
                if debug_log_path and len(details) % max(1, debug_progress_every) == 0:
                    self._debug_log(
                        debug_log_path,
                        {
                            "event": "instance_details_progress",
                            "completed": len(details) + len(errors),
                            "loaded": len(details),
                            "failed": len(errors),
                        },
                    )
            self._debug_log(
                debug_log_path,
                {
                    "event": "instance_details_done",
                    "loaded": len(details),
                    "failed": len(errors),
                },
            )
            return details, errors

        max_cap = min(use_workers, len(keys))
        current_workers = min(4, max_cap)
        min_workers = 1 if current_workers == 1 else min(2, current_workers)
        ramp_step = max(1, max_cap // 4)
        window_size = max(current_workers * 5, 50)
        ewma_latency: Optional[float] = None

        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "instance_details_adaptive_start",
                    "max_workers_cap": max_cap,
                    "initial_workers": current_workers,
                    "min_workers": min_workers,
                    "ramp_step": ramp_step,
                },
            )

        pending = deque(keys)
        in_flight: Dict[Any, Tuple[int, int]] = {}
        window_count = 0
        window_errors = 0
        window_latency = 0.0

        with ThreadPoolExecutor(max_workers=max_cap) as executor:

            def submit_one() -> bool:
                if not pending:
                    return False
                key = pending.popleft()
                future = executor.submit(fetch_with_timing, key)
                in_flight[future] = key
                return True

            while len(in_flight) < current_workers and pending:
                submit_one()

            while in_flight:
                done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
                for future in done:
                    key = in_flight.pop(future)
                    k, resp, elapsed, err = future.result()
                    if err:
                        errors.append(
                            {
                                "instance_no": key[0],
                                "token_no": key[1],
                                "error": str(err),
                            }
                        )
                        window_errors += 1
                    elif resp is not None:
                        details[k] = resp
                    window_count += 1
                    window_latency += float(elapsed or 0.0)

                    if (
                        debug_log_path
                        and (len(details) + len(errors)) % max(1, debug_progress_every)
                        == 0
                    ):
                        self._debug_log(
                            debug_log_path,
                            {
                                "event": "instance_details_progress",
                                "completed": len(details) + len(errors),
                                "loaded": len(details),
                                "failed": len(errors),
                                "current_workers": current_workers,
                            },
                        )

                should_adjust = window_count >= window_size or (
                    not pending and not in_flight
                )
                if should_adjust and window_count > 0:
                    avg_latency = window_latency / max(1, window_count)
                    error_rate = window_errors / max(1, window_count)

                    if ewma_latency is None:
                        ewma_latency = avg_latency
                    else:
                        ewma_latency = (ewma_latency * 0.7) + (avg_latency * 0.3)

                    new_workers = current_workers
                    if error_rate > 0.02:
                        new_workers = max(
                            min_workers, int(max(1, current_workers * 0.75))
                        )
                    elif (
                        ewma_latency
                        and avg_latency > ewma_latency * 1.5
                        and current_workers > min_workers
                    ):
                        new_workers = max(
                            min_workers, current_workers - max(1, current_workers // 4)
                        )
                    elif (
                        error_rate == 0
                        and ewma_latency
                        and avg_latency <= ewma_latency * 1.1
                        and current_workers < max_cap
                    ):
                        new_workers = min(max_cap, current_workers + ramp_step)

                    if new_workers != current_workers:
                        current_workers = new_workers
                        window_size = max(current_workers * 5, 50)
                        if debug_log_path:
                            self._debug_log(
                                debug_log_path,
                                {
                                    "event": "instance_details_throttle",
                                    "current_workers": current_workers,
                                    "error_rate": round(error_rate, 4),
                                    "avg_latency_ms": int(avg_latency * 1000),
                                    "ewma_latency_ms": int((ewma_latency or 0) * 1000),
                                    "pending": len(pending),
                                    "in_flight": len(in_flight),
                                },
                            )

                    window_count = 0
                    window_errors = 0
                    window_latency = 0.0

                while len(in_flight) < current_workers and pending:
                    submit_one()

        self._debug_log(
            debug_log_path,
            {
                "event": "instance_details_done",
                "loaded": len(details),
                "failed": len(errors),
            },
        )
        return details, errors

    def _attach_instance_details(
        self,
        tasks: List[Dict[str, Any]],
        details: Dict[Tuple[int, int], Dict[str, Any]],
        errors: List[Dict[str, Any]],
        detail_mode: str,
    ) -> None:
        if detail_mode not in ("summary", "full"):
            return
        error_map = {
            (e.get("instance_no"), e.get("token_no")): e.get("error") for e in errors
        }
        for task in tasks:
            instance_no = task.get("InstanceNo")
            if instance_no is None:
                continue
            token_no = int(task.get("TokenNo") or 0)
            key = (int(instance_no), token_no)
            if key in error_map:
                task["WorkflowInstanceError"] = error_map.get(key)
            detail = details.get(key)
            if not detail:
                continue
            if detail_mode == "full":
                task["WorkflowInstance"] = detail.get("WorkflowInstance")
                task["LinkedDocuments"] = detail.get("LinkedDocuments")
            else:
                task["WorkflowInstanceSummary"] = self._summarize_workflow_instance(
                    detail
                )

    def _get_workflow_instances_core(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        debug_enabled = bool(args.get("debug", False))
        debug_log_path = args.get("debug_log_path")
        debug_progress_every = int(args.get("debug_progress_every") or 500)
        two_phase = bool(args.get("two_phase", False))
        fetch_details = bool(args.get("fetch_details", False))
        debug_info: Dict[str, Any] = (
            {
                "workflow_query": {},
                "instance_details": {},
                "filtering": {},
            }
            if debug_enabled
            else {}
        )
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "start",
                    "workflow_flags": args.get("workflow_flags"),
                    "task_filter": args.get("task_filter"),
                    "max_rows": args.get("max_rows"),
                    "detail_mode": args.get("instance_detail_mode"),
                },
            )
        task_filter = args.get("task_filter")
        if isinstance(task_filter, str) and task_filter.strip():
            workflow_flags = self._normalize_workflow_flags(task_filter)
        else:
            workflow_flags = self._normalize_workflow_flags(
                args.get("workflow_flags", "RunningInstances")
            )
        if args.get("max_rows") is None:
            max_rows = self._default_workflow_max_rows(client)
        else:
            max_rows = int(args.get("max_rows"))
        filter_to_user_requested = bool(args.get("filter_to_user", True))
        filter_to_user = filter_to_user_requested
        include_unfiltered = bool(args.get("include_unfiltered", False))
        include_overdue_summary = bool(args.get("include_overdue_summary", True))
        resolve_group_membership = bool(args.get("resolve_group_membership", True))
        assignee_values = self._coerce_str_list(args.get("assignee_values")) or []
        assignee_values.extend(self.tenant_assignee_aliases.get(tenant, []))

        detail_mode = str(args.get("instance_detail_mode") or "summary").strip().lower()
        if detail_mode not in ("none", "summary", "full"):
            detail_mode = "summary"
        max_instance_workers = args.get("max_instance_workers")
        if max_instance_workers is None:
            max_instance_workers = 8 if (two_phase and fetch_details) else 4
        max_instance_workers = int(max_instance_workers)
        is_access_mask_needed = bool(args.get("is_access_mask_needed", False))
        load_history = bool(args.get("load_history", False))

        if two_phase and not fetch_details:
            detail_mode = "none"
            filter_to_user = False

        user_query = args.get("user_query")
        user_query_flags = int(args.get("user_query_flags", 5))
        user_candidates = []
        user_needs_confirmation = False
        if isinstance(user_query, str) and user_query.strip():
            user, user_candidates, user_needs_confirmation = (
                self._resolve_user_from_query(
                    user_query, tenant, client, flags=user_query_flags
                )
            )
            if user is None:
                user = {}
        else:
            connected = client.get_connected_user(False) or {}
            user = connected.get("User") or {}

        user_values = self._extract_user_values(user)
        match_values = []
        if user_values:
            match_values.extend(user_values)
        if assignee_values:
            for v in assignee_values:
                if v not in match_values:
                    match_values.append(v)

        start = time.time()
        try:
            resp = client.execute_workflow_query_for_all(
                workflow_flags=workflow_flags, max_rows=max_rows
            )
        except Exception as exc:
            if debug_enabled:
                debug_info["workflow_query"] = {
                    "workflow_flags": workflow_flags,
                    "max_rows": max_rows,
                    "duration_ms": int((time.time() - start) * 1000),
                    "error": str(exc),
                }
                self._debug_log(
                    debug_log_path,
                    {
                        "event": "workflow_query_error",
                        "workflow_flags": workflow_flags,
                        "max_rows": max_rows,
                        "error": str(exc),
                    },
                )
                return {"error": str(exc), "debug": debug_info}
            raise
        if debug_enabled:
            debug_info["workflow_query"] = {
                "workflow_flags": workflow_flags,
                "max_rows": max_rows,
                "duration_ms": int((time.time() - start) * 1000),
            }
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "workflow_query_done",
                    "workflow_flags": workflow_flags,
                    "max_rows": max_rows,
                    "duration_ms": int((time.time() - start) * 1000),
                },
            )
        tasks, user_field_labels, _ = self._extract_workflow_tasks(resp)
        max_rows_reached = len(tasks) == max_rows

        need_instance_details = detail_mode != "none" or filter_to_user
        details: Dict[Tuple[int, int], Dict[str, Any]] = {}
        detail_errors: List[Dict[str, Any]] = []
        if need_instance_details and tasks:
            details_start = time.time()
            details, detail_errors = self._fetch_workflow_instance_details(
                client,
                tasks,
                max_workers=max_instance_workers,
                is_access_mask_needed=is_access_mask_needed,
                load_history=load_history,
                debug_log_path=debug_log_path,
                debug_progress_every=debug_progress_every,
            )
            if debug_enabled:
                debug_info["instance_details"] = {
                    "mode": detail_mode,
                    "requested": len(tasks),
                    "loaded": len(details),
                    "failed": len(detail_errors),
                    "duration_ms": int((time.time() - details_start) * 1000),
                    "errors_sample": detail_errors[:10],
                }
            if debug_log_path:
                self._debug_log(
                    debug_log_path,
                    {
                        "event": "instance_details_done",
                        "requested": len(tasks),
                        "loaded": len(details),
                        "failed": len(detail_errors),
                        "duration_ms": int((time.time() - details_start) * 1000),
                    },
                )

        # Precompute group membership for AssignedTo values.
        group_membership: Dict[str, bool] = {}
        group_candidates: List[str] = []
        if filter_to_user and resolve_group_membership and user_values and details:
            for key, detail in details.items():
                wf = (detail or {}).get("WorkflowInstance") or {}
                assigned_to = wf.get("AssignedTo")
                if not assigned_to:
                    continue
                if self._match_user_value(assigned_to, match_values):
                    continue
                for cand in self._group_name_candidates(str(assigned_to)):
                    if cand not in group_candidates:
                        group_candidates.append(cand)
            if group_candidates:
                domain_name = user.get("DomainName") if isinstance(user, dict) else None
                group_membership = self._resolve_group_membership(
                    client, group_candidates, user_values, domain_name=domain_name
                )

        filtered_tasks = tasks
        filter_applied = False
        unresolved_instances: List[Dict[str, Any]] = []
        if filter_to_user:
            if not match_values and not user.get("UserId") and not user.get("UserNo"):
                filtered_tasks = tasks
                filter_applied = False
            elif not details:
                filtered_tasks = []
                filter_applied = True
            else:
                user_id = user.get("UserId") or user.get("UserNo") or user.get("UserID")
                try:
                    user_id = int(user_id) if user_id is not None else None
                except (TypeError, ValueError):
                    user_id = None
                filtered = []
                for task in tasks:
                    instance_no = task.get("InstanceNo")
                    if instance_no is None:
                        continue
                    token_no = int(task.get("TokenNo") or 0)
                    key = (int(instance_no), token_no)
                    detail = details.get(key)
                    if not detail:
                        unresolved_instances.append(
                            {"instance_no": key[0], "token_no": key[1]}
                        )
                        continue
                    wf = (detail or {}).get("WorkflowInstance") or {}
                    matched = False
                    if not user_query and wf.get("IsAssignedToUser") is True:
                        matched = True
                    if not matched and user_id is not None:
                        assigned_users = self._coerce_int_list(
                            wf.get("AssignedToUsers")
                        )
                        if user_id in assigned_users:
                            matched = True
                    if not matched:
                        assigned_to = wf.get("AssignedTo")
                        if assigned_to and self._match_user_value(
                            assigned_to, match_values
                        ):
                            matched = True
                        elif (
                            assigned_to
                            and resolve_group_membership
                            and group_membership
                        ):
                            if group_membership.get(assigned_to):
                                matched = True
                            else:
                                for cand in self._group_name_candidates(
                                    str(assigned_to)
                                ):
                                    if group_membership.get(cand):
                                        matched = True
                                        break
                    if matched:
                        filtered.append(task)
                filtered_tasks = filtered
                filter_applied = True
        if debug_enabled:
            debug_info["filtering"] = {
                "filter_to_user": filter_to_user,
                "filter_to_user_requested": filter_to_user_requested,
                "filter_applied": filter_applied,
                "total_tasks": len(tasks),
                "filtered_tasks": len(filtered_tasks),
                "user_id": user.get("UserId")
                or user.get("UserNo")
                or user.get("UserID"),
                "match_values_count": len(match_values),
                "group_candidates": len(group_candidates),
                "group_matches": len([k for k, v in group_membership.items() if v]),
                "unresolved_instances": len(unresolved_instances),
            }
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "filtering_done",
                    "filter_to_user": filter_to_user,
                    "filter_applied": filter_applied,
                    "total_tasks": len(tasks),
                    "filtered_tasks": len(filtered_tasks),
                    "group_candidates": len(group_candidates),
                    "group_matches": len([k for k, v in group_membership.items() if v]),
                    "unresolved_instances": len(unresolved_instances),
                },
            )

        if need_instance_details and detail_mode in ("summary", "full"):
            self._attach_instance_details(
                filtered_tasks, details, detail_errors, detail_mode
            )
            if include_unfiltered and filtered_tasks != tasks:
                self._attach_instance_details(
                    tasks, details, detail_errors, detail_mode
                )

        output: Dict[str, Any] = {
            "user": user,
            "user_query": user_query,
            "user_candidates": user_candidates,
            "user_needs_confirmation": user_needs_confirmation,
            "workflow_flags": workflow_flags,
            "task_filter": task_filter,
            "max_rows": max_rows,
            "max_rows_reached": max_rows_reached,
            "total_count": len(tasks),
            "filter_to_user": filter_to_user,
            "filter_to_user_requested": filter_to_user_requested,
            "filter_applied": filter_applied,
            "assignee_values": assignee_values,
            "user_field_labels": user_field_labels,
            "group_membership_matches": [k for k, v in group_membership.items() if v],
            "instance_detail_mode": detail_mode,
            "instance_details_requested": need_instance_details,
            "instance_details_loaded": len(details),
            "instance_details_failed": len(detail_errors),
            "instance_detail_errors": detail_errors,
            "unresolved_instances": unresolved_instances,
            "task_count": len(filtered_tasks),
            "instances": filtered_tasks,
            "two_phase": two_phase,
            "fetch_details": fetch_details,
            "suggested_max_instance_workers": 8
            if two_phase and not fetch_details
            else None,
            "debug": debug_info if debug_enabled else None,
        }
        if debug_log_path:
            self._debug_log(
                debug_log_path,
                {
                    "event": "done",
                    "task_count": len(filtered_tasks),
                    "max_rows_reached": max_rows_reached,
                    "note": output.get("note"),
                },
            )

        overdue_keys = set()
        if include_overdue_summary:
            overdue_resp = client.execute_workflow_query_for_all(
                workflow_flags=self._normalize_workflow_flags("overdue"),
                max_rows=max_rows,
            )
            overdue_all, _, _ = self._extract_workflow_tasks(overdue_resp)
            overdue_keys = {self._task_key(t) for t in overdue_all}

            on_schedule = 0
            overdue = 0
            for task in filtered_tasks:
                key = self._task_key(task)
                is_overdue = key in overdue_keys
                task["IsOverdue"] = is_overdue
                task["ScheduleStatus"] = "overdue" if is_overdue else "on_schedule"
                if is_overdue:
                    overdue += 1
                else:
                    on_schedule += 1

            output["overdue_count"] = overdue
            output["on_schedule_count"] = on_schedule
            output["overdue_tasks_count"] = overdue
            if overdue > 0:
                output["highlight"] = {
                    "message": f"{overdue} overdue task(s) found.",
                    "overdue_count": overdue,
                    "on_schedule_count": on_schedule,
                }

        if include_unfiltered and filtered_tasks != tasks:
            output["all_tasks_count"] = len(tasks)
            output["all_tasks"] = tasks

        if two_phase and not fetch_details:
            output["note"] = (
                "Two-phase mode: returning overall counts only. Re-run with fetch_details=true to filter by assignment."
            )
        elif (
            filter_to_user
            and not match_values
            and not user.get("UserId")
            and not user.get("UserNo")
        ):
            output["note"] = "No assignee values available for filtering."
        elif filter_to_user and filter_applied and not filtered_tasks and tasks:
            output["note"] = (
                "No tasks matched the user assignment from GetWorkflowInstance."
            )
        if max_rows_reached:
            output["note"] = (
                "Reached max_rows; results may be truncated. Increase max_rows to fetch more."
            )

        return output

    @staticmethod
    def _task_key(task: Dict[str, Any]) -> Tuple[Any, Any, Any]:
        return (
            task.get("InstanceNo"),
            task.get("TokenNo"),
            task.get("WorkflowNo"),
        )

    def _extract_workflow_tasks(
        self, resp: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], List[str], set]:
        results = resp.get("WorkflowQueryResultList") or []
        tasks: List[Dict[str, Any]] = []
        user_field_indexes = set()
        user_field_labels: List[str] = []
        user_field_pattern = re.compile(
            r"(user|assignee|assigned|owner)", re.IGNORECASE
        )

        for result in results:
            columns = result.get("Columns") or []
            col_labels: List[str] = []
            for col in columns:
                label = (
                    col.get("Caption")
                    or col.get("IndexDataFieldName")
                    or col.get("ColName")
                    or ""
                )
                col_labels.append(label)

            for idx, label in enumerate(col_labels):
                if label and user_field_pattern.search(label):
                    user_field_indexes.add(idx)
                    if label not in user_field_labels:
                        user_field_labels.append(label)

            for row in result.get("ResultRows") or []:
                row_values = row.get("IndexValues") or []
                mapped = {}
                for idx, label in enumerate(col_labels):
                    if idx < len(row_values):
                        mapped[label] = row_values[idx]
                entry = {
                    "CaseDefNo": result.get("CaseDefNo"),
                    "CaseDefName": result.get("CaseDefName"),
                    "CategoryNo": result.get("CategoryNo"),
                    "CategoryName": result.get("CategoryName"),
                    "ProcessNo": result.get("ProcessNo"),
                    "ProcessName": result.get("ProcessName"),
                    "WorkflowNo": row.get("WorkflowNo"),
                    "InstanceNo": row.get("InstanceNo"),
                    "TokenNo": row.get("TokenNo"),
                    "Status": row.get("Status"),
                    "IndexValues": mapped,
                }
                for key in ("DocNo", "VersionNo", "Size"):
                    if key in row:
                        entry[key] = row.get(key)
                tasks.append(entry)

        return tasks, user_field_labels, user_field_indexes

    def _get_my_workflow_tasks(
        self, args: Dict[str, Any], tenant: str, client: ThereforeClient
    ) -> Dict[str, Any]:
        args = dict(args or {})
        if args.get("filter_to_user") is None:
            args["filter_to_user"] = True
        output = self._get_workflow_instances_core(args, tenant, client)
        # preserve legacy key
        output["tasks"] = output.get("instances", [])
        return output

    def _normalize_statistics_query_type(self, value: Any) -> int:
        mapping = {
            "undefined": 0,
            "workflowinstancesbyprocess": 100,
            "workflowinstancesbytask": 101,
            "workflowinstancesrunningbyprocess": 102,
            "workflowinstancesrunningbytask": 103,
            "workflowinstancesfinishedbyprocess": 104,
            "workflowinstancesfinishedbytask": 105,
            "workflowoverdueinstancesbyprocess": 106,
            "workflowoverdueinstancesbytask": 107,
            "workflowerrorinstancesbyprocess": 108,
            "workflowerrorinstancesbytask": 109,
            "documentscreatedbycategory": 200,
            "documentscheckedoutbycategory": 201,
            "documentscreatedtodaybycategory": 202,
            "documentscreatedthisweekbycategory": 203,
            "documentscreatedthismonthbycategory": 204,
            "documentscreatedthisyearbycategory": 205,
            "documentscreatedlastweekbycategory": 206,
            "documentscreatedlastmonthbycategory": 207,
            "documentscreatedlastyearbycategory": 208,
            "taskstodo": 400,
            "tasksstarted": 401,
            "tasksdone": 402,
            "tasksallbystate": 403,
            "tasksoverduetodo": 404,
            "tasksoverduestarted": 405,
        }
        normalized = self._normalize_enum_value(value, mapping, "QueryType")
        if normalized is None:
            raise ValueError("QueryType is required")
        return int(normalized)

    def _cache_path(self, template: str, tenant: str) -> str:
        safe = re.sub(r"[^a-z0-9]+", "_", tenant.lower()) or "default"
        return template.format(tenant=safe)

    def _get_cached_categories(
        self, tenant: str, client: ThereforeClient
    ) -> List[Dict[str, Any]]:
        now = time.time()
        if (
            tenant in self._category_cache
            and (now - self._category_cache_ts.get(tenant, 0))
            < self._category_cache_ttl
        ):
            return self._category_cache[tenant]["items"]

        cache_path = self._cache_path(self._category_cache_path, tenant)
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if (now - cached.get("ts", 0)) < self._category_cache_ttl:
                self._category_cache[tenant] = cached
                self._category_cache_ts[tenant] = cached.get("ts", now)
                return cached.get("items") or []
        except Exception:
            pass

        tree = client.get_categories_tree({})
        items = tree.get("TreeItems") or []
        flat = self._flatten_tree(items)
        payload = {"ts": now, "items": flat}
        self._category_cache[tenant] = payload
        self._category_cache_ts[tenant] = now
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        return flat

    def _get_cached_keyword_dictionaries(
        self, tenant: str, client: ThereforeClient
    ) -> List[Dict[str, Any]]:
        now = time.time()
        if (
            tenant in self._keyword_dict_cache
            and (now - self._keyword_dict_cache_ts.get(tenant, 0))
            < self._keyword_dict_cache_ttl
        ):
            return self._keyword_dict_cache[tenant]["items"]

        cache_path = self._cache_path(self._keyword_dict_cache_path, tenant)
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if (now - cached.get("ts", 0)) < self._keyword_dict_cache_ttl:
                self._keyword_dict_cache[tenant] = cached
                self._keyword_dict_cache_ts[tenant] = cached.get("ts", now)
                return cached.get("items") or []
        except Exception:
            pass

        resp = client.get_objects(flags=0, obj_type=22)
        items = self._extract_object_items(resp)
        payload = {"ts": now, "items": items}
        self._keyword_dict_cache[tenant] = payload
        self._keyword_dict_cache_ts[tenant] = now
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        return items

    def _get_cached_fields(
        self, tenant: str, category_no: int, client: ThereforeClient
    ) -> List[Dict[str, Any]]:
        now = time.time()
        tenant_cache = self._field_cache.setdefault(tenant, {})
        tenant_ts = self._field_cache_ts.setdefault(tenant, {})

        if (
            category_no in tenant_cache
            and (now - tenant_ts.get(category_no, 0)) < self._field_cache_ttl
        ):
            return tenant_cache[category_no]["fields"]

        cache_path = self._cache_path(self._field_cache_path, tenant)
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            item = cached.get(str(category_no))
            if item and (now - item.get("ts", 0)) < self._field_cache_ttl:
                tenant_cache[category_no] = item
                tenant_ts[category_no] = item.get("ts", now)
                return item.get("fields") or []
        except Exception:
            pass

        info = client.get_category_info(category_no)
        fields = info.get("CategoryFields") or []
        payload = {"ts": now, "fields": fields}
        tenant_cache[category_no] = payload
        tenant_ts[category_no] = now

        disk_cache = {}
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                disk_cache = json.load(f)
        except Exception:
            disk_cache = {}
        disk_cache[str(category_no)] = payload
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(disk_cache, f, indent=2)
        except Exception:
            pass
        return fields


def _parse_aliases(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"[;,]+", raw) if p.strip()]
    return parts


def load_clients() -> Tuple[
    Dict[str, ThereforeClient], Optional[str], Dict[str, str], Dict[str, List[str]]
]:
    default_env_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.local"
    )
    env_path = os.environ.get("THEREFORE_ENV_PATH", default_env_path)
    env_values = load_env(env_path)
    configs, default_tenant, tenant_labels = build_tenant_configs_from_env(env_values)
    clients: Dict[str, ThereforeClient] = {}
    tenant_aliases: Dict[str, List[str]] = {}
    for key, cfg in configs.items():
        if not cfg.base_url:
            # No static config for this tenant (e.g. no .env.local at all, or an empty
            # THEREFORE_BASE_URL). Skip it instead of crashing the whole server at
            # startup - therefore_connect exists precisely so a server can start with
            # zero preconfigured tenants and have them registered at runtime instead.
            print(
                f"[WARN] Skipping tenant '{tenant_labels.get(key, key)}': no "
                f"THEREFORE_BASE_URL configured. Use therefore_connect to register it "
                f"at runtime instead.",
                file=sys.stderr,
            )
            continue
        clients[key] = ThereforeClient(cfg)
        label = tenant_labels.get(key, key)
        prefix = f"THEREFORE_{str(label).upper()}_"
        raw = (
            env_values.get(prefix + "ASSIGNEE_ALIASES")
            or env_values.get(prefix + "USER_GROUPS")
            or env_values.get("THEREFORE_ASSIGNEE_ALIASES")
            or env_values.get("THEREFORE_USER_GROUPS")
        )
        tenant_aliases[key] = _parse_aliases(raw)

    if default_tenant not in clients:
        default_tenant = next(iter(clients), None)

    if not clients:
        print(
            "[WARN] No tenants configured at startup. The server is running with zero "
            "clients - use therefore_connect to register one at runtime.",
            file=sys.stderr,
        )

    return clients, default_tenant, tenant_labels, tenant_aliases


def run_stdio_mode(server: "MCPServer") -> None:
    """Run the server in stdio mode (MCP standard)."""
    while True:
        try:
            msg = _read_message()
        except json.JSONDecodeError as e:
            _write_message(_error_response(None, -32700, f"Parse error: {e}"))
            continue
        if msg is None:
            break
        response = server.handle(msg)
        if response is not None:
            _write_message(response)


def _build_http_app(server: "MCPServer") -> "FastAPI":
    """Build the FastAPI app for HTTP mode with MCP SSE transport."""
    if not HAS_FASTAPI:
        raise RuntimeError(
            "FastAPI is required for HTTP mode. Install with: pip install fastapi uvicorn"
        )

    app = FastAPI(title="Therefore MCP HTTP Server")

    # Bearer token auth — skip for health check
    auth_token_global = os.environ.get("THEREFORE_MCP_AUTH_TOKEN", "").strip()
    
    @app.middleware("http")
    async def check_auth(request: Request, call_next):
        # Capture IP for auditing (prefer Cloudflare header if present)
        server._current_client_ip = request.headers.get("cf-connecting-ip") or request.client.host
        
        if request.url.path == "/health":
            return await call_next(request)
        
        header = request.headers.get("authorization", "").strip()
        scheme, _, token = header.partition(" ")
        token = token.strip()
        
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(
                {"error": "Unauthorized: Missing Bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 1. Check if token is in the client_access whitelist
        if server.client_access and token in server.client_access:
            server._current_client_key = token
            return await call_next(request)
        
        # 2. Fallback to global auth token (which allows ALL tenants)
        if auth_token_global and token == auth_token_global:
            server._current_client_key = None # Global token has no restriction
            return await call_next(request)

        return JSONResponse(
            {"error": "Unauthorized: Invalid token"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Session management: session_id -> asyncio.Queue
    _sessions: Dict[str, asyncio.Queue] = {}

    @app.get("/sse")
    async def sse_endpoint(request: Request):
        """MCP SSE transport: client connects here to receive events."""
        session_id = str(uuid.uuid4())
        queue = asyncio.Queue()
        _sessions[session_id] = queue

        # Build the messages endpoint URL from the incoming request
        base_url = str(request.base_url).rstrip("/")
        messages_url = f"{base_url}/messages?session_id={session_id}"

        async def event_stream():
            # First event: tell the client where to POST messages
            yield f"event: endpoint\ndata: {messages_url}\n\n"
            try:
                while True:
                    # Check if client disconnected
                    if await request.is_disconnected():
                        break
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield f"event: message\ndata: {data}\n\n"
                    except asyncio.TimeoutError:
                        # Send keepalive comment to prevent connection timeout
                        yield ": keepalive\n\n"
            finally:
                _sessions.pop(session_id, None)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/messages")
    async def messages_endpoint(request: Request, session_id: str):
        """MCP SSE transport: client POSTs JSON-RPC messages here."""
        queue = _sessions.get(session_id)
        if queue is None:
            return JSONResponse(
                _error_response(None, -32000, "Unknown or expired session"),
                status_code=400,
            )

        body = await request.json()
        if not isinstance(body, dict):
            return JSONResponse(
                _error_response(None, -32700, "Request must be a JSON object"),
                status_code=400,
            )

        response = server.handle(body)
        if response is not None:
            await queue.put(json.dumps(response, separators=(",", ":")))

        return JSONResponse({"ok": True}, status_code=202)

    # -- Streamable HTTP transport (MCP 2025-03-26) --
    # Tracks sessions by Mcp-Session-Id header.
    # Each session has an optional asyncio.Queue for the GET SSE stream.
    _mcp_sessions: Dict[
        str, Optional[asyncio.Queue]
    ] = {}  # session_id -> queue or None

    @app.get("/mcp")
    async def streamable_http_get(request: Request):
        """MCP Streamable HTTP: long-lived SSE stream for server-initiated messages."""
        session_id = request.headers.get("mcp-session-id")
        if not session_id or session_id not in _mcp_sessions:
            return JSONResponse(
                _error_response(None, -32000, "Bad or missing Mcp-Session-Id"),
                status_code=400,
            )

        accept = request.headers.get("accept", "")
        if "text/event-stream" not in accept:
            return JSONResponse(
                _error_response(
                    None, -32000, "Accept header must include text/event-stream"
                ),
                status_code=406,
            )

        queue: asyncio.Queue = asyncio.Queue()
        _mcp_sessions[session_id] = queue

        async def event_stream():
            try:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        data = await asyncio.wait_for(queue.get(), timeout=30.0)
                        yield f"event: message\ndata: {data}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                # Clear the queue reference but keep the session alive
                if _mcp_sessions.get(session_id) is queue:
                    _mcp_sessions[session_id] = None

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "Mcp-Session-Id": session_id,
            },
        )

    @app.post("/mcp")
    async def streamable_http_endpoint(request: Request):
        """MCP Streamable HTTP transport endpoint."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                _error_response(None, -32700, "Parse error"),
                status_code=400,
            )
        if not isinstance(body, dict):
            return JSONResponse(
                _error_response(None, -32700, "Request must be a JSON object"),
                status_code=400,
            )

        method = body.get("method")
        msg_id = body.get("id")
        is_notification = msg_id is None

        # Session management
        session_id = request.headers.get("mcp-session-id")
        if method == "initialize":
            # Create a new session
            session_id = str(uuid.uuid4())
        elif session_id not in _mcp_sessions:
            return JSONResponse(
                _error_response(msg_id, -32000, "Bad or missing Mcp-Session-Id"),
                status_code=400,
            )

        response = server.handle(body)

        if method == "initialize" and response is not None:
            _mcp_sessions[session_id] = None
            return JSONResponse(
                response,
                headers={"Mcp-Session-Id": session_id},
            )

        if is_notification:
            return Response(status_code=202)

        if response is not None:
            return JSONResponse(
                response,
                headers={"Mcp-Session-Id": session_id} if session_id else {},
            )
        return Response(status_code=202)

    @app.delete("/mcp")
    async def streamable_http_delete(request: Request):
        """Terminate a Streamable HTTP session."""
        session_id = request.headers.get("mcp-session-id")
        if session_id and session_id in _mcp_sessions:
            del _mcp_sessions[session_id]
        return Response(status_code=200)

    # -- Direct JSON-RPC (non-MCP transport) --

    @app.post("/")
    async def rpc_handler(request: Request) -> JSONResponse:
        """Direct JSON-RPC over HTTP."""
        try:
            body = await request.json()
            if not isinstance(body, dict):
                return JSONResponse(
                    _error_response(None, -32700, "Request must be a JSON object"),
                    status_code=400,
                )

            response = server.handle(body)
            if response is not None:
                return JSONResponse(response)
            return JSONResponse({"jsonrpc": "2.0", "id": body.get("id")})
        except Exception as e:
            return JSONResponse(
                _error_response(None, -32603, f"Internal server error: {str(e)}"),
                status_code=500,
            )

    @app.get("/health")
    async def health_check():
        """Health check endpoint."""
        return {"status": "ok", "mode": "http-mcp"}

    return app


def run_http_mode(server: "MCPServer", host: str, port: int) -> None:
    """Run the server in HTTP-only mode using FastAPI."""
    app = _build_http_app(server)
    auth_enabled = bool(os.environ.get("THEREFORE_MCP_AUTH_TOKEN", "").strip())
    print(
        f"Starting Therefore MCP server in HTTP mode on {host}:{port}", file=sys.stderr
    )
    print(
        f"Auth: {'Bearer token' if auth_enabled else 'NONE (set THEREFORE_MCP_AUTH_TOKEN to enable)'}",
        file=sys.stderr,
    )
    print(f"Access at: http://{host}:{port}", file=sys.stderr)
    print(f"Health check: http://{host}:{port}/health", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")


def _start_http_background(server: "MCPServer", host: str, port: int) -> None:
    """Start the HTTP server in a daemon thread (for dual stdio+http mode)."""
    app = _build_http_app(server)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    uv_server = uvicorn.Server(config)
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    print(f"HTTP server started on {host}:{port}", file=sys.stderr)


def load_client_access() -> Dict[str, List[str]]:
    """Load client-to-tenant permissions from config/clients.json."""
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
        "config", "clients.json"
    )
    if not os.path.exists(config_path):
        return {}
    
    try:
        with open(config_path, "r") as f:
            data = json.load(f)
            # Normalize keys
            return {k: [normalize_tenant_key(t) for t in v] for k, v in data.items()}
    except Exception as e:
        print(f"Warning: Failed to load client access config: {e}", file=sys.stderr)
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="Therefore MCP Server")
    parser.add_argument(
        "--stdio",
        action="store_true",
        default=False,
        help="Run in stdio mode (default if no mode specified)",
    )
    parser.add_argument(
        "--http",
        type=int,
        metavar="PORT",
        help="Run in HTTP mode on specified port (e.g., --http 8000)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="HTTP host to bind to (default: 0.0.0.0)",
    )

    args = parser.parse_args()

    # Load clients and create server
    clients, default_tenant, tenant_labels, tenant_aliases = load_clients()
    client_access = load_client_access()
    server = MCPServer(
        clients, default_tenant, tenant_labels, tenant_aliases, 
        client_access=client_access
    )

    # Debug startup diagnostics (stderr only)
    any_debug = any(c.config.debug for c in clients.values())
    if any_debug:

        def _dbg(msg: str) -> None:
            print(f"[THEREFORE] {msg}", file=sys.stderr, flush=True)

        _dbg("--- startup diagnostics ---")
        for key, client in clients.items():
            label = tenant_labels.get(key, key)
            cfg = client.config
            _dbg(f"  tenant={label} base_url={cfg.base_url} auth={cfg.auth_method}")
        _dbg(f"  default_tenant={default_tenant}")

    # Determine mode and run
    use_stdio = args.stdio or args.http is None  # stdio is the default
    use_http = args.http is not None

    if any_debug:
        if use_stdio and use_http:
            _dbg(f"  transport=stdio+http (port {args.http})")
        elif use_http:
            _dbg(f"  transport=http (port {args.http})")
        else:
            _dbg("  transport=stdio")
        _dbg("--- end startup diagnostics ---")

    if use_stdio and use_http:
        # Dual mode: HTTP in background thread, stdio on main thread
        _start_http_background(server, args.host, args.http)
        run_stdio_mode(server)
    elif use_http:
        # HTTP-only mode (--http without --stdio)
        run_http_mode(server, args.host, args.http)
    else:
        # stdio-only mode (default)
        run_stdio_mode(server)


if __name__ == "__main__":
    main()
