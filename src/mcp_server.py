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
sys.path.insert(0, os.path.join(_REPO_ROOT, 'tools', 'config_generator'))
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
    return json.loads(line.decode('utf-8', errors='replace'))


def _write_message(payload: Dict[str, Any]) -> None:
    """Write a newline-delimited JSON-RPC message to stdout (MCP stdio transport)."""
    data = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    sys.stdout.buffer.write(data + b'\n')
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
    return {
        "content": [
            {"type": "text", "text": json.dumps(obj, indent=2)}
        ]
    }


def build_tools() -> List[Dict[str, Any]]:
    tools = [
        {
            "name": "resolve_category",
            "description": "Fuzzy-match a category name to category numbers. Returns ranked candidates.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                    "min_score": {"type": "number", "default": 0.35},
                    "include_non_categories": {"type": "boolean", "default": False},
                    "confirm_threshold": {"type": "number", "default": 0.75}
                }
            },
        },
        {
            "name": "list_category_fields",
            "description": "List fields for a category with key metadata (FieldNo, Caption, FieldID, etc.). Use resolve_category to find category_no.",
            "inputSchema": {
                "type": "object",
                "required": ["category_no"],
                "properties": {
                    "category_no": {"type": "integer", "description": "Category number. Use resolve_category to find this."}
                }
            },
        },
        {
            "name": "resolve_field",
            "description": "Fuzzy-match a field label to field numbers within a category. Returns ranked candidates.",
            "inputSchema": {
                "type": "object",
                "required": ["category_no", "query"],
                "properties": {
                    "category_no": {"type": "integer"},
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                    "min_score": {"type": "number", "default": 0.35},
                    "field_type_hint": {"type": "integer", "description": "Optional FieldType hint"},
                    "confirm_threshold": {"type": "number", "default": 0.75}
                }
            },
        },
        {
            "name": "get_categories_tree",
            "description": "Return the full categories tree. Use empty payload to fetch all nodes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "payload": {"type": "object", "description": "Optional request payload"}
                }
            },
        },
        {
            "name": "get_category_info",
            "description": "Get category definition and field metadata by category number. Use resolve_category to find category_no.",
            "inputSchema": {
                "type": "object",
                "required": ["category_no"],
                "properties": {
                    "category_no": {"type": "integer", "description": "Category number. Use resolve_category to find this."}
                }
            },
        },
        {
            "name": "get_document",
            "description": "Fetch a document by document number.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"},
                    "include_index_data": {"type": "boolean", "default": True},
                    "include_streams_info": {"type": "boolean", "default": False},
                    "include_streams_data": {"type": "boolean", "default": False},
                    "include_checkout_status": {"type": "boolean", "default": False},
                    "include_access_mask": {"type": "boolean", "default": False}
                }
            },
        },
        {
            "name": "get_document_index_data",
            "description": "Fetch index data for a document.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"}
                }
            },
        },
        {
            "name": "get_web_api_server_version",
            "description": "Get WebAPI server version info.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            },
        },
        {
            "name": "get_connection_token",
            "description": "Get a connection token for the current user.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            },
        },
        {
            "name": "get_domain_info",
            "description": "Get domain info for the current tenant.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            },
        },
        {
            "name": "get_client_discovery_info",
            "description": "Get discovery info for clients (capabilities/endpoints).",
            "inputSchema": {
                "type": "object",
                "properties": {}
            },
        },
        {
            "name": "get_system_customer_id",
            "description": "Get system customer/client id for the tenant.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            },
        },
        {
            "name": "get_connected_user",
            "description": "Get the connected user. If create=true, create if missing.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "create": {"type": "boolean", "default": False}
                }
            },
        },
        {
            "name": "get_permission_constants",
            "description": "Get permission constants.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            },
        },
        {
            "name": "get_role_permission_constants",
            "description": "Get role permission constants.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            },
        },
        {
            "name": "get_document_properties",
            "description": "Get document properties by document number.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"},
                    "version_no": {"type": "integer", "default": 0},
                    "is_doc_title_needed": {"type": "boolean", "default": False}
                }
            },
        },
        {
            "name": "get_document_history",
            "description": "Get document history by document number.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"}
                }
            },
        },
        {
            "name": "get_document_checkout_status",
            "description": "Get document checkout status by document number.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"}
                }
            },
        },
        {
            "name": "get_objects_list",
            "description": "Get objects list. Provide LoadItemsList as in the WebAPI.",
            "inputSchema": {
                "type": "object",
                "required": ["load_items_list"],
                "properties": {
                    "load_items_list": {"type": "array", "items": {"type": "object"}}
                }
            },
        },
        {
            "name": "get_objects",
            "description": "Get objects using the GetObjects endpoint (Flags + Type).",
            "inputSchema": {
                "type": "object",
                "required": ["flags", "obj_type"],
                "properties": {
                    "flags": {"type": "integer"},
                    "obj_type": {"type": "integer"}
                }
            },
        },
        {
            "name": "execute_users_query",
            "description": "Query users by name or other text. Use an empty string to return all users.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Search text to match against user names, display names, and email. Use empty string to return all users."},
                    "domain_names": {"type": "array", "items": {"type": "string"}},
                    "flags": {"type": "integer", "default": 5}
                }
            },
        },
        {
            "name": "get_users_from_group",
            "description": "Get users from a group by name or id.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "integer"},
                    "group_name": {"type": "string"},
                    "domain_name": {"type": "string"}
                }
            },
        },
        {
            "name": "get_user_details",
            "description": "Get details for a user or group id.",
            "inputSchema": {
                "type": "object",
                "required": ["user_or_group_id"],
                "properties": {
                    "user_or_group_id": {"type": "integer"}
                }
            },
        },
        {
            "name": "get_keywords_by_field_no",
            "description": "List keywords for a keyword field (dictionary) by field number. Use resolve_field to find field_no.",
            "inputSchema": {
                "type": "object",
                "required": ["field_no"],
                "properties": {
                    "field_no": {"type": "integer", "description": "Field number. Use resolve_field to find this."},
                    "category_no": {"type": "integer", "description": "Category number. Use resolve_category to find this."},
                    "case_definition_no": {"type": "integer"},
                    "dependent_field_filter_value": {"type": "string"},
                    "show_deactivated_keywords": {"type": "boolean"},
                    "skip_loading_keyword_nos": {"type": "boolean"},
                    "max_rows": {"type": "integer"},
                    "index_data_items": {"type": "array", "items": {"type": "object"}}
                }
            },
        },
        {
            "name": "get_keywords_by_key_dic",
            "description": "List keywords from a keyword dictionary by dictionary number.",
            "inputSchema": {
                "type": "object",
                "required": ["key_dic_no"],
                "properties": {
                    "key_dic_no": {"type": "integer"},
                    "filter_value": {"type": "string"},
                    "max_values": {"type": "integer"},
                    "include_deactivated_keywords": {"type": "boolean"}
                }
            },
        },
        {
            "name": "validate_keywords",
            "description": "Validate keywords for a keyword field; returns invalid keywords. Use resolve_field to find field_no.",
            "inputSchema": {
                "type": "object",
                "required": ["field_no", "keywords"],
                "properties": {
                    "field_no": {"type": "integer", "description": "Field number. Use resolve_field to find this."},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "is_filter_mode": {"type": "boolean"}
                }
            },
        },
        {
            "name": "get_keywords_by_dictionary_name",
            "description": "Resolve a keyword dictionary by name (fuzzy) and list its keywords.",
            "inputSchema": {
                "type": "object",
                "required": ["dictionary_name"],
                "properties": {
                    "dictionary_name": {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                    "min_score": {"type": "number", "default": 0.35},
                    "confirm_threshold": {"type": "number", "default": 0.75},
                    "filter_value": {"type": "string"},
                    "max_values": {"type": "integer"},
                    "include_deactivated_keywords": {"type": "boolean"}
                }
            },
        },
        {
            "name": "add_dictionary_keyword",
            "description": "Add a keyword to a dictionary (id or name).",
            "inputSchema": {
                "type": "object",
                "required": ["keyword_name"],
                "properties": {
                    "keyword_name": {"type": "string"},
                    "dictionary_no": {"type": "integer"},
                    "dictionary_name": {"type": "string"},
                    "dictionary_type_no": {"type": "integer"},
                    "is_keyword_deactivated": {"type": "boolean"},
                    "check_existing": {"type": "boolean", "default": True},
                    "ignore_if_exists": {"type": "boolean", "default": True},
                    "include_deactivated_keywords": {"type": "boolean", "default": True}
                }
            },
        },
        {
            "name": "update_dictionary_keyword",
            "description": "Update (rename/deactivate) a keyword in a dictionary.",
            "inputSchema": {
                "type": "object",
                "required": ["new_keyword_name"],
                "properties": {
                    "dictionary_no": {"type": "integer"},
                    "dictionary_name": {"type": "string"},
                    "dictionary_type_no": {"type": "integer"},
                    "keyword_id": {"type": "integer"},
                    "keyword_name": {"type": "string", "description": "Existing keyword to rename"},
                    "new_keyword_name": {"type": "string"},
                    "is_keyword_deactivated": {"type": "boolean"},
                    "check_existing": {"type": "boolean", "default": True},
                    "ignore_if_exists": {"type": "boolean", "default": True},
                    "include_deactivated_keywords": {"type": "boolean", "default": True}
                }
            },
        },
        {
            "name": "delete_dictionary_keyword",
            "description": "Delete a keyword from a dictionary.",
            "inputSchema": {
                "type": "object",
                "required": [],
                "properties": {
                    "dictionary_no": {"type": "integer"},
                    "dictionary_name": {"type": "string"},
                    "dictionary_type_no": {"type": "integer"},
                    "keyword_id": {"type": "integer"},
                    "keyword_name": {"type": "string"},
                    "include_deactivated_keywords": {"type": "boolean", "default": True}
                }
            },
        },
        {
            "name": "deactivate_dictionary_keyword",
            "description": "Deactivate a keyword in a dictionary (alias for update_dictionary_keyword with IsKeywordDeactivated=true).",
            "inputSchema": {
                "type": "object",
                "required": [],
                "properties": {
                    "dictionary_no": {"type": "integer"},
                    "dictionary_name": {"type": "string"},
                    "dictionary_type_no": {"type": "integer"},
                    "keyword_id": {"type": "integer"},
                    "keyword_name": {"type": "string"},
                    "include_deactivated_keywords": {"type": "boolean", "default": True}
                }
            },
        },
        {
            "name": "execute_workflow_query_for_all",
            "description": "Execute a workflow query across all processes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_flags": {"type": "integer", "default": 0},
                    "max_rows": {"type": "integer", "description": "Optional override; defaults to THEREFORE_WORKFLOW_MAX_ROWS or 1000."},
                    "include_instance_details": {"type": "boolean", "default": False},
                    "instance_detail_mode": {"type": "string", "default": "summary", "description": "none|summary|full. summary includes assignment/current task/due dates; full attaches WorkflowInstance + LinkedDocuments."},
                    "max_instance_workers": {"type": "integer", "default": 4},
                    "is_access_mask_needed": {"type": "boolean", "default": False},
                    "load_history": {"type": "boolean", "default": False},
                    "debug": {"type": "boolean", "default": False},
                    "debug_log_path": {"type": "string", "description": "Optional path for debug JSONL logs."},
                    "debug_progress_every": {"type": "integer", "default": 500}
                }
            },
        },
        {
            "name": "execute_workflow_query_for_process",
            "description": "Execute a workflow query for a specific process.",
            "inputSchema": {
                "type": "object",
                "required": ["process_no"],
                "properties": {
                    "process_no": {"type": "integer"},
                    "workflow_flags": {"type": "integer", "default": 0},
                    "max_rows": {"type": "integer", "description": "Optional override; defaults to THEREFORE_WORKFLOW_MAX_ROWS or 1000."},
                    "include_instance_details": {"type": "boolean", "default": False},
                    "instance_detail_mode": {"type": "string", "default": "summary", "description": "none|summary|full. summary includes assignment/current task/due dates; full attaches WorkflowInstance + LinkedDocuments."},
                    "max_instance_workers": {"type": "integer", "default": 4},
                    "is_access_mask_needed": {"type": "boolean", "default": False},
                    "load_history": {"type": "boolean", "default": False},
                    "debug": {"type": "boolean", "default": False},
                    "debug_log_path": {"type": "string", "description": "Optional path for debug JSONL logs."},
                    "debug_progress_every": {"type": "integer", "default": 500}
                }
            },
        },
        {
            "name": "get_linked_workflows_for_doc",
            "description": "Get linked workflows for a document.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"},
                    "wf_doc_link_type": {"type": "integer", "default": 0}
                }
            },
        },
        {
            "name": "get_workflow_history",
            "description": "Get workflow history for an instance.",
            "inputSchema": {
                "type": "object",
                "required": ["instance_no"],
                "properties": {
                    "instance_no": {"type": "integer"},
                    "block_size": {"type": "integer", "default": 1000},
                    "include_routing_info": {"type": "boolean", "default": True},
                    "max_creation_date": {"type": "string", "description": "Optional /Date(...) format value"},
                    "seq_pos": {"type": "integer", "default": 0}
                }
            },
        },
        {
            "name": "get_workflow_instance",
            "description": "Get workflow instance by instance/token.",
            "inputSchema": {
                "type": "object",
                "required": ["instance_no"],
                "properties": {
                    "instance_no": {"type": "integer"},
                    "token_no": {"type": "integer", "default": 0},
                    "is_access_mask_needed": {"type": "boolean", "default": False},
                    "load_history": {"type": "boolean", "default": False}
                }
            },
        },
        {
            "name": "get_workflow_process",
            "description": "Get workflow process definition.",
            "inputSchema": {
                "type": "object",
                "required": ["process_no"],
                "properties": {
                    "process_no": {"type": "integer"},
                    "version_no": {"type": "integer", "default": 0},
                    "load_tasks": {"type": "boolean", "default": True},
                    "is_access_mask_needed": {"type": "boolean", "default": False}
                }
            },
        },
        {
            "name": "get_workflow_task_settings",
            "description": "Get workflow task settings for a task in a process.",
            "inputSchema": {
                "type": "object",
                "required": ["task_no", "process_no"],
                "properties": {
                    "task_no": {"type": "integer"},
                    "process_no": {"type": "integer"},
                    "version_no": {"type": "integer", "default": 0},
                    "setting_names": {"type": "array", "items": {"type": "string"}}
                }
            },
        },
        {
            "name": "get_my_workflow_tasks",
            "description": "List workflow tasks for the connected user. Defaults to running instances. Uses GetWorkflowInstance for assignment/state.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_flags": {"type": ["string", "integer"], "default": "RunningInstances"},
                    "task_filter": {"type": "string", "description": "Optional filter: running|overdue|all|finished|error|default."},
                    "max_rows": {"type": "integer", "description": "Optional override; defaults to THEREFORE_WORKFLOW_MAX_ROWS or 1000."},
                    "filter_to_user": {"type": "boolean", "default": True},
                    "include_unfiltered": {"type": "boolean", "default": False},
                    "include_overdue_summary": {"type": "boolean", "default": True},
                    "assignee_values": {"type": "array", "items": {"type": "string"}, "description": "Optional extra assignee/group names to include when filtering."},
                    "resolve_group_membership": {"type": "boolean", "default": True},
                    "user_query": {"type": "string", "description": "Optional user search string (e.g., full name)."},
                    "user_query_flags": {"type": "integer", "default": 5},
                    "instance_detail_mode": {"type": "string", "default": "summary", "description": "none|summary|full. summary includes assignment/current task/due dates; full attaches WorkflowInstance + LinkedDocuments."},
                    "max_instance_workers": {"type": "integer", "default": 4},
                    "is_access_mask_needed": {"type": "boolean", "default": False},
                    "load_history": {"type": "boolean", "default": False},
                    "debug": {"type": "boolean", "default": False},
                    "debug_log_path": {"type": "string", "description": "Optional path for debug JSONL logs."},
                    "debug_progress_every": {"type": "integer", "default": 500},
                    "two_phase": {"type": "boolean", "default": False},
                    "fetch_details": {"type": "boolean", "default": False}
                }
            },
        },
        {
            "name": "get_my_workflow_instances",
            "description": "List workflow instances for the connected user (assignment/state from GetWorkflowInstance).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_flags": {"type": ["string", "integer"], "default": "RunningInstances"},
                    "task_filter": {"type": "string", "description": "Optional filter: running|overdue|all|finished|error|default."},
                    "max_rows": {"type": "integer", "description": "Optional override; defaults to THEREFORE_WORKFLOW_MAX_ROWS or 1000."},
                    "include_overdue_summary": {"type": "boolean", "default": True},
                    "assignee_values": {"type": "array", "items": {"type": "string"}, "description": "Optional extra assignee/group names to include when filtering."},
                    "resolve_group_membership": {"type": "boolean", "default": True},
                    "instance_detail_mode": {"type": "string", "default": "summary", "description": "none|summary|full. summary includes assignment/current task/due dates; full attaches WorkflowInstance + LinkedDocuments."},
                    "max_instance_workers": {"type": "integer", "default": 4},
                    "is_access_mask_needed": {"type": "boolean", "default": False},
                    "load_history": {"type": "boolean", "default": False},
                    "debug": {"type": "boolean", "default": False},
                    "debug_log_path": {"type": "string", "description": "Optional path for debug JSONL logs."},
                    "debug_progress_every": {"type": "integer", "default": 500},
                    "two_phase": {"type": "boolean", "default": False},
                    "fetch_details": {"type": "boolean", "default": False}
                }
            },
        },
        {
            "name": "get_all_workflow_instances",
            "description": "List workflow instances for all assignees (optionally enrich with GetWorkflowInstance).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workflow_flags": {"type": ["string", "integer"], "default": "RunningInstances"},
                    "task_filter": {"type": "string", "description": "Optional filter: running|overdue|all|finished|error|default."},
                    "max_rows": {"type": "integer", "description": "Optional override; defaults to THEREFORE_WORKFLOW_MAX_ROWS or 1000."},
                    "include_overdue_summary": {"type": "boolean", "default": True},
                    "instance_detail_mode": {"type": "string", "default": "summary", "description": "none|summary|full. summary includes assignment/current task/due dates; full attaches WorkflowInstance + LinkedDocuments."},
                    "max_instance_workers": {"type": "integer", "default": 4},
                    "is_access_mask_needed": {"type": "boolean", "default": False},
                    "load_history": {"type": "boolean", "default": False},
                    "debug": {"type": "boolean", "default": False},
                    "debug_log_path": {"type": "string", "description": "Optional path for debug JSONL logs."},
                    "debug_progress_every": {"type": "integer", "default": 500},
                    "two_phase": {"type": "boolean", "default": False},
                    "fetch_details": {"type": "boolean", "default": False}
                }
            },
        },
        {
            "name": "get_workflow_instances_for_user",
            "description": "List workflow instances for a resolved user (assignment/state from GetWorkflowInstance).",
            "inputSchema": {
                "type": "object",
                "required": ["user_query"],
                "properties": {
                    "user_query": {"type": "string", "description": "User search string (e.g., full name or username)."},
                    "user_query_flags": {"type": "integer", "default": 5},
                    "workflow_flags": {"type": ["string", "integer"], "default": "RunningInstances"},
                    "task_filter": {"type": "string", "description": "Optional filter: running|overdue|all|finished|error|default."},
                    "max_rows": {"type": "integer", "description": "Optional override; defaults to THEREFORE_WORKFLOW_MAX_ROWS or 1000."},
                    "include_overdue_summary": {"type": "boolean", "default": True},
                    "assignee_values": {"type": "array", "items": {"type": "string"}, "description": "Optional extra assignee/group names to include when filtering."},
                    "resolve_group_membership": {"type": "boolean", "default": True},
                    "instance_detail_mode": {"type": "string", "default": "summary", "description": "none|summary|full. summary includes assignment/current task/due dates; full attaches WorkflowInstance + LinkedDocuments."},
                    "max_instance_workers": {"type": "integer", "default": 4},
                    "is_access_mask_needed": {"type": "boolean", "default": False},
                    "load_history": {"type": "boolean", "default": False},
                    "debug": {"type": "boolean", "default": False},
                    "debug_log_path": {"type": "string", "description": "Optional path for debug JSONL logs."},
                    "debug_progress_every": {"type": "integer", "default": 500},
                    "two_phase": {"type": "boolean", "default": False},
                    "fetch_details": {"type": "boolean", "default": False}
                }
            },
        },
        {
            "name": "execute_single_query",
            "description": "Execute a single query. Use resolve_category to find CategoryNo and resolve_field to find field numbers before building the query. If the query contains multiple category numbers, automatically runs an async multi-query and returns merged results.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "object",
                        "description": "Query definition object. Use resolve_category to find CategoryNo and resolve_field to find field numbers.",
                        "required": ["CategoryNo"],
                        "properties": {
                            "CategoryNo": {"type": "integer", "description": "Category number to query. Use resolve_category to find this."},
                            "Conditions": {
                                "type": "array",
                                "description": "Filter conditions. Each condition uses FieldNoOrName (field number or name) and a Condition string. Operators: '= value', '>= value', '<= value', '<> value', 'LIKE value' (% wildcard), 'BETWEEN val1 AND val2'. Date format: YYYY-MM-DDThh:mm:ss. Use resolve_field to find field numbers.",
                                "items": {
                                    "type": "object",
                                    "required": ["FieldNoOrName", "Condition"],
                                    "properties": {
                                        "FieldNoOrName": {"type": "string", "description": "Field number (as string) or field name."},
                                        "Condition": {"type": "string", "description": "Condition expression, e.g. '= John', '>= 2024-01-01T00:00:00', 'LIKE %invoice%'."},
                                        "TimeZone": {"type": "integer", "description": "0 = UTC (default), 1 = ServerLocal.", "default": 0}
                                    }
                                }
                            },
                            "SelectedFieldsNoOrNames": {
                                "type": "array",
                                "description": "Field numbers or names to return as columns. If omitted, returns default fields.",
                                "items": {"type": "string"}
                            },
                            "OrderByFieldsNoOrNames": {
                                "type": "array",
                                "description": "Field numbers or names to sort results by.",
                                "items": {"type": "string"}
                            },
                            "GroupByFieldsNoOrNames": {
                                "type": "array",
                                "description": "Field numbers or names to group results by.",
                                "items": {"type": "string"}
                            },
                            "MaxRows": {"type": "integer", "description": "Maximum number of rows to return.", "default": 2147483647},
                            "Mode": {"type": "integer", "description": "Query mode: 0 = NormalQuery (default), 1 = FileQuery, 4 = WorkflowQuery, 5 = CaseQuery.", "default": 0},
                            "CaseDefinitionNo": {"type": "integer", "description": "Case definition number for case queries. Omit or 0 if not applicable."},
                            "QueryNo": {"type": "integer", "description": "Saved query number. If provided, executes a saved query instead of building one from Conditions."},
                            "IsPersonalQuery": {"type": "boolean", "description": "Whether QueryNo refers to a personal (true) or public (false) saved query."}
                        }
                    },
                    "full_text": {"type": "string"}
                }
            },
        },
        {
            "name": "execute_async_single_query",
            "description": "Execute an async single query with batching. Use resolve_category to find CategoryNo and resolve_field to find field numbers before building the query. If auto_fetch_all=true, fetches all rows and releases the query.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "object",
                        "description": "Query definition object. Use resolve_category to find CategoryNo and resolve_field to find field numbers.",
                        "required": ["CategoryNo"],
                        "properties": {
                            "CategoryNo": {"type": "integer", "description": "Category number to query. Use resolve_category to find this."},
                            "Conditions": {
                                "type": "array",
                                "description": "Filter conditions. Each condition uses FieldNoOrName (field number or name) and a Condition string. Operators: '= value', '>= value', '<= value', '<> value', 'LIKE value' (% wildcard), 'BETWEEN val1 AND val2'. Date format: YYYY-MM-DDThh:mm:ss. Use resolve_field to find field numbers.",
                                "items": {
                                    "type": "object",
                                    "required": ["FieldNoOrName", "Condition"],
                                    "properties": {
                                        "FieldNoOrName": {"type": "string", "description": "Field number (as string) or field name."},
                                        "Condition": {"type": "string", "description": "Condition expression, e.g. '= John', '>= 2024-01-01T00:00:00', 'LIKE %invoice%'."},
                                        "TimeZone": {"type": "integer", "description": "0 = UTC (default), 1 = ServerLocal.", "default": 0}
                                    }
                                }
                            },
                            "SelectedFieldsNoOrNames": {
                                "type": "array",
                                "description": "Field numbers or names to return as columns. If omitted, returns default fields.",
                                "items": {"type": "string"}
                            },
                            "OrderByFieldsNoOrNames": {
                                "type": "array",
                                "description": "Field numbers or names to sort results by.",
                                "items": {"type": "string"}
                            },
                            "GroupByFieldsNoOrNames": {
                                "type": "array",
                                "description": "Field numbers or names to group results by.",
                                "items": {"type": "string"}
                            },
                            "MaxRows": {"type": "integer", "description": "Maximum number of rows to return.", "default": 2147483647},
                            "Mode": {"type": "integer", "description": "Query mode: 0 = NormalQuery (default), 1 = FileQuery, 4 = WorkflowQuery, 5 = CaseQuery.", "default": 0},
                            "CaseDefinitionNo": {"type": "integer", "description": "Case definition number for case queries. Omit or 0 if not applicable."},
                            "QueryNo": {"type": "integer", "description": "Saved query number. If provided, executes a saved query instead of building one from Conditions."},
                            "IsPersonalQuery": {"type": "boolean", "description": "Whether QueryNo refers to a personal (true) or public (false) saved query."}
                        }
                    },
                    "full_text": {"type": "string"},
                    "row_block_size": {"type": "integer", "default": 1000},
                    "max_rows": {"type": "integer", "default": 2147483647},
                    "auto_fetch_all": {"type": "boolean", "default": True}
                }
            },
        },
        {
            "name": "get_next_single_query_rows",
            "description": "Fetch next batch of rows for an async single query (GetNextSingleQueryRows).",
            "inputSchema": {
                "type": "object",
                "required": ["query_id", "row_block_size"],
                "properties": {
                    "query_id": {"type": "integer"},
                    "row_block_size": {"type": "integer"}
                }
            },
        },
        {
            "name": "release_single_query",
            "description": "Release server resources for an async single query.",
            "inputSchema": {
                "type": "object",
                "required": ["query_id"],
                "properties": {
                    "query_id": {"type": "integer"}
                }
            },
        },
        {
            "name": "execute_full_text_query",
            "description": "Execute a full-text search query. Use resolve_category to find category numbers if filtering by categories.",
            "inputSchema": {
                "type": "object",
                "required": ["search"],
                "properties": {
                    "search": {"type": "string"},
                    "categories": {"type": "array", "items": {"type": "integer"}, "description": "Optional category numbers to filter results. Use resolve_category to find these."},
                    "max_rows": {"type": "integer", "default": 100},
                    "include_index_data": {"type": "boolean", "default": False},
                    "case_no": {"type": "integer", "default": 0}
                }
            },
        },
        {
            "name": "call_endpoint",
            "description": "Call an arbitrary WebAPI endpoint (POST) with a JSON payload.",
            "inputSchema": {
                "type": "object",
                "required": ["endpoint"],
                "properties": {
                    "endpoint": {"type": "string", "description": "Endpoint name or path (e.g., GetDomainInfo)."},
                    "payload": {"type": "object", "description": "JSON payload to send (optional)."}
                }
            },
        },
        {
            "name": "execute_statistics_query",
            "description": "Execute a statistics query (e.g., documents created by category, workflow instance counts).",
            "inputSchema": {
                "type": "object",
                "required": ["query_type"],
                "properties": {
                    "query_type": {"type": ["string", "integer"], "description": "Statistics query type (name or numeric)."},
                    "restrict_to_obj_no": {"type": "integer", "description": "Optional object/category/workflow restriction."},
                    "restrict_to_user": {"type": "boolean", "description": "Optional restriction to current user."}
                }
            },
        },
        {
            "name": "execute_async_multi_query",
            "description": "Execute an async multi-query with batching. Use resolve_category to find CategoryNo and resolve_field to find field numbers before building queries. If auto_fetch_all=true, fetches all rows and releases the query.",
            "inputSchema": {
                "type": "object",
                "required": ["queries"],
                "properties": {
                    "queries": {
                        "type": "array",
                        "description": "Array of query definition objects. Each query has the same structure as execute_single_query.",
                        "items": {
                            "type": "object",
                            "required": ["CategoryNo"],
                            "properties": {
                                "CategoryNo": {"type": "integer", "description": "Category number to query. Use resolve_category to find this."},
                                "Conditions": {
                                    "type": "array",
                                    "description": "Filter conditions. Each condition uses FieldNoOrName (field number or name) and a Condition string. Operators: '= value', '>= value', '<= value', '<> value', 'LIKE value' (% wildcard), 'BETWEEN val1 AND val2'. Date format: YYYY-MM-DDThh:mm:ss. Use resolve_field to find field numbers.",
                                    "items": {
                                        "type": "object",
                                        "required": ["FieldNoOrName", "Condition"],
                                        "properties": {
                                            "FieldNoOrName": {"type": "string", "description": "Field number (as string) or field name."},
                                            "Condition": {"type": "string", "description": "Condition expression, e.g. '= John', '>= 2024-01-01T00:00:00', 'LIKE %invoice%'."},
                                            "TimeZone": {"type": "integer", "description": "0 = UTC (default), 1 = ServerLocal.", "default": 0}
                                        }
                                    }
                                },
                                "SelectedFieldsNoOrNames": {
                                    "type": "array",
                                    "description": "Field numbers or names to return as columns. If omitted, returns default fields.",
                                    "items": {"type": "string"}
                                },
                                "OrderByFieldsNoOrNames": {
                                    "type": "array",
                                    "description": "Field numbers or names to sort results by.",
                                    "items": {"type": "string"}
                                },
                                "GroupByFieldsNoOrNames": {
                                    "type": "array",
                                    "description": "Field numbers or names to group results by.",
                                    "items": {"type": "string"}
                                },
                                "MaxRows": {"type": "integer", "description": "Maximum number of rows to return.", "default": 2147483647},
                                "Mode": {"type": "integer", "description": "Query mode: 0 = NormalQuery (default), 1 = FileQuery, 4 = WorkflowQuery, 5 = CaseQuery.", "default": 0},
                                "CaseDefinitionNo": {"type": "integer", "description": "Case definition number for case queries. Omit or 0 if not applicable."},
                                "QueryNo": {"type": "integer", "description": "Saved query number. If provided, executes a saved query instead of building one from Conditions."},
                                "IsPersonalQuery": {"type": "boolean", "description": "Whether QueryNo refers to a personal (true) or public (false) saved query."}
                            }
                        }
                    },
                    "full_text": {"type": "string"},
                    "row_block_size": {"type": "integer", "default": 1000},
                    "max_rows": {"type": "integer", "default": 2147483647},
                    "auto_fetch_all": {"type": "boolean", "default": True}
                }
            },
        },
        {
            "name": "get_next_multi_query_rows",
            "description": "Fetch next batch of rows for an async multi-query (GetNextMultiQueryRows).",
            "inputSchema": {
                "type": "object",
                "required": ["query_id", "row_block_size"],
                "properties": {
                    "query_id": {"type": "integer"},
                    "row_block_size": {"type": "integer"}
                }
            },
        },
        {
            "name": "release_multi_query",
            "description": "Release server resources for an async multi-query.",
            "inputSchema": {
                "type": "object",
                "required": ["query_id"],
                "properties": {
                    "query_id": {"type": "integer"}
                }
            },
        },
        {
            "name": "create_document",
            "description": "Create a document using the web-client flow (GetCategoryInfo -> PreprocessIndexData -> EvaluateConditionalProperties -> CreateDocument). Use resolve_category to find category_no and resolve_field to build index_data_items. Default auto-append mode is 0.",
            "inputSchema": {
                "type": "object",
                "required": ["category_no"],
                "properties": {
                    "category_no": {"type": "integer", "description": "Category number for the document. Use resolve_category to find this."},
                    "check_in_comments": {"type": "string"},
                    "with_auto_append_mode": {"type": "integer", "default": 0},
                    "do_fill_dependent_fields": {"type": "boolean", "default": True},
                    "streams": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["file_name"],
                            "properties": {
                                "file_name": {"type": "string"},
                                "file_data_base64": {"type": "string"},
                                "file_data_text": {"type": "string"}
                            }
                        }
                    },
                    "content_text": {"type": "string", "description": "If provided and streams is empty, create a single text file."},
                    "content_filename": {"type": "string", "default": "document.txt"},
                    "index_data_items": {"type": "array", "items": {"type": "object"}},
                    "run_webclient_flow": {"type": "boolean", "default": True}
                }
            },
        },
        {
            "name": "update_document_index_data",
            "description": "Update index fields for a document (uses SaveDocumentIndexData). Use resolve_field to find field numbers before building updates.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"},
                    "updates": {
                        "type": "array",
                        "description": "List of field updates by field number. Use resolve_field to find field_no.",
                        "items": {
                            "type": "object",
                            "required": ["value"],
                            "anyOf": [
                                {"required": ["field_no"]},
                                {"required": ["field_name"]}
                            ],
                            "properties": {
                                "field_no": {"type": "integer", "description": "Field number. Use resolve_field to find this."},
                                "field_name": {"type": "string", "description": "Optional field label/name to resolve if field_no not provided."},
                                "value": {}
                            }
                        }
                    },
                    "index_data_items": {
                        "type": "array",
                        "description": "Optional raw IndexDataItems; if provided, updates is ignored.",
                        "items": {"type": "object"}
                    },
                    "check_in_comments": {"type": "string"},
                    "do_fill_dependent_fields": {"type": "boolean", "default": True}
                }
            },
        },
        {
            "name": "update_document",
            "description": "Update a document's streams and/or index data (uses UpdateDocument). Use resolve_field to find field numbers for updates.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"},
                    "updates": {
                        "type": "array",
                        "description": "Optional index field updates by field number. Use resolve_field to find field_no.",
                        "items": {
                            "type": "object",
                            "required": ["value"],
                            "anyOf": [
                                {"required": ["field_no"]},
                                {"required": ["field_name"]}
                            ],
                            "properties": {
                                "field_no": {"type": "integer", "description": "Field number. Use resolve_field to find this."},
                                "field_name": {"type": "string", "description": "Optional field label/name to resolve if field_no not provided."},
                                "value": {}
                            }
                        }
                    },
                    "index_data_items": {
                        "type": "array",
                        "description": "Optional raw IndexDataItems; if provided, updates is ignored.",
                        "items": {"type": "object"}
                    },
                    "streams": {
                        "type": "array",
                        "description": "Streams to add or update.",
                        "items": {
                            "type": "object",
                            "required": ["file_name"],
                            "properties": {
                                "file_name": {"type": "string"},
                                "file_data_base64": {"type": "string"},
                                "file_data_text": {"type": "string"},
                                "stream_no": {"type": "integer"},
                                "new_stream_insert_mode": {"type": "integer", "default": 0}
                            }
                        }
                    },
                    "stream_nos_to_delete": {
                        "type": "array",
                        "items": {"type": "integer"}
                    },
                    "streams_to_rename": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["stream_no", "file_name"],
                            "properties": {
                                "stream_no": {"type": "integer"},
                                "file_name": {"type": "string"}
                            }
                        }
                    },
                    "check_in_comments": {"type": "string"},
                    "do_fill_dependent_fields": {"type": "boolean", "default": True}
                }
            },
        },
        {
            "name": "add_streams_to_document",
            "description": "Add new streams to a document (uses AddStreamsToDocument).",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no", "streams"],
                "properties": {
                    "doc_no": {"type": "integer"},
                    "streams": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["file_name"],
                            "properties": {
                                "file_name": {"type": "string"},
                                "file_data_base64": {"type": "string"},
                                "file_data_text": {"type": "string"},
                                "stream_no": {"type": "integer"},
                                "new_stream_insert_mode": {"type": "integer", "default": 0}
                            }
                        }
                    },
                    "conversion_options": {
                        "type": "object",
                        "description": "Optional conversion settings (e.g., ConvertTo)."
                    },
                    "check_in_comments": {"type": "string"}
                }
            },
        },
        {
            "name": "delete_document",
            "description": "Delete a document by document number.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"}
                }
            },
        },
        {
            "name": "check_out_document",
            "description": "Check out (lock) a document for editing. Returns checkout status and lock information.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer", "description": "Document number to check out."},
                    "version_no": {"type": "integer", "default": 0, "description": "Version number (0 for current)."}
                }
            },
        },
        {
            "name": "check_in_document",
            "description": "Check in (release lock on) a document after editing. Use after check_out_document.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer", "description": "Document number to check in."},
                    "check_in_comments": {"type": "string", "description": "Optional comments describing changes."},
                    "version_no": {"type": "integer", "default": 0, "description": "Version number (0 for current)."}
                }
            },
        },
        {
            "name": "undo_check_out_document",
            "description": "Cancel document checkout without saving changes. Reverts to state before checkout.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer", "description": "Document number to undo checkout."},
                    "version_no": {"type": "integer", "default": 0, "description": "Version number (0 for current)."}
                }
            },
        },
        {
            "name": "add_comment",
            "description": "Add a comment to a document.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no", "comment_text"],
                "properties": {
                    "doc_no": {"type": "integer", "description": "Document number."},
                    "comment_text": {"type": "string", "description": "The comment text to add."},
                    "version_no": {"type": "integer", "default": 0, "description": "Version number (0 for current)."}
                }
            },
        },
        {
            "name": "get_comments",
            "description": "Get all comments for a document.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer", "description": "Document number."},
                    "version_no": {"type": "integer", "default": 0, "description": "Version number (0 for current)."}
                }
            },
        },
        {
            "name": "complete_task",
            "description": "Complete a workflow task. Use after claiming a task with claim_workflow_instance.",
            "inputSchema": {
                "type": "object",
                "required": ["workflow_instance_token", "task_no"],
                "properties": {
                    "workflow_instance_token": {"type": "string", "description": "Workflow instance token from get_workflow_instance."},
                    "task_no": {"type": "integer", "description": "Task number to complete."},
                    "user_decision": {"type": "string", "description": "Optional user decision/outcome (e.g., 'Approve', 'Reject')."},
                    "index_data_items": {"type": "array", "items": {"type": "object"}, "description": "Optional index data updates."}
                }
            },
        },
        {
            "name": "claim_workflow_instance",
            "description": "Claim a workflow task for the current user. Use before completing tasks.",
            "inputSchema": {
                "type": "object",
                "required": ["workflow_instance_token"],
                "properties": {
                    "workflow_instance_token": {"type": "string", "description": "Workflow instance token from get_workflow_instance."},
                    "task_no": {"type": "integer", "description": "Optional task number to claim."}
                }
            },
        },
        {
            "name": "disclaim_workflow_instance",
            "description": "Unclaim (release) a workflow task. Allows others to claim it.",
            "inputSchema": {
                "type": "object",
                "required": ["workflow_instance_token"],
                "properties": {
                    "workflow_instance_token": {"type": "string", "description": "Workflow instance token from get_workflow_instance."},
                    "task_no": {"type": "integer", "description": "Optional task number to disclaim."}
                }
            },
        },
        {
            "name": "delegate_workflow_instance",
            "description": "Delegate a workflow task to another user.",
            "inputSchema": {
                "type": "object",
                "required": ["workflow_instance_token", "user_id"],
                "properties": {
                    "workflow_instance_token": {"type": "string", "description": "Workflow instance token from get_workflow_instance."},
                    "user_id": {"type": "integer", "description": "User ID to delegate to. Use execute_users_query to find user IDs."},
                    "task_no": {"type": "integer", "description": "Optional task number to delegate."}
                }
            },
        },
        {
            "name": "create_case",
            "description": "Create a new case in a case definition.",
            "inputSchema": {
                "type": "object",
                "required": ["case_definition_no"],
                "properties": {
                    "case_definition_no": {"type": "integer", "description": "Case definition number."},
                    "index_data_items": {"type": "array", "items": {"type": "object"}, "description": "Optional case index data."}
                }
            },
        },
        {
            "name": "get_case",
            "description": "Get case details by case number.",
            "inputSchema": {
                "type": "object",
                "required": ["case_no"],
                "properties": {
                    "case_no": {"type": "integer", "description": "Case number."}
                }
            },
        },
        {
            "name": "get_case_documents",
            "description": "List documents in a case.",
            "inputSchema": {
                "type": "object",
                "required": ["case_no"],
                "properties": {
                    "case_no": {"type": "integer", "description": "Case number."},
                    "max_rows": {"type": "integer", "default": 1000, "description": "Maximum documents to return."}
                }
            },
        },
        {
            "name": "get_case_history",
            "description": "Get audit trail/history for a case.",
            "inputSchema": {
                "type": "object",
                "required": ["case_no"],
                "properties": {
                    "case_no": {"type": "integer", "description": "Case number."}
                }
            },
        },
        {
            "name": "create_user",
            "description": "Create a new Therefore user account.",
            "inputSchema": {
                "type": "object",
                "required": ["user_name", "full_name"],
                "properties": {
                    "user_name": {"type": "string", "description": "Username (login name)."},
                    "full_name": {"type": "string", "description": "Full display name."},
                    "email": {"type": "string", "description": "Email address."},
                    "password": {"type": "string", "description": "Initial password."},
                    "domain_name": {"type": "string", "description": "Domain name for AD/LDAP users."}
                }
            },
        },
        {
            "name": "update_user_group_assignment",
            "description": "Update user's group memberships.",
            "inputSchema": {
                "type": "object",
                "required": ["user_id"],
                "properties": {
                    "user_id": {"type": "integer", "description": "User ID. Use execute_users_query to find user IDs."},
                    "group_ids": {"type": "array", "items": {"type": "integer"}, "description": "List of group IDs to assign."}
                }
            },
        },
        {
            "name": "get_user_group_assignment",
            "description": "Get user's group memberships. Returns list of group IDs the user belongs to.",
            "inputSchema": {
                "type": "object",
                "required": ["user_id"],
                "properties": {
                    "user_id": {"type": "integer", "description": "User ID. Use execute_users_query to find user IDs."}
                }
            },
        },
        {
            "name": "set_user_password",
            "description": "Set (reset) a user's password. Admin operation to change another user's password.",
            "inputSchema": {
                "type": "object",
                "required": ["user_id", "new_password"],
                "properties": {
                    "user_id": {"type": "integer", "description": "User ID. Use execute_users_query to find user IDs."},
                    "new_password": {"type": "string", "description": "New password to set."}
                }
            },
        },
        {
            "name": "change_user_password",
            "description": "Change the current user's password. Requires old password for verification.",
            "inputSchema": {
                "type": "object",
                "required": ["old_password", "new_password"],
                "properties": {
                    "old_password": {"type": "string", "description": "Current password for verification."},
                    "new_password": {"type": "string", "description": "New password to set."}
                }
            },
        },
        {
            "name": "reset_user_password",
            "description": "Reset a user's password and optionally send reset email. Generates a new temporary password.",
            "inputSchema": {
                "type": "object",
                "required": ["user_id"],
                "properties": {
                    "user_id": {"type": "integer", "description": "User ID. Use execute_users_query to find user IDs."},
                    "send_email": {"type": "boolean", "default": True, "description": "Send password reset email to user."}
                }
            },
        },
        {
            "name": "delete_portal_user",
            "description": "Delete a portal user account. For portal/external users only, not internal Therefore users.",
            "inputSchema": {
                "type": "object",
                "required": ["user_id"],
                "properties": {
                    "user_id": {"type": "integer", "description": "Portal user ID."}
                }
            },
        },
        {
            "name": "save_portal_user",
            "description": "Create or update a portal user account. For portal/external users only.",
            "inputSchema": {
                "type": "object",
                "required": ["user_id"],
                "properties": {
                    "user_id": {"type": "integer", "description": "User ID (0 to create new user)."},
                    "user_name": {"type": "string", "description": "Username (login name)."},
                    "full_name": {"type": "string", "description": "Full display name."},
                    "email": {"type": "string", "description": "Email address."},
                    "is_active": {"type": "boolean", "description": "Whether the user account is active."}
                }
            },
        },
        {
            "name": "move_user_license",
            "description": "Move a license from one user to another. Used for license management.",
            "inputSchema": {
                "type": "object",
                "required": ["source_user_id", "target_user_id"],
                "properties": {
                    "source_user_id": {"type": "integer", "description": "Source user ID to take license from."},
                    "target_user_id": {"type": "integer", "description": "Target user ID to assign license to."}
                }
            },
        },
        {
            "name": "get_user_settings",
            "description": "Get user-specific settings and preferences.",
            "inputSchema": {
                "type": "object",
                "required": ["user_id"],
                "properties": {
                    "user_id": {"type": "integer", "description": "User ID. Use execute_users_query to find user IDs."}
                }
            },
        },
        {
            "name": "set_user_settings",
            "description": "Update user-specific settings and preferences.",
            "inputSchema": {
                "type": "object",
                "required": ["user_id", "settings"],
                "properties": {
                    "user_id": {"type": "integer", "description": "User ID. Use execute_users_query to find user IDs."},
                    "settings": {"type": "object", "description": "Settings object with key-value pairs to update."}
                }
            },
        },
        {
            "name": "copy_document",
            "description": "Copy a document to create a duplicate. Can copy to a different category.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer", "description": "Document number to copy."},
                    "target_category_no": {"type": "integer", "description": "Optional target category. Use resolve_category to find category numbers."},
                    "index_data_items": {"type": "array", "items": {"type": "object"}, "description": "Optional index data for the copy."}
                }
            },
        },
        {
            "name": "get_document_versions",
            "description": "List all versions of a document.",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer", "description": "Document number."}
                }
            },
        },
        {
            "name": "get_converted_doc_streams",
            "description": (
                "Retrieve document streams converted server-side to a target format "
                "(PDF, TIFF, JPEG, etc.) via the GetConvertedDocStreams WebAPI endpoint. "
                "Returns the converted file data as base64-encoded streams."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["doc_no", "convert_to"],
                "properties": {
                    "doc_no": {"type": "integer", "description": "Document number"},
                    "convert_to": {
                        "description": (
                            "Target conversion format. Accepts string names or numeric values: "
                            "Original (0), SingleTIFF (1), SinglePDF (2), MultipageTIFF (3), "
                            "MultipagePDF (4), SearchablePDF (5), SearchablePDFA (6), JPEG (50)"
                        )
                    },
                    "annotation_mode": {
                        "description": "Annotation handling: Default (0), Merge (1), Hide (2)"
                    },
                    "signature_mode": {
                        "description": (
                            "Signature handling: NoSignature (0), SignatureOnly (1), "
                            "SignatureAndTimestamp (2)"
                        )
                    },
                    "certificate_name": {"type": "string", "description": "Certificate name for signing"},
                    "time_stamp_server": {"type": "string", "description": "Timestamp server URL"},
                    "time_stamp_user": {"type": "string", "description": "Timestamp server username"},
                    "time_stamp_pwd": {"type": "string", "description": "Timestamp server password"},
                    "multipage_stream_name": {"type": "string", "description": "Output filename for multipage conversions"},
                    "stream_nos": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Specific stream numbers to convert (omit for all streams)"
                    },
                    "version_no": {"type": "integer", "description": "Document version number (omit for latest)"},
                    "retrieve_reason": {"type": "string", "description": "Reason for retrieval (audit trail)"},
                    "archive_converted_files": {"type": "boolean", "description": "Archive converted files back to the document"},
                    "custom_archive_file_name": {"type": "string", "description": "Custom filename when archiving converted files"},
                }
            },
        },
        {
            "name": "get_logfiles",
            "description": (
                "Retrieve and parse Therefore server log files from the system "
                "Logfiles category (ID 1). Fetches document streams, decodes base64, "
                "and parses pipe-delimited entries into structured data."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "days_back": {
                        "type": "integer",
                        "description": "Number of days to look back (default: 7)",
                    },
                    "application_filter": {
                        "type": "string",
                        "description": "Filter by application name (e.g. 'Therefore Server')",
                    },
                    "max_docs": {
                        "type": "integer",
                        "description": "Maximum number of log documents to retrieve (default: 10)",
                    },
                    "include_raw": {
                        "type": "boolean",
                        "description": "Include raw stream text in response (default: false)",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["analysis", "summary", "full"],
                        "description": (
                            "Output mode: 'analysis' (default) returns compact statistics with "
                            "grouped error summaries — fits in LLM context; 'summary' returns "
                            "statistics plus every individual error entry; 'full' returns all "
                            "individual entries per document."
                        ),
                    },
                    "severity_filter": {
                        "type": "string",
                        "enum": ["all", "errors_only"],
                        "description": "Filter entries: 'all' (default) processes everything; 'errors_only' skips non-error entries for faster processing.",
                    },
                }
            },
        },
        {
            "name": "get_login_history",
            "description": (
                "Retrieve and analyse Therefore login history. Shows authentication attempts "
                "including successes, failures, client applications, IP addresses, and server nodes. "
                "When username is provided, returns history for that single user (fuzzy-matched). "
                "When username is omitted, enumerates all tenant users and fetches history for each, "
                "providing a per-user breakdown. "
                "Use 'analysis' mode for a compact summary with failure rates and breakdowns by "
                "user, day, client, IP, node, and error code; 'full' mode returns all raw entries."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "days_back": {
                        "type": "integer",
                        "description": "Number of days of history to retrieve (default 7).",
                    },
                    "username": {
                        "type": "string",
                        "description": (
                            "Optional username to filter by. Fuzzy-matched to resolve the user. "
                            "Omit for tenant-wide login history."
                        ),
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum number of login entries per user to retrieve (default 1000).",
                    },
                    "output_mode": {
                        "type": "string",
                        "enum": ["analysis", "full"],
                        "description": (
                            "Output format: 'analysis' (default) returns statistics with breakdowns "
                            "by day, client, IP, node, and error code; 'full' returns all raw entries."
                        ),
                    },
                },
            },
        },
        {
            "name": "generate_category_config",
            "description": (
                "Generate a Therefore category configuration delta XML from a structured spec or "
                "natural language description. The generated XML can be imported into Therefore to "
                "create a new category with fields, keyword dictionaries, folder, and auto-layout. "
                "Provide EITHER 'spec' (structured JSON) OR 'description' (natural language/text), not both."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "object",
                        "description": (
                            "Structured category spec as JSON. Must contain 'category' (with 'name', optional "
                            "'folder', 'description', 'force_new_folder', 'folder_conflict_policy', "
                            "'dictionary_conflict_policy') and 'fields' array. Each field has 'name', 'type' "
                            "(text|number|decimal|date|keyword_single|table), optional 'length', 'scale', "
                            "'dictionary' (with 'mode', 'name', 'keywords'), or 'columns' (for table type)."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "Natural language description of the category to create. Include category name, "
                            "folder, and field definitions. Example: 'Create an Invoice category in folder "
                            "\"Accounting\" with text field \"Invoice Number\", date field \"Invoice Date\", "
                            "decimal field \"Total Amount\"'."
                        ),
                    },
                    "baseline_path": {
                        "type": "string",
                        "description": (
                            "Path to baseline TheConfiguration.xml export. If omitted, uses "
                            "THEREFORE_CONFIG_BASELINE_PATH env var or default per-tenant path."
                        ),
                    },
                    "api_check": {
                        "type": "boolean",
                        "description": (
                            "Whether to use the Therefore API to check for existing folders and "
                            "dictionaries. Defaults to true."
                        ),
                    },
                    "output_path": {
                        "type": "string",
                        "description": (
                            "Optional output file path for the generated XML. If omitted, auto-generates "
                            "a path under docs/notes/generated_configs/."
                        ),
                    },
                },
            },
        },
    ]

    # Add optional tenant selection to all tools.
    for tool in tools:
        schema = tool.get('inputSchema')
        if not schema or schema.get('type') != 'object':
            continue
        props = schema.setdefault('properties', {})
        if 'tenant' not in props:
            props['tenant'] = {
                'type': 'string',
                'description': 'Optional tenant key when multiple tenants are configured.',
            }
        if 'tenant_hint' not in props:
            props['tenant_hint'] = {
                'type': 'string',
                'description': 'Optional user prompt text to infer tenant when tenant is not provided.',
            }
    return tools


def build_prompts() -> List[Dict[str, Any]]:
    """Build the list of MCP prompts exposed by this server."""
    return [
        {
            'name': 'create-category',
            'description': (
                'Interactive guide for creating a new Therefore category configuration. '
                'Walks through gathering requirements, building a structured spec, and '
                'generating the delta XML via the generate_category_config tool.'
            ),
            'arguments': [
                {
                    'name': 'description',
                    'description': 'Optional starting description of the category to create.',
                    'required': False,
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
    ):
        self.clients = clients
        self.default_tenant = default_tenant
        self._last_tenant: Optional[str] = default_tenant
        self.tenant_labels = tenant_labels
        self.tenant_assignee_aliases = tenant_assignee_aliases or {}
        self.tools = build_tools()
        self.prompts = build_prompts()
        cache_dir = os.environ.get('THEREFORE_CACHE_DIR') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cache')
        os.makedirs(cache_dir, exist_ok=True)
        self._category_cache: Dict[str, Dict[str, Any]] = {}
        self._category_cache_ts: Dict[str, float] = {}
        self._category_cache_ttl: int = 300
        self._category_cache_path = os.path.join(cache_dir, 'category_cache_{tenant}.json')
        self._field_cache: Dict[str, Dict[int, Dict[str, Any]]] = {}
        self._field_cache_ts: Dict[str, Dict[int, float]] = {}
        self._field_cache_ttl: int = 300
        self._field_cache_path = os.path.join(cache_dir, 'field_cache_{tenant}.json')
        self._keyword_dict_cache: Dict[str, Dict[str, Any]] = {}
        self._keyword_dict_cache_ts: Dict[str, float] = {}
        self._keyword_dict_cache_ttl: int = 300
        self._keyword_dict_cache_path = os.path.join(cache_dir, 'keyword_dictionary_cache_{tenant}.json')

    def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = msg.get('method')
        msg_id = msg.get('id')
        params = msg.get('params') or {}

        if method == 'initialize':
            return _result_response(msg_id, {
                'protocolVersion': '2024-11-05',
                'capabilities': {
                    'tools': {'listChanged': False},
                    'prompts': {'listChanged': False},
                },
                'serverInfo': {
                    'name': 'therefore-mcp',
                    'version': '0.1.0'
                }
            })
        if method in ('initialized', 'notifications/initialized'):
            return None
        if method == 'tools/list':
            return _result_response(msg_id, {'tools': self.tools})
        if method == 'tools/call':
            name = params.get('name')
            args = params.get('arguments') or {}
            try:
                result = self._call_tool(name, args)
                return _result_response(msg_id, _tool_content(result))
            except Exception as e:
                return _result_response(msg_id, {
                    'content': [{
                        'type': 'text',
                        'text': json.dumps({'error': str(e), 'trace': traceback.format_exc()}, indent=2)
                    }],
                    'isError': True
                })
        if method == 'ping':
            return _result_response(msg_id, {})
        if method == 'prompts/list':
            return _result_response(msg_id, {'prompts': self.prompts})
        if method == 'prompts/get':
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
        name = params.get('name')
        arguments = params.get('arguments') or {}
        if name == 'create-category':
            return self._prompt_create_category(arguments)
        raise ValueError(f"Unknown prompt: {name}")

    def _prompt_create_category(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        description = arguments.get('description', '')
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
3. **Generate**: Call the `generate_category_config` tool with the `spec` parameter.
4. **Review**: Present the result to the user — the generated XML content and output file path.

Keep it conversational. Ask clarifying questions if the user's requirements are ambiguous.
"""
        if description:
            prompt_text += f"\n## Starting Point\nThe user provided this initial description:\n\n{description}\n"

        return {
            'description': 'Interactive guide for creating a Therefore category configuration.',
            'messages': [
                {
                    'role': 'user',
                    'content': {
                        'type': 'text',
                        'text': prompt_text,
                    },
                },
            ],
        }

    def _call_tool(self, name: str, args: Dict[str, Any]) -> Any:
        tenant = self._resolve_tenant(args)
        client = self.clients[tenant]
        if name == 'resolve_category':
            return self._resolve_category(args, tenant, client)
        if name == 'list_category_fields':
            return self._list_category_fields(int(args['category_no']), tenant, client)
        if name == 'resolve_field':
            return self._resolve_field(args, tenant, client)
        if name == 'get_categories_tree':
            return client.get_categories_tree(args.get('payload'))
        if name == 'get_category_info':
            return client.get_category_info(int(args['category_no']))
        if name == 'get_document':
            return client.get_document(
                doc_no=int(args['doc_no']),
                include_index_data=bool(args.get('include_index_data', True)),
                include_streams_info=bool(args.get('include_streams_info', False)),
                include_streams_data=bool(args.get('include_streams_data', False)),
                include_checkout_status=bool(args.get('include_checkout_status', False)),
                include_access_mask=bool(args.get('include_access_mask', False)),
            )
        if name == 'get_document_index_data':
            return client.get_document_index_data(int(args['doc_no']))
        if name == 'get_web_api_server_version':
            return client.get_web_api_server_version()
        if name == 'get_connection_token':
            return client.get_connection_token()
        if name == 'get_domain_info':
            return client.get_domain_info()
        if name == 'get_client_discovery_info':
            return client.get_client_discovery_info()
        if name == 'get_system_customer_id':
            return client.get_system_customer_id()
        if name == 'get_connected_user':
            return client.get_connected_user(bool(args.get('create', False)))
        if name == 'get_permission_constants':
            return client.get_permission_constants()
        if name == 'get_role_permission_constants':
            return client.get_role_permission_constants()
        if name == 'get_document_properties':
            return client.get_document_properties(
                doc_no=int(args['doc_no']),
                version_no=int(args.get('version_no', 0)),
                is_doc_title_needed=bool(args.get('is_doc_title_needed', False)),
            )
        if name == 'get_document_history':
            return client.get_document_history(int(args['doc_no']))
        if name == 'get_document_checkout_status':
            return client.get_document_checkout_status(int(args['doc_no']))
        if name == 'get_objects_list':
            return client.get_objects_list(args['load_items_list'])
        if name == 'get_objects':
            resp = client.get_objects(
                flags=int(args['flags']),
                obj_type=int(args['obj_type']),
            )
            # Normalize items across GetObjects/GetObjectsList payload shapes.
            resp['items'] = self._extract_object_items(resp)
            return resp
        if name == 'execute_users_query':
            domain_names = args.get('domain_names')
            if domain_names is None:
                try:
                    domain_info = client.get_domain_info() or {}
                    domain_names = domain_info.get('DomainNames') or []
                except Exception:
                    domain_names = None
            return client.execute_users_query(
                query=args['query'],
                domain_names=domain_names,
                flags=int(args.get('flags', 5)),
            )
        if name == 'get_users_from_group':
            return client.get_users_from_group(
                group_id=args.get('group_id'),
                group_name=args.get('group_name'),
                domain_name=args.get('domain_name'),
            )
        if name == 'get_user_details':
            return client.get_user_details(int(args['user_or_group_id']))
        if name == 'get_keywords_by_field_no':
            return client.get_keywords_by_field_no(
                field_no=int(args['field_no']),
                category_no=args.get('category_no'),
                case_definition_no=args.get('case_definition_no'),
                dependent_field_filter_value=args.get('dependent_field_filter_value'),
                show_deactivated_keywords=args.get('show_deactivated_keywords'),
                index_data_items=args.get('index_data_items'),
                skip_loading_keyword_nos=args.get('skip_loading_keyword_nos'),
                max_rows=args.get('max_rows'),
            )
        if name == 'get_keywords_by_key_dic':
            return client.get_keywords_by_key_dic(
                key_dic_no=int(args['key_dic_no']),
                filter_value=args.get('filter_value'),
                max_values=args.get('max_values'),
                include_deactivated_keywords=args.get('include_deactivated_keywords'),
            )
        if name == 'validate_keywords':
            return client.validate_keywords(
                field_no=int(args['field_no']),
                keywords=args.get('keywords') or [],
                is_filter_mode=args.get('is_filter_mode'),
            )
        if name == 'get_keywords_by_dictionary_name':
            return self._get_keywords_by_dictionary_name(args, tenant, client)
        if name == 'add_dictionary_keyword':
            return self._add_dictionary_keyword(args, tenant, client)
        if name == 'update_dictionary_keyword':
            return self._update_dictionary_keyword(args, tenant, client)
        if name == 'delete_dictionary_keyword':
            return self._delete_dictionary_keyword(args, tenant, client)
        if name == 'deactivate_dictionary_keyword':
            return self._deactivate_dictionary_keyword(args, tenant, client)
        if name == 'execute_workflow_query_for_all':
            debug_enabled = bool(args.get('debug', False))
            debug_log_path = args.get('debug_log_path')
            debug_progress_every = int(args.get('debug_progress_every') or 500)
            debug_info: Dict[str, Any] = {
                'workflow_query': {},
                'instance_details': {},
            } if debug_enabled else {}
            if debug_log_path:
                self._debug_log(debug_log_path, {
                    'event': 'start',
                    'workflow_flags': args.get('workflow_flags'),
                    'max_rows': args.get('max_rows'),
                    'detail_mode': args.get('instance_detail_mode'),
                })
            if args.get('max_rows') is None:
                max_rows = self._default_workflow_max_rows(client)
            else:
                max_rows = int(args.get('max_rows', 1000))
            workflow_flags = self._normalize_workflow_flags(args.get('workflow_flags', 0))
            start = time.time()
            try:
                resp = client.execute_workflow_query_for_all(
                    workflow_flags=workflow_flags,
                    max_rows=max_rows,
                )
            except Exception as exc:
                if debug_enabled:
                    debug_info['workflow_query'] = {
                        'workflow_flags': workflow_flags,
                        'max_rows': max_rows,
                        'duration_ms': int((time.time() - start) * 1000),
                        'error': str(exc),
                    }
                    if debug_log_path:
                        self._debug_log(debug_log_path, {
                            'event': 'workflow_query_error',
                            'workflow_flags': workflow_flags,
                            'max_rows': max_rows,
                            'error': str(exc),
                        })
                    return {'error': str(exc), 'debug': debug_info}
                raise
            if debug_enabled:
                debug_info['workflow_query'] = {
                    'workflow_flags': workflow_flags,
                    'max_rows': max_rows,
                    'duration_ms': int((time.time() - start) * 1000),
                }
            if debug_log_path:
                self._debug_log(debug_log_path, {
                    'event': 'workflow_query_done',
                    'workflow_flags': workflow_flags,
                    'max_rows': max_rows,
                    'duration_ms': int((time.time() - start) * 1000),
                })
            if not args.get('include_instance_details'):
                output = {'workflow_query': resp, 'debug': debug_info} if debug_enabled else resp
                if debug_log_path:
                    self._debug_log(debug_log_path, {'event': 'done'})
                return output
            detail_mode = str(args.get('instance_detail_mode') or 'summary').strip().lower()
            if detail_mode == 'none':
                detail_mode = 'summary'
            tasks, user_field_labels, _ = self._extract_workflow_tasks(resp)
            max_rows_reached = len(tasks) == max_rows
            details_start = time.time()
            details, detail_errors = self._fetch_workflow_instance_details(
                client,
                tasks,
                max_workers=int(args.get('max_instance_workers') or 4),
                is_access_mask_needed=bool(args.get('is_access_mask_needed', False)),
                load_history=bool(args.get('load_history', False)),
                debug_log_path=debug_log_path,
                debug_progress_every=debug_progress_every,
            )
            if debug_enabled:
                debug_info['instance_details'] = {
                    'mode': detail_mode,
                    'requested': len(tasks),
                    'loaded': len(details),
                    'failed': len(detail_errors),
                    'duration_ms': int((time.time() - details_start) * 1000),
                    'errors_sample': detail_errors[:10],
                }
            if debug_log_path:
                self._debug_log(debug_log_path, {
                    'event': 'instance_details_done',
                    'requested': len(tasks),
                    'loaded': len(details),
                    'failed': len(detail_errors),
                    'duration_ms': int((time.time() - details_start) * 1000),
                })
            self._attach_instance_details(tasks, details, detail_errors, detail_mode)
            output = {
                'workflow_query': resp,
                'instances': tasks,
                'user_field_labels': user_field_labels,
                'max_rows': max_rows,
                'max_rows_reached': max_rows_reached,
                'total_count': len(tasks),
                'note': 'Reached max_rows; results may be truncated. Increase max_rows to fetch more.' if max_rows_reached else None,
                'instance_detail_mode': detail_mode,
                'instance_details_loaded': len(details),
                'instance_details_failed': len(detail_errors),
                'instance_detail_errors': detail_errors,
                'debug': debug_info if debug_enabled else None,
            }
            if debug_log_path:
                self._debug_log(debug_log_path, {
                    'event': 'done',
                    'total_count': len(tasks),
                    'max_rows_reached': max_rows_reached,
                })
            return output
        if name == 'execute_workflow_query_for_process':
            debug_enabled = bool(args.get('debug', False))
            debug_log_path = args.get('debug_log_path')
            debug_progress_every = int(args.get('debug_progress_every') or 500)
            debug_info: Dict[str, Any] = {
                'workflow_query': {},
                'instance_details': {},
            } if debug_enabled else {}
            if debug_log_path:
                self._debug_log(debug_log_path, {
                    'event': 'start',
                    'process_no': args.get('process_no'),
                    'workflow_flags': args.get('workflow_flags'),
                    'max_rows': args.get('max_rows'),
                    'detail_mode': args.get('instance_detail_mode'),
                })
            if args.get('max_rows') is None:
                max_rows = self._default_workflow_max_rows(client)
            else:
                max_rows = int(args.get('max_rows', 1000))
            workflow_flags = self._normalize_workflow_flags(args.get('workflow_flags', 0))
            process_no = int(args['process_no'])
            start = time.time()
            try:
                resp = client.execute_workflow_query_for_process(
                    process_no=process_no,
                    workflow_flags=workflow_flags,
                    max_rows=max_rows,
                )
            except Exception as exc:
                if debug_enabled:
                    debug_info['workflow_query'] = {
                        'process_no': process_no,
                        'workflow_flags': workflow_flags,
                        'max_rows': max_rows,
                        'duration_ms': int((time.time() - start) * 1000),
                        'error': str(exc),
                    }
                    if debug_log_path:
                        self._debug_log(debug_log_path, {
                            'event': 'workflow_query_error',
                            'process_no': process_no,
                            'workflow_flags': workflow_flags,
                            'max_rows': max_rows,
                            'error': str(exc),
                        })
                    return {'error': str(exc), 'debug': debug_info}
                raise
            if debug_enabled:
                debug_info['workflow_query'] = {
                    'process_no': process_no,
                    'workflow_flags': workflow_flags,
                    'max_rows': max_rows,
                    'duration_ms': int((time.time() - start) * 1000),
                }
            if debug_log_path:
                self._debug_log(debug_log_path, {
                    'event': 'workflow_query_done',
                    'process_no': process_no,
                    'workflow_flags': workflow_flags,
                    'max_rows': max_rows,
                    'duration_ms': int((time.time() - start) * 1000),
                })
            if not args.get('include_instance_details'):
                output = {'workflow_query': resp, 'debug': debug_info} if debug_enabled else resp
                if debug_log_path:
                    self._debug_log(debug_log_path, {'event': 'done'})
                return output
            detail_mode = str(args.get('instance_detail_mode') or 'summary').strip().lower()
            if detail_mode == 'none':
                detail_mode = 'summary'
            tasks, user_field_labels, _ = self._extract_workflow_tasks(resp)
            max_rows_reached = len(tasks) == max_rows
            details_start = time.time()
            details, detail_errors = self._fetch_workflow_instance_details(
                client,
                tasks,
                max_workers=int(args.get('max_instance_workers') or 4),
                is_access_mask_needed=bool(args.get('is_access_mask_needed', False)),
                load_history=bool(args.get('load_history', False)),
                debug_log_path=debug_log_path,
                debug_progress_every=debug_progress_every,
            )
            if debug_enabled:
                debug_info['instance_details'] = {
                    'mode': detail_mode,
                    'requested': len(tasks),
                    'loaded': len(details),
                    'failed': len(detail_errors),
                    'duration_ms': int((time.time() - details_start) * 1000),
                    'errors_sample': detail_errors[:10],
                }
            if debug_log_path:
                self._debug_log(debug_log_path, {
                    'event': 'instance_details_done',
                    'requested': len(tasks),
                    'loaded': len(details),
                    'failed': len(detail_errors),
                    'duration_ms': int((time.time() - details_start) * 1000),
                })
            self._attach_instance_details(tasks, details, detail_errors, detail_mode)
            output = {
                'workflow_query': resp,
                'instances': tasks,
                'user_field_labels': user_field_labels,
                'max_rows': max_rows,
                'max_rows_reached': max_rows_reached,
                'total_count': len(tasks),
                'note': 'Reached max_rows; results may be truncated. Increase max_rows to fetch more.' if max_rows_reached else None,
                'instance_detail_mode': detail_mode,
                'instance_details_loaded': len(details),
                'instance_details_failed': len(detail_errors),
                'instance_detail_errors': detail_errors,
                'debug': debug_info if debug_enabled else None,
            }
            if debug_log_path:
                self._debug_log(debug_log_path, {
                    'event': 'done',
                    'total_count': len(tasks),
                    'max_rows_reached': max_rows_reached,
                })
            return output
        if name == 'get_linked_workflows_for_doc':
            return client.get_linked_workflows_for_doc(
                doc_no=int(args['doc_no']),
                wf_doc_link_type=int(args.get('wf_doc_link_type', 0)),
            )
        if name == 'get_workflow_history':
            return client.get_workflow_history(
                instance_no=int(args['instance_no']),
                block_size=int(args.get('block_size', 1000)),
                include_routing_info=bool(args.get('include_routing_info', True)),
                max_creation_date=args.get('max_creation_date'),
                seq_pos=int(args.get('seq_pos', 0)),
            )
        if name == 'get_workflow_instance':
            return client.get_workflow_instance(
                instance_no=int(args['instance_no']),
                token_no=int(args.get('token_no', 0)),
                is_access_mask_needed=bool(args.get('is_access_mask_needed', False)),
                load_history=bool(args.get('load_history', False)),
            )
        if name == 'get_workflow_process':
            return client.get_workflow_process(
                process_no=int(args['process_no']),
                version_no=int(args.get('version_no', 0)),
                load_tasks=bool(args.get('load_tasks', True)),
                is_access_mask_needed=bool(args.get('is_access_mask_needed', False)),
            )
        if name == 'get_workflow_task_settings':
            return client.get_workflow_task_settings(
                task_no=int(args['task_no']),
                process_no=int(args['process_no']),
                version_no=int(args.get('version_no', 0)),
                setting_names=args.get('setting_names'),
            )
        if name == 'get_my_workflow_tasks':
            return self._get_my_workflow_tasks(args, tenant, client)
        if name == 'get_my_workflow_instances':
            args = dict(args or {})
            args['filter_to_user'] = True
            output = self._get_workflow_instances_core(args, tenant, client)
            output['tasks'] = output.get('instances', [])
            return output
        if name == 'get_all_workflow_instances':
            args = dict(args or {})
            args['filter_to_user'] = False
            output = self._get_workflow_instances_core(args, tenant, client)
            output['tasks'] = output.get('instances', [])
            return output
        if name == 'get_workflow_instances_for_user':
            args = dict(args or {})
            args['filter_to_user'] = True
            output = self._get_workflow_instances_core(args, tenant, client)
            output['tasks'] = output.get('instances', [])
            return output
        if name == 'execute_single_query':
            query = args['query']
            categories = self._extract_category_list(query)
            if categories and len(categories) > 1:
                base_query = dict(query)
                for key in ('CategoryNos', 'CategoryIDs', 'CategoryIds', 'Categories', 'CategoryList'):
                    base_query.pop(key, None)
                if isinstance(base_query.get('CategoryNo'), (list, tuple, set, str)):
                    base_query.pop('CategoryNo', None)
                row_block_size = int(base_query.get('RowBlockSize') or 1000)
                max_rows = int(base_query.get('MaxRows') or 2147483647)
                queries = []
                for cat in categories:
                    q = dict(base_query)
                    q['CategoryNo'] = int(cat)
                    queries.append(q)
                return client.execute_async_multi_query_all(
                    queries=queries,
                    full_text=args.get('full_text'),
                    row_block_size=row_block_size,
                    max_rows=max_rows,
                )
            return client.execute_single_query(
                query=query,
                full_text=args.get('full_text')
            )
        if name == 'execute_async_single_query':
            row_block_size = int(args.get('row_block_size', 1000))
            max_rows = int(args.get('max_rows', 2147483647))
            auto_fetch_all = bool(args.get('auto_fetch_all', True))
            if auto_fetch_all:
                return client.execute_async_single_query_all(
                    query=args['query'],
                    full_text=args.get('full_text'),
                    row_block_size=row_block_size,
                    max_rows=max_rows,
                )
            return client.execute_async_single_query(
                query=args['query'],
                full_text=args.get('full_text')
            )
        if name == 'get_next_single_query_rows':
            return client.get_next_single_query_rows(
                query_id=int(args['query_id']),
                row_block_size=int(args['row_block_size']),
            )
        if name == 'release_single_query':
            return client.release_single_query(int(args['query_id']))
        if name == 'execute_full_text_query':
            return client.execute_full_text_query(
                search=args['search'],
                categories=args.get('categories'),
                max_rows=int(args.get('max_rows', 100)),
                include_index_data=bool(args.get('include_index_data', False)),
                case_no=int(args.get('case_no', 0)),
            )
        if name == 'call_endpoint':
            return client.call_endpoint(
                endpoint=args['endpoint'],
                payload=args.get('payload'),
            )
        if name == 'execute_statistics_query':
            query_type = self._normalize_statistics_query_type(args.get('query_type'))
            return client.execute_statistics_query(
                query_type=query_type,
                restrict_to_obj_no=args.get('restrict_to_obj_no'),
                restrict_to_user=args.get('restrict_to_user'),
            )
        if name == 'execute_async_multi_query':
            row_block_size = int(args.get('row_block_size', 1000))
            max_rows = int(args.get('max_rows', 2147483647))
            auto_fetch_all = bool(args.get('auto_fetch_all', True))
            if auto_fetch_all:
                return client.execute_async_multi_query_all(
                    queries=args['queries'],
                    full_text=args.get('full_text'),
                    row_block_size=row_block_size,
                    max_rows=max_rows,
                )
            return client.execute_async_multi_query(
                queries=args['queries'],
                full_text=args.get('full_text'),
            )
        if name == 'get_next_multi_query_rows':
            return client.get_next_multi_query_rows(
                query_id=int(args['query_id']),
                row_block_size=int(args['row_block_size']),
            )
        if name == 'release_multi_query':
            return client.release_multi_query(int(args['query_id']))
        if name == 'create_document':
            category_no = int(args['category_no'])
            check_in_comments = args.get('check_in_comments', '')
            with_auto_append_mode = int(args.get('with_auto_append_mode', 0))
            do_fill_dependent_fields = bool(args.get('do_fill_dependent_fields', True))
            run_webclient_flow = bool(args.get('run_webclient_flow', True))
            index_data_items = args.get('index_data_items') or []

            streams = []
            for s in (args.get('streams') or []):
                file_name = s.get('file_name')
                file_data_base64 = s.get('file_data_base64')
                file_data_text = s.get('file_data_text')
                if file_data_text and not file_data_base64:
                    file_data_base64 = base64.b64encode(file_data_text.encode('utf-8')).decode('ascii')
                if not file_name:
                    raise ValueError('stream missing file_name')
                if not file_data_base64:
                    raise ValueError('stream missing file_data_base64 or file_data_text')
                streams.append({
                    'FileName': file_name,
                    'FileDataBase64JSON': file_data_base64,
                    'NewStreamInsertMode': 0,
                })

            if not streams:
                content_text = args.get('content_text')
                if content_text is None:
                    raise ValueError('Either streams or content_text must be provided')
                filename = args.get('content_filename') or 'document.txt'
                streams.append(ThereforeClient.make_stream_from_text(filename, content_text))

            return client.create_document(
                category_no=category_no,
                streams=streams,
                index_data_items=index_data_items,
                check_in_comments=check_in_comments,
                with_auto_append_mode=with_auto_append_mode,
                do_fill_dependent_fields=do_fill_dependent_fields,
                run_webclient_flow=run_webclient_flow,
                persist_evaluate_response_path='/Volumes/DataSSD/source/therefore-mcp/docs/notes/evaluate_conditional_properties.json',
            )
        if name == 'update_document_index_data':
            return self._update_document_index_data(args, tenant, client)
        if name == 'update_document':
            return self._update_document(args, tenant, client)
        if name == 'add_streams_to_document':
            return self._add_streams_to_document(args, tenant, client)
        if name == 'delete_document':
            return client.delete_document(int(args['doc_no']))
        if name == 'check_out_document':
            return client.check_out_document(
                doc_no=int(args['doc_no']),
                version_no=int(args.get('version_no', 0)),
            )
        if name == 'check_in_document':
            return client.check_in_document(
                doc_no=int(args['doc_no']),
                check_in_comments=args.get('check_in_comments'),
                version_no=int(args.get('version_no', 0)),
            )
        if name == 'undo_check_out_document':
            return client.undo_check_out_document(
                doc_no=int(args['doc_no']),
                version_no=int(args.get('version_no', 0)),
            )
        if name == 'add_comment':
            return client.add_comment(
                doc_no=int(args['doc_no']),
                comment_text=str(args['comment_text']),
                version_no=int(args.get('version_no', 0)),
            )
        if name == 'get_comments':
            return client.get_comments(
                doc_no=int(args['doc_no']),
                version_no=int(args.get('version_no', 0)),
            )
        if name == 'complete_task':
            return client.complete_task(
                workflow_instance_token=str(args['workflow_instance_token']),
                task_no=int(args['task_no']),
                user_decision=args.get('user_decision'),
                index_data_items=args.get('index_data_items'),
            )
        if name == 'claim_workflow_instance':
            return client.claim_workflow_instance(
                workflow_instance_token=str(args['workflow_instance_token']),
                task_no=int(args['task_no']) if args.get('task_no') is not None else None,
            )
        if name == 'disclaim_workflow_instance':
            return client.disclaim_workflow_instance(
                workflow_instance_token=str(args['workflow_instance_token']),
                task_no=int(args['task_no']) if args.get('task_no') is not None else None,
            )
        if name == 'delegate_workflow_instance':
            return client.delegate_workflow_instance(
                workflow_instance_token=str(args['workflow_instance_token']),
                user_id=int(args['user_id']),
                task_no=int(args['task_no']) if args.get('task_no') is not None else None,
            )
        if name == 'create_case':
            return client.create_case(
                case_definition_no=int(args['case_definition_no']),
                index_data_items=args.get('index_data_items'),
            )
        if name == 'get_case':
            return client.get_case(int(args['case_no']))
        if name == 'get_case_documents':
            return client.get_case_documents(
                case_no=int(args['case_no']),
                max_rows=int(args.get('max_rows', 1000)),
            )
        if name == 'get_case_history':
            return client.get_case_history(int(args['case_no']))
        if name == 'create_user':
            return client.create_user(
                user_name=str(args['user_name']),
                full_name=str(args['full_name']),
                email=args.get('email'),
                password=args.get('password'),
                domain_name=args.get('domain_name'),
            )
        if name == 'update_user_group_assignment':
            return client.update_user_group_assignment(
                user_id=int(args['user_id']),
                group_ids=args.get('group_ids'),
            )
        if name == 'get_user_group_assignment':
            return client.get_user_group_assignment(int(args['user_id']))
        if name == 'set_user_password':
            return client.set_user_password(
                user_id=int(args['user_id']),
                new_password=str(args['new_password']),
            )
        if name == 'change_user_password':
            return client.change_user_password(
                old_password=str(args['old_password']),
                new_password=str(args['new_password']),
            )
        if name == 'reset_user_password':
            return client.reset_user_password(
                user_id=int(args['user_id']),
                send_email=bool(args.get('send_email', True)),
            )
        if name == 'delete_portal_user':
            return client.delete_portal_user(int(args['user_id']))
        if name == 'save_portal_user':
            return client.save_portal_user(
                user_id=int(args['user_id']),
                user_name=args.get('user_name'),
                full_name=args.get('full_name'),
                email=args.get('email'),
                is_active=args.get('is_active'),
            )
        if name == 'move_user_license':
            return client.move_user_license(
                source_user_id=int(args['source_user_id']),
                target_user_id=int(args['target_user_id']),
            )
        if name == 'get_user_settings':
            return client.get_user_settings(int(args['user_id']))
        if name == 'set_user_settings':
            return client.set_user_settings(
                user_id=int(args['user_id']),
                settings=args['settings'],
            )
        if name == 'copy_document':
            return client.copy_document(
                doc_no=int(args['doc_no']),
                target_category_no=int(args['target_category_no']) if args.get('target_category_no') is not None else None,
                index_data_items=args.get('index_data_items'),
            )
        if name == 'get_document_versions':
            return client.get_document_versions(int(args['doc_no']))
        if name == 'get_converted_doc_streams':
            return self._get_converted_doc_streams(args, tenant, client)
        if name == 'get_logfiles':
            return self._get_logfiles(args, tenant, client)
        if name == 'get_login_history':
            return self._get_login_history(args, tenant, client)
        if name == 'generate_category_config':
            return self._generate_category_config(args, tenant, client)

        raise ValueError(f'Unknown tool: {name}')

    def _generate_category_config(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        import xml.etree.ElementTree as ET
        from generate import spec_from_mapping, parse_description, build_delta_xml

        spec_obj = args.get('spec')
        description = args.get('description')
        if spec_obj and description:
            raise ValueError("Provide either 'spec' or 'description', not both.")
        if not spec_obj and not description:
            raise ValueError("Provide either 'spec' (structured JSON) or 'description' (natural language text).")

        if spec_obj:
            spec = spec_from_mapping(spec_obj)
        else:
            spec = parse_description(description)

        # Resolve baseline path
        baseline_path = args.get('baseline_path')
        if not baseline_path:
            baseline_path = os.environ.get('THEREFORE_CONFIG_BASELINE_PATH')
        if not baseline_path:
            baseline_path = os.path.join(
                _REPO_ROOT, 'tools', 'config_generator', 'examples',
                f'{tenant}-baseline-TheConfiguration.xml'
            )
        if not os.path.isfile(baseline_path):
            raise ValueError(
                f"Baseline file not found: {baseline_path}. "
                f"Provide 'baseline_path', set THEREFORE_CONFIG_BASELINE_PATH env var, "
                f"or place a baseline export at tools/config_generator/examples/{tenant}-baseline-TheConfiguration.xml"
            )

        # API check: reuse the existing authenticated client by default
        api_check = args.get('api_check', True)
        api_client = client if api_check else None

        tree = build_delta_xml(spec, baseline_path, api_client=api_client, interactive=False)
        xml_content = ET.tostring(tree.getroot(), encoding='unicode')

        # Write output file
        output_path = args.get('output_path')
        if not output_path:
            slug = re.sub(r'[^A-Za-z0-9]+', '_', spec.name).strip('_').lower()
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_dir = os.path.join(_REPO_ROOT, 'docs', 'notes', 'generated_configs')
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, f'{slug}-{timestamp}-delta.xml')

        with open(output_path, 'w') as f:
            f.write(xml_content)

        field_names = [fld.name for fld in spec.fields]
        return {
            'xml_content': xml_content,
            'category_name': spec.name,
            'folder': spec.folder or '(auto-generated)',
            'fields_count': len(spec.fields),
            'field_names': field_names,
            'output_file': output_path,
            'note': (
                'Delta XML generated successfully. Import this file into Therefore using '
                'Administration > Configuration > Import to create the category.'
            ),
        }

    def _resolve_tenant(self, args: Dict[str, Any]) -> str:
        tenant_raw = (
            args.get('tenant')
            or args.get('tenant_name')
            or args.get('tenantName')
        )
        if tenant_raw:
            key = normalize_tenant_key(str(tenant_raw))
            if key in self.clients:
                self._last_tenant = key
                return key
            available = ', '.join(self.tenant_labels.get(k, k) for k in self.clients.keys())
            raise ValueError(f'Unknown tenant "{tenant_raw}". Available tenants: {available}')

        inferred = self._infer_tenant_from_args(args)
        if inferred:
            self._last_tenant = inferred
            return inferred

        if self._last_tenant and self._last_tenant in self.clients:
            return self._last_tenant

        if self.default_tenant and self.default_tenant in self.clients:
            return self.default_tenant
        if len(self.clients) == 1:
            return next(iter(self.clients.keys()))

        available = ', '.join(self.tenant_labels.get(k, k) for k in self.clients.keys())
        raise ValueError(f'Multiple tenants configured. Please provide tenant. Available tenants: {available}')

    def _infer_tenant_from_args(self, args: Dict[str, Any]) -> Optional[str]:
        if not args or not self.clients or len(self.clients) == 1:
            return None

        texts: List[str] = []

        hint = args.get('tenant_hint')
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
                if len(val) > 64 and re.fullmatch(r'[A-Za-z0-9+/=]+', val):
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
        text = re.sub(r'[^a-z0-9]+', ' ', text)
        return ' '.join(text.split())

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
            parts = [p for p in re.split(r'[\\s,;]+', value.strip()) if p]
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
            parts = [p.strip() for p in re.split(r'[;,]+', value) if p.strip()]
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
        for key in ('CategoryNo', 'CategoryNos', 'CategoryIDs', 'CategoryIds', 'Categories', 'CategoryList'):
            if key in query:
                return self._coerce_int_list(query.get(key))
        return None

    def _flatten_tree(self, items: List[Dict[str, Any]], parent_path: str = '') -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in items:
            name = item.get('Name') or ''
            path = f"{parent_path}/{name}" if parent_path else name
            out.append({
                'name': name,
                'path': path,
                'item_no': item.get('ItemNo'),
                'item_type': item.get('ItemType'),
                'folder_type': item.get('FolderType'),
                'parent_case_def_no': item.get('ParentCaseDefNo'),
                'parent_folder_no': item.get('ParentFolderNo'),
                'guid': item.get('Guid'),
            })
            children = item.get('ChildItems') or []
            if children:
                out.extend(self._flatten_tree(children, path))
        return out

    def _extract_object_items(self, payload: Any) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        seen: set = set()

        def record(obj: Dict[str, Any]) -> None:
            name = obj.get('Name') or obj.get('name') or ''
            if not name:
                return
            item_no = obj.get('ItemNo')
            if item_no is None:
                item_no = obj.get('ID')
            if item_no is None:
                item_no = obj.get('Id')
            if item_no is None:
                item_no = obj.get('Number')
            key = (name, str(item_no))
            if key in seen:
                return
            seen.add(key)
            items.append({
                'name': name,
                'item_no': item_no,
                'id': obj.get('ID') or obj.get('Id'),
                'item_type': obj.get('ItemType') or obj.get('Type') or obj.get('TypeNo'),
                'folder_type': obj.get('FolderType'),
                'parent_case_def_no': obj.get('ParentCaseDefNo'),
                'parent_folder_no': obj.get('ParentFolderNo'),
                'guid': obj.get('Guid'),
                'data': obj.get('Data'),
            })

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if ('Name' in node or 'name' in node) and any(k in node for k in ('ItemNo', 'ID', 'Id', 'Number')):
                    record(node)
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        walk(payload)
        return items

    def _resolve_category(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        query = args['query']
        max_results = int(args.get('max_results', 5))
        min_score = float(args.get('min_score', 0.35))
        include_non_categories = bool(args.get('include_non_categories', False))
        confirm_threshold = float(args.get('confirm_threshold', 0.75))

        flat = self._get_cached_categories(tenant, client)

        if not include_non_categories:
            flat = [c for c in flat if c.get('item_type') == 2]

        # if query is numeric, try exact match on item_no
        numeric_match = None
        if str(query).isdigit():
            qno = int(query)
            numeric_match = [c for c in flat if c.get('item_no') == qno]

        candidates = []
        for c in flat:
            name = c.get('name') or ''
            path = c.get('path') or ''
            score = max(self._score(query, name), self._score(query, path))
            if score >= min_score:
                candidates.append({**c, 'score': round(score, 4)})

        candidates.sort(key=lambda x: x['score'], reverse=True)
        if numeric_match:
            for c in numeric_match:
                if all(c['item_no'] != m['item_no'] for m in candidates):
                    candidates.insert(0, {**c, 'score': 1.0})

        needs_confirmation = True
        if candidates:
            if candidates[0]['score'] >= confirm_threshold and (len(candidates) == 1 or candidates[0]['score'] - candidates[1]['score'] >= 0.15):
                needs_confirmation = False

        return {
            'query': query,
            'count': len(candidates[:max_results]),
            'candidates': candidates[:max_results],
            'needs_confirmation': needs_confirmation,
        }

    def _resolve_keyword_dictionary(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        query = args.get('dictionary_name') or args.get('query') or args.get('name')
        if not query:
            raise ValueError('dictionary_name is required')
        max_results = int(args.get('max_results', 5))
        min_score = float(args.get('min_score', 0.35))
        confirm_threshold = float(args.get('confirm_threshold', 0.75))

        items = self._get_cached_keyword_dictionaries(tenant, client)

        numeric_match: List[Dict[str, Any]] = []
        if str(query).isdigit():
            qno = int(query)
            for item in items:
                try:
                    item_no = item.get('item_no')
                    if item_no is not None and int(item_no) == qno:
                        numeric_match.append(item)
                except Exception:
                    continue

        candidates: List[Dict[str, Any]] = []
        for item in items:
            name = item.get('name') or ''
            score = self._score(str(query), name)
            if score >= min_score:
                candidates.append({
                    'dictionary_no': item.get('item_no'),
                    'name': name,
                    'id': item.get('id'),
                    'data': item.get('data'),
                    'score': round(score, 4),
                })

        candidates.sort(key=lambda x: x['score'], reverse=True)
        if numeric_match:
            for item in numeric_match:
                if all(c['dictionary_no'] != item.get('item_no') for c in candidates):
                    candidates.insert(0, {
                        'dictionary_no': item.get('item_no'),
                        'name': item.get('name'),
                        'id': item.get('id'),
                        'data': item.get('data'),
                        'score': 1.0,
                    })

        needs_confirmation = True
        if candidates:
            if candidates[0]['score'] >= confirm_threshold and (len(candidates) == 1 or candidates[0]['score'] - candidates[1]['score'] >= 0.15):
                needs_confirmation = False

        return {
            'query': query,
            'count': len(candidates[:max_results]),
            'candidates': candidates[:max_results],
            'needs_confirmation': needs_confirmation,
        }

    def _get_keywords_by_dictionary_name(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
    ) -> Dict[str, Any]:
        resolution = self._resolve_keyword_dictionary(args, tenant, client)
        if resolution.get('needs_confirmation') or not resolution.get('candidates'):
            return resolution

        top = resolution['candidates'][0]
        dictionary_no = top.get('dictionary_no')
        if dictionary_no is None:
            raise ValueError('Resolved dictionary does not include a dictionary number')

        resp = client.get_keywords_by_key_dic(
            key_dic_no=int(dictionary_no),
            filter_value=args.get('filter_value'),
            max_values=args.get('max_values'),
            include_deactivated_keywords=args.get('include_deactivated_keywords'),
        )
        return {
            **resolution,
            'needs_confirmation': False,
            'dictionary_no': dictionary_no,
            'dictionary_name': top.get('name'),
            'keywords': resp.get('Keywords') or [],
            'keyword_nos': resp.get('KeywordNos') or [],
            'all_rows_returned': resp.get('AllRowsReturned'),
        }

    def _add_dictionary_keyword(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
    ) -> Dict[str, Any]:
        keyword_name = str(args.get('keyword_name') or '').strip()
        if not keyword_name:
            raise ValueError('keyword_name is required')

        dictionary_no = args.get('dictionary_no')
        dictionary_type_no = args.get('dictionary_type_no')
        dictionary_name = args.get('dictionary_name')

        if dictionary_no is None and dictionary_name:
            resolution = self._resolve_keyword_dictionary({'dictionary_name': dictionary_name}, tenant, client)
            if resolution.get('needs_confirmation') or not resolution.get('candidates'):
                return {
                    'keyword_name': keyword_name,
                    'needs_confirmation': True,
                    'resolution': resolution,
                }
            dictionary_no = resolution['candidates'][0].get('dictionary_no')
        if dictionary_no is None and dictionary_type_no is None:
            raise ValueError('dictionary_no, dictionary_name, or dictionary_type_no is required')

        check_existing = bool(args.get('check_existing', True))
        ignore_if_exists = bool(args.get('ignore_if_exists', True))
        include_deactivated = bool(args.get('include_deactivated_keywords', True))

        existing = []
        if check_existing and dictionary_no is not None:
            try:
                existing_resp = client.get_keywords_by_key_dic(
                    key_dic_no=int(dictionary_no),
                    include_deactivated_keywords=include_deactivated,
                    max_values=100000,
                )
                existing = existing_resp.get('Keywords') or []
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
                    'status': 'exists',
                    'keyword_name': keyword_name,
                    'matched_keyword': existing_match,
                    'dictionary_no': dictionary_no,
                    'dictionary_type_no': dictionary_type_no,
                }
            raise ValueError(f'Keyword "{keyword_name}" already exists in dictionary {dictionary_no}')

        resp = client.add_dictionary_keyword(
            dictionary_no=int(dictionary_no) if dictionary_no is not None else None,
            dictionary_type_no=int(dictionary_type_no) if dictionary_type_no is not None else None,
            keyword_name=keyword_name,
            is_keyword_deactivated=args.get('is_keyword_deactivated'),
        )
        return {
            'status': 'added',
            'keyword_name': keyword_name,
            'dictionary_no': dictionary_no,
            'dictionary_type_no': dictionary_type_no,
            'response': resp,
        }

    def _update_dictionary_keyword(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
    ) -> Dict[str, Any]:
        new_keyword_name = str(args.get('new_keyword_name') or '').strip()
        if not new_keyword_name:
            raise ValueError('new_keyword_name is required')

        dictionary_no = args.get('dictionary_no')
        dictionary_type_no = args.get('dictionary_type_no')
        dictionary_name = args.get('dictionary_name')
        keyword_id = args.get('keyword_id')
        keyword_name = args.get('keyword_name')

        if dictionary_no is None and dictionary_name:
            resolution = self._resolve_keyword_dictionary({'dictionary_name': dictionary_name}, tenant, client)
            if resolution.get('needs_confirmation') or not resolution.get('candidates'):
                return {
                    'keyword_name': keyword_name,
                    'new_keyword_name': new_keyword_name,
                    'needs_confirmation': True,
                    'resolution': resolution,
                }
            dictionary_no = resolution['candidates'][0].get('dictionary_no')

        if dictionary_no is None and dictionary_type_no is None:
            raise ValueError('dictionary_no, dictionary_name, or dictionary_type_no is required')

        include_deactivated = bool(args.get('include_deactivated_keywords', True))
        existing = []
        existing_resp = None
        if dictionary_no is not None:
            try:
                existing_resp = client.get_keywords_by_key_dic(
                    key_dic_no=int(dictionary_no),
                    include_deactivated_keywords=include_deactivated,
                    max_values=100000,
                )
                existing = existing_resp.get('Keywords') or []
            except Exception:
                existing = []

        if keyword_id is None:
            if not keyword_name:
                raise ValueError('keyword_id or keyword_name is required')
            target = str(keyword_name).strip().lower()
            if not existing_resp:
                raise ValueError('Unable to resolve keyword_id without dictionary keywords.')
            keyword_nos = existing_resp.get('KeywordNos') or []
            found_id = None
            for idx, kw in enumerate(existing):
                if str(kw).strip().lower() == target:
                    if idx < len(keyword_nos):
                        found_id = keyword_nos[idx]
                    break
            if found_id is None:
                raise ValueError(f'Keyword "{keyword_name}" not found in dictionary {dictionary_no}')
            keyword_id = found_id

        check_existing = bool(args.get('check_existing', True))
        ignore_if_exists = bool(args.get('ignore_if_exists', True))
        if check_existing and existing:
            target = new_keyword_name.strip().lower()
            for kw in existing:
                if str(kw).strip().lower() == target:
                    if ignore_if_exists:
                        return {
                            'status': 'exists',
                            'keyword_name': new_keyword_name,
                            'dictionary_no': dictionary_no,
                            'dictionary_type_no': dictionary_type_no,
                        }
                    raise ValueError(f'Keyword "{new_keyword_name}" already exists in dictionary {dictionary_no}')

        resp = client.update_dictionary_keyword(
            dictionary_no=int(dictionary_no) if dictionary_no is not None else None,
            dictionary_type_no=int(dictionary_type_no) if dictionary_type_no is not None else None,
            keyword_id=int(keyword_id),
            keyword_name=new_keyword_name,
            is_keyword_deactivated=args.get('is_keyword_deactivated'),
        )
        return {
            'status': 'updated',
            'dictionary_no': dictionary_no,
            'dictionary_type_no': dictionary_type_no,
            'keyword_id': keyword_id,
            'keyword_name': new_keyword_name,
            'response': resp,
        }

    def _delete_dictionary_keyword(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
    ) -> Dict[str, Any]:
        dictionary_no = args.get('dictionary_no')
        dictionary_type_no = args.get('dictionary_type_no')
        dictionary_name = args.get('dictionary_name')
        keyword_id = args.get('keyword_id')
        keyword_name = args.get('keyword_name')

        if dictionary_no is None and dictionary_name:
            resolution = self._resolve_keyword_dictionary({'dictionary_name': dictionary_name}, tenant, client)
            if resolution.get('needs_confirmation') or not resolution.get('candidates'):
                return {
                    'keyword_name': keyword_name,
                    'needs_confirmation': True,
                    'resolution': resolution,
                }
            dictionary_no = resolution['candidates'][0].get('dictionary_no')

        if dictionary_no is None and dictionary_type_no is None:
            raise ValueError('dictionary_no, dictionary_name, or dictionary_type_no is required')

        include_deactivated = bool(args.get('include_deactivated_keywords', True))
        if keyword_id is None:
            if not keyword_name:
                raise ValueError('keyword_id or keyword_name is required')
            existing_resp = client.get_keywords_by_key_dic(
                key_dic_no=int(dictionary_no),
                include_deactivated_keywords=include_deactivated,
                max_values=100000,
            )
            keywords = existing_resp.get('Keywords') or []
            keyword_nos = existing_resp.get('KeywordNos') or []
            target = str(keyword_name).strip().lower()
            found_id = None
            for idx, kw in enumerate(keywords):
                if str(kw).strip().lower() == target:
                    if idx < len(keyword_nos):
                        found_id = keyword_nos[idx]
                    break
            if found_id is None:
                raise ValueError(f'Keyword "{keyword_name}" not found in dictionary {dictionary_no}')
            keyword_id = found_id

        resp = client.delete_dictionary_keyword(
            dictionary_no=int(dictionary_no) if dictionary_no is not None else None,
            dictionary_type_no=int(dictionary_type_no) if dictionary_type_no is not None else None,
            keyword_id=int(keyword_id),
        )
        return {
            'status': 'deleted',
            'dictionary_no': dictionary_no,
            'dictionary_type_no': dictionary_type_no,
            'keyword_id': keyword_id,
            'response': resp,
        }

    def _deactivate_dictionary_keyword(
        self,
        args: Dict[str, Any],
        tenant: str,
        client: ThereforeClient,
    ) -> Dict[str, Any]:
        dictionary_no = args.get('dictionary_no')
        dictionary_type_no = args.get('dictionary_type_no')
        dictionary_name = args.get('dictionary_name')
        keyword_id = args.get('keyword_id')
        keyword_name = args.get('keyword_name')

        if dictionary_no is None and dictionary_name:
            resolution = self._resolve_keyword_dictionary({'dictionary_name': dictionary_name}, tenant, client)
            if resolution.get('needs_confirmation') or not resolution.get('candidates'):
                return {
                    'keyword_name': keyword_name,
                    'needs_confirmation': True,
                    'resolution': resolution,
                }
            dictionary_no = resolution['candidates'][0].get('dictionary_no')

        if dictionary_no is None and dictionary_type_no is None:
            raise ValueError('dictionary_no, dictionary_name, or dictionary_type_no is required')

        include_deactivated = bool(args.get('include_deactivated_keywords', True))
        if keyword_id is None:
            if not keyword_name:
                raise ValueError('keyword_id or keyword_name is required')
            existing_resp = client.get_keywords_by_key_dic(
                key_dic_no=int(dictionary_no),
                include_deactivated_keywords=include_deactivated,
                max_values=100000,
            )
            keywords = existing_resp.get('Keywords') or []
            keyword_nos = existing_resp.get('KeywordNos') or []
            target = str(keyword_name).strip().lower()
            found_id = None
            for idx, kw in enumerate(keywords):
                if str(kw).strip().lower() == target:
                    if idx < len(keyword_nos):
                        found_id = keyword_nos[idx]
                    break
            if found_id is None:
                raise ValueError(f'Keyword "{keyword_name}" not found in dictionary {dictionary_no}')
            keyword_id = found_id

        resp = client.update_dictionary_keyword(
            dictionary_no=int(dictionary_no) if dictionary_no is not None else None,
            dictionary_type_no=int(dictionary_type_no) if dictionary_type_no is not None else None,
            keyword_id=int(keyword_id),
            is_keyword_deactivated=True,
        )
        return {
            'status': 'deactivated',
            'dictionary_no': dictionary_no,
            'dictionary_type_no': dictionary_type_no,
            'keyword_id': keyword_id,
            'response': resp,
        }

    def _list_category_fields(self, category_no: int, tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        fields = self._get_cached_fields(tenant, category_no, client)
        simplified = []
        for f in fields:
            simplified.append({
                'field_no': f.get('FieldNo'),
                'field_id': f.get('FieldID'),
                'caption': f.get('Caption'),
                'index_name': f.get('IndexDataFieldName'),
                'field_type': f.get('FieldType'),
                'type_no': f.get('TypeNo'),
                'mandatory': f.get('Mandatory'),
                'visible': f.get('Visible'),
                'regular_expr': f.get('RegularExpr'),
                'regex_sample': f.get('RegExSample'),
                'is_auto_append': f.get('IsAutoAppendField'),
                'counter_mode': f.get('CounterMode'),
            })
        return {
            'category_no': category_no,
            'field_count': len(simplified),
            'fields': simplified,
        }

    def _resolve_field(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        category_no = int(args['category_no'])
        query = args['query']
        max_results = int(args.get('max_results', 5))
        min_score = float(args.get('min_score', 0.35))
        field_type_hint = args.get('field_type_hint')
        confirm_threshold = float(args.get('confirm_threshold', 0.75))

        fields = self._get_cached_fields(tenant, category_no, client)

        candidates = []
        for f in fields:
            caption = f.get('Caption') or ''
            field_id = f.get('FieldID') or ''
            index_name = f.get('IndexDataFieldName') or ''

            score = max(
                self._score(query, caption),
                self._score(query, field_id),
                self._score(query, index_name),
            )

            if field_type_hint is not None and f.get('FieldType') == field_type_hint:
                score = min(1.0, score + 0.05)

            if score >= min_score:
                candidates.append({
                    'field_no': f.get('FieldNo'),
                    'field_id': field_id,
                    'caption': caption,
                    'index_name': index_name,
                    'field_type': f.get('FieldType'),
                    'type_no': f.get('TypeNo'),
                    'mandatory': f.get('Mandatory'),
                    'visible': f.get('Visible'),
                    'score': round(score, 4),
                })

        candidates.sort(key=lambda x: x['score'], reverse=True)
        needs_confirmation = True
        if candidates:
            if candidates[0]['score'] >= confirm_threshold and (len(candidates) == 1 or candidates[0]['score'] - candidates[1]['score'] >= 0.15):
                needs_confirmation = False

        return {
            'category_no': category_no,
            'query': query,
            'count': len(candidates[:max_results]),
            'candidates': candidates[:max_results],
            'needs_confirmation': needs_confirmation,
        }

    def _prepare_index_update(
        self,
        doc_no: int,
        updates: List[Dict[str, Any]],
        index_data_items_override: Optional[List[Dict[str, Any]]] = None,
        tenant: str = '',
        client: Optional[ThereforeClient] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str], Optional[str], Optional[int]]:
        if client is None:
            raise ValueError('client is required for index update preparation')
        current = client.get_document_index_data(doc_no)
        idx = current.get('IndexData') or {}
        last_change_time = idx.get('LastChangeTime')
        last_change_time_iso = idx.get('LastChangeTimeISO8601')
        category_no = idx.get('CategoryNo')

        if index_data_items_override is not None:
            return index_data_items_override, last_change_time, last_change_time_iso, category_no

        if not updates:
            return [], last_change_time, last_change_time_iso, category_no

        type_keys = [
            'StringIndexData',
            'IntIndexData',
            'MoneyIndexData',
            'DateIndexData',
            'DateTimeIndexData',
            'LogicalIndexData',
            'SingleKeywordData',
            'MultipleKeywordData',
            'TableIndexData',
        ]

        existing_map: Dict[int, Tuple[str, Optional[str]]] = {}
        for item in (idx.get('IndexDataItems') or []):
            for key in type_keys:
                data = item.get(key)
                if data and data.get('FieldNo') is not None:
                    try:
                        fno = int(data.get('FieldNo'))
                    except Exception:
                        continue
                    existing_map[fno] = (key, data.get('FieldName'))
                    break

        category_fields: Optional[List[Dict[str, Any]]] = None

        def find_field_meta(field_no: int) -> Optional[Dict[str, Any]]:
            nonlocal category_fields
            if category_no and category_fields is None:
                category_fields = self._get_cached_fields(tenant, int(category_no), client)
            if not category_fields:
                return None
            for f in category_fields:
                try:
                    if int(f.get('FieldNo')) == field_no:
                        return f
                except Exception:
                    continue
            return None

        def resolve_field_no(query: str) -> Tuple[int, Dict[str, Any]]:
            nonlocal category_fields
            if category_no and category_fields is None:
                category_fields = self._get_cached_fields(int(category_no))
            if not category_fields:
                raise ValueError('Category fields not available to resolve field name')

            candidates = []
            for f in category_fields:
                caption = f.get('Caption') or ''
                field_id = f.get('FieldID') or ''
                index_name = f.get('IndexDataFieldName') or ''
                score = max(
                    self._score(query, caption),
                    self._score(query, field_id),
                    self._score(query, index_name),
                )
                if score >= 0.35:
                    candidates.append((score, f))

            if not candidates:
                raise ValueError(f'No field matches query: {query}')

            candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_field = candidates[0]
            needs_confirmation = True
            if best_score >= 0.75 and (len(candidates) == 1 or best_score - candidates[1][0] >= 0.15):
                needs_confirmation = False
            if needs_confirmation:
                top = [
                    {
                        'field_no': c[1].get('FieldNo'),
                        'caption': c[1].get('Caption'),
                        'field_id': c[1].get('FieldID'),
                        'index_name': c[1].get('IndexDataFieldName'),
                        'score': round(c[0], 4),
                    }
                    for c in candidates[:5]
                ]
                raise ValueError(f'Ambiguous field name \"{query}\". Candidates: {top}')

            return int(best_field.get('FieldNo')), best_field

        def infer_type_key(field_type: Optional[int]) -> Optional[str]:
            mapping = {
                1: 'StringIndexData',
                2: 'IntIndexData',
                3: 'DateIndexData',
                5: 'MoneyIndexData',
                6: 'LogicalIndexData',
                9: 'StringIndexData',
            }
            return mapping.get(field_type)

        index_data_items: List[Dict[str, Any]] = []
        for upd in updates:
            field_no = upd.get('field_no')
            if field_no is None:
                query = (
                    upd.get('field_name')
                    or upd.get('field_id')
                    or upd.get('caption')
                    or upd.get('index_name')
                    or upd.get('query')
                )
                if not query:
                    raise ValueError('Update item must include field_no or field_name/query')
                field_no, meta = resolve_field_no(str(query))
            else:
                field_no = int(field_no)
            value = upd.get('value')

            if field_no in existing_map:
                type_key, field_name = existing_map[field_no]
            else:
                meta = find_field_meta(field_no)
                if not meta:
                    raise ValueError(f'Field {field_no} not found for document {doc_no}')
                field_type = meta.get('FieldType')
                if field_type == 4:
                    raise ValueError(f'Field {field_no} is label-only and cannot hold a value')
                type_key = infer_type_key(field_type)
                if not type_key:
                    raise ValueError(f'Field {field_no} has unsupported FieldType {field_type}; provide index_data_items explicitly')
                field_name = meta.get('IndexDataFieldName') or meta.get('FieldID') or meta.get('Caption')

            if type_key == 'MultipleKeywordData':
                if value is None:
                    data_value = []
                elif isinstance(value, list):
                    data_value = value
                else:
                    data_value = [value]
                data = {
                    'FieldNo': field_no,
                    'DataValue': data_value,
                    'FieldName': field_name,
                }
            else:
                data = {
                    'FieldNo': field_no,
                    'DataValue': value,
                    'FieldName': field_name,
                }

            index_data_items.append({type_key: data})

        return index_data_items, last_change_time, last_change_time_iso, category_no

    def _update_document_index_data(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        doc_no = int(args['doc_no'])
        check_in_comments = args.get('check_in_comments', '')
        do_fill_dependent_fields = bool(args.get('do_fill_dependent_fields', True))

        index_data_items, last_change_time, last_change_time_iso, _ = self._prepare_index_update(
            doc_no=doc_no,
            updates=args.get('updates') or [],
            index_data_items_override=args.get('index_data_items'),
            tenant=tenant,
            client=client,
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
            'update_response': update_resp,
            'updated_index_data': updated.get('IndexData'),
        }

    def _update_document(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        doc_no = int(args['doc_no'])
        check_in_comments = args.get('check_in_comments', '')
        do_fill_dependent_fields = bool(args.get('do_fill_dependent_fields', True))

        index_data_items, last_change_time, last_change_time_iso, _ = self._prepare_index_update(
            doc_no=doc_no,
            updates=args.get('updates') or [],
            index_data_items_override=args.get('index_data_items'),
            tenant=tenant,
            client=client,
        )

        streams_to_update = []
        for s in (args.get('streams') or []):
            file_name = s.get('file_name')
            file_data_base64 = s.get('file_data_base64')
            file_data_text = s.get('file_data_text')
            if file_data_text and not file_data_base64:
                file_data_base64 = base64.b64encode(file_data_text.encode('utf-8')).decode('ascii')
            if not file_name:
                raise ValueError('stream missing file_name')
            if not file_data_base64:
                raise ValueError('stream missing file_data_base64 or file_data_text')
            entry = {
                'FileName': file_name,
                'FileDataBase64JSON': file_data_base64,
                'NewStreamInsertMode': self._normalize_stream_insert_mode(s.get('new_stream_insert_mode', 0)),
            }
            if s.get('stream_no') is not None:
                entry['StreamNo'] = int(s.get('stream_no'))
            streams_to_update.append(entry)

        streams_to_rename = []
        for r in (args.get('streams_to_rename') or []):
            streams_to_rename.append({
                'StreamNo': int(r['stream_no']),
                'FileName': r['file_name'],
            })

        update_resp = client.update_document(
            doc_no=doc_no,
            index_data_items=index_data_items,
            streams_to_update=streams_to_update or None,
            stream_nos_to_delete=args.get('stream_nos_to_delete'),
            streams_to_rename=streams_to_rename or None,
            check_in_comments=check_in_comments,
            do_fill_dependent_fields=do_fill_dependent_fields,
            last_change_time=last_change_time,
            last_change_time_iso=last_change_time_iso,
            conversion_options=self._normalize_conversion_options(args.get('conversion_options')),
        )
        updated = client.get_document(doc_no, include_index_data=True, include_streams_info=True)
        return {
            'update_response': update_resp,
            'updated_document': updated,
        }

    def _add_streams_to_document(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        doc_no = int(args['doc_no'])
        check_in_comments = args.get('check_in_comments', '')
        conversion_options = self._normalize_conversion_options(args.get('conversion_options'))

        streams_to_upload = []
        for s in (args.get('streams') or []):
            file_name = s.get('file_name')
            file_data_base64 = s.get('file_data_base64')
            file_data_text = s.get('file_data_text')
            if file_data_text and not file_data_base64:
                file_data_base64 = base64.b64encode(file_data_text.encode('utf-8')).decode('ascii')
            if not file_name:
                raise ValueError('stream missing file_name')
            if not file_data_base64:
                raise ValueError('stream missing file_data_base64 or file_data_text')
            entry = {
                'FileName': file_name,
                'FileDataBase64JSON': file_data_base64,
                'NewStreamInsertMode': self._normalize_stream_insert_mode(s.get('new_stream_insert_mode', 0)),
            }
            if s.get('stream_no') is not None:
                entry['StreamNo'] = int(s.get('stream_no'))
            streams_to_upload.append(entry)

        add_resp = client.add_streams_to_document(
            doc_no=doc_no,
            streams=streams_to_upload,
            conversion_options=conversion_options,
            check_in_comments=check_in_comments,
        )
        updated = client.get_document(doc_no, include_index_data=False, include_streams_info=True)
        return {
            'add_streams_response': add_resp,
            'updated_streams_info': updated.get('StreamsInfo'),
        }

    def _get_converted_doc_streams(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        doc_no = int(args['doc_no'])
        conversion_options: Dict[str, Any] = {}
        if args.get('convert_to') is not None:
            conversion_options['ConvertTo'] = args['convert_to']
        if args.get('annotation_mode') is not None:
            conversion_options['AnnotationMode'] = args['annotation_mode']
        if args.get('signature_mode') is not None:
            conversion_options['SignatureMode'] = args['signature_mode']
        if args.get('certificate_name') is not None:
            conversion_options['CertificateName'] = args['certificate_name']
        if args.get('time_stamp_server') is not None:
            conversion_options['TimeStampServer'] = args['time_stamp_server']
        if args.get('time_stamp_user') is not None:
            conversion_options['TimeStampUser'] = args['time_stamp_user']
        if args.get('time_stamp_pwd') is not None:
            conversion_options['TimeStampPwd'] = args['time_stamp_pwd']
        if args.get('multipage_stream_name') is not None:
            conversion_options['MultipageStreamName'] = args['multipage_stream_name']
        conversion_options = self._normalize_conversion_options(conversion_options) or {}
        return client.get_converted_doc_streams(
            doc_no=doc_no,
            conversion_options=conversion_options,
            stream_nos=args.get('stream_nos'),
            version_no=args.get('version_no'),
            is_file_data_base64_json_needed=True,
            retrieve_reason=args.get('retrieve_reason'),
            archive_converted_files=args.get('archive_converted_files'),
            custom_archive_file_name=args.get('custom_archive_file_name'),
        )

    def _get_logfiles(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        from datetime import timedelta

        days_back = int(args.get('days_back', 7))
        application_filter = args.get('application_filter')
        max_docs = int(args.get('max_docs', 10))
        include_raw = bool(args.get('include_raw', False))
        output_mode = args.get('output_mode', 'analysis')
        severity_filter = args.get('severity_filter', 'all')

        # Validate category 1 is the Logfiles category
        cat_info = client.get_category_info(1)
        cat_name = (cat_info.get('Name') or '').strip()

        # Discover field numbers from data fields (skip labels with TypeNo=4)
        generated_field_no = None
        application_field_no = None
        data_field_types = {1, 2, 3, 7}  # text, number, date, datetime

        for f in (cat_info.get('CategoryFields') or []):
            type_no = f.get('TypeNo')
            if type_no not in data_field_types:
                continue
            caption = (f.get('Caption') or '').lower()
            fno = f.get('FieldNo')
            if caption == 'generated' and type_no == 7:
                generated_field_no = fno
            elif caption == 'application' and type_no == 1:
                application_field_no = fno

        if generated_field_no is None:
            raise ValueError(
                f"Category 1 ('{cat_name}') does not have a 'Generated' datetime data field. "
                "Expected the Logfiles category."
            )

        # Build query conditions
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days_back)
        cutoff_str = cutoff.strftime('%Y-%m-%dT00:00:00')

        conditions = [
            {'FieldNo': generated_field_no, 'Condition': f'>= {cutoff_str}'},
        ]
        if application_filter:
            if application_field_no is None:
                raise ValueError(
                    "Cannot filter by application: 'Application' text field not found in category 1."
                )
            conditions.append({'FieldNo': application_field_no, 'Condition': application_filter})

        query = {
            'CategoryNo': 1,
            'Condition': conditions,
            'MaxRows': max_docs,
        }

        query_result = client.execute_single_query(query)
        qr = query_result.get('QueryResult') or {}
        rows = qr.get('ResultRows') or []

        documents = []
        fetch_errors = []
        all_entries: List[Dict[str, Any]] = []
        doc_meta: List[Dict[str, Any]] = []

        for row in rows:
            doc_no = row.get('DocNo')
            if not doc_no:
                continue
            try:
                doc = client.get_document(int(doc_no), include_streams_data=True, include_index_data=False)
                streams_info = doc.get('StreamsInfo') or []
                doc_entries: List[Dict[str, Any]] = []
                raw_texts: List[str] = []
                doc_application = ''
                doc_server = ''

                for stream in streams_info:
                    b64_data = stream.get('StreamDataBase64JSON') or stream.get('FileDataBase64JSON')
                    if not b64_data:
                        continue
                    raw_bytes = base64.b64decode(b64_data)
                    if raw_bytes.startswith(b'\xef\xbb\xbf'):
                        raw_bytes = raw_bytes[3:]
                    text = raw_bytes.decode('utf-8', errors='replace')
                    if include_raw:
                        raw_texts.append(text)
                    parsed = MCPServer._parse_log_text(text, include_raw=include_raw)
                    header = parsed.get('header', {})
                    if not doc_application and header.get('application'):
                        doc_application = header['application']
                    if not doc_server and header.get('server'):
                        doc_server = header['server']
                    doc_entries.extend(parsed.get('entries', []))

                if severity_filter == 'errors_only':
                    doc_entries = [
                        e for e in doc_entries
                        if e.get('error_code', '0').strip() not in ('', '0')
                    ]
                all_entries.extend(doc_entries)

                # Extract first date from entries for doc metadata
                doc_date = ''
                for e in doc_entries:
                    ts = e.get('timestamp', '')
                    if ts:
                        doc_date = ts.split(',')[0].split('T')[0].strip()
                        break

                doc_meta.append({
                    'doc_no': int(doc_no),
                    'application': doc_application,
                    'server': doc_server,
                    'date': doc_date,
                    'entry_count': len(doc_entries),
                })

                if output_mode == 'full':
                    doc_result: Dict[str, Any] = {
                        'doc_no': doc_no,
                        'metadata': {k: row.get(k) for k in row if k != 'DocNo'},
                        'entry_count': len(doc_entries),
                        'entries': doc_entries,
                    }
                    if include_raw:
                        doc_result['raw_streams'] = raw_texts
                    documents.append(doc_result)

            except Exception as exc:
                fetch_errors.append({'doc_no': doc_no, 'error': str(exc)})

        # Branch on output mode
        if output_mode == 'full':
            result: Dict[str, Any] = {
                'status': 'ok',
                'documents': documents,
                'summary': {
                    'total_documents': len(documents),
                    'total_entries': len(all_entries),
                    'days_back': days_back,
                    'query_rows_returned': len(rows),
                },
            }
            if fetch_errors:
                result['fetch_errors'] = fetch_errors
            return result

        # Summary or analysis mode
        summary = MCPServer._summarize_log_entries(all_entries, doc_meta, compact=(output_mode == 'analysis'))
        result = {
            'status': 'ok',
            **summary,
        }
        if fetch_errors:
            result['fetch_errors'] = fetch_errors
        return result

    def _get_login_history(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        days_back = int(args.get('days_back', 7))
        username = args.get('username')
        max_entries = int(args.get('max_entries', 1000))
        output_mode = args.get('output_mode', 'analysis')

        # Compute TimestampFrom
        from datetime import datetime, timedelta, timezone
        ts_from = datetime.now(timezone.utc) - timedelta(days=days_back)
        timestamp_from = ts_from.strftime('%Y-%m-%dT%H:%M:%S')

        if username:
            # --- Single-user mode: resolve and fetch for one user ---
            best_user, candidates, needs_confirmation = self._resolve_user_from_query(username, tenant, client)
            if not best_user:
                return {'status': 'error', 'error': f'No user found matching "{username}".'}
            if needs_confirmation:
                return {
                    'status': 'needs_confirmation',
                    'message': f'Ambiguous username "{username}". Please confirm or be more specific.',
                    'candidates': candidates,
                }
            user_no = best_user.get('UserId')
            resolved_user_info = {
                'UserId': best_user.get('UserId'),
                'UserName': best_user.get('UserName'),
                'DisplayName': best_user.get('DisplayName'),
                'SMTP': best_user.get('SMTP'),
            }

            # Domain/AD accounts resolve to UserId 0 — login history is not available for them
            if user_no == 0:
                return {
                    'status': 'ok',
                    'warning': (
                        f'User "{resolved_user_info["DisplayName"]}" is a domain account (UserId=0). '
                        'Login history is only available for native Therefore accounts, not domain/AD accounts.'
                    ),
                    'tenant': tenant,
                    'days_back': days_back,
                    'total_entries': 0,
                    'resolved_user': resolved_user_info,
                }

            resp = client.get_login_history(max_entries=max_entries, timestamp_from=timestamp_from, user_no=user_no)
            entries = resp.get('Entries') or []
            # Tag entries with user identity
            for entry in entries:
                entry['_UserNo'] = user_no
                entry['_DisplayName'] = resolved_user_info['DisplayName']
                entry['_UserName'] = resolved_user_info['UserName']

            result = self._build_login_history_result(entries, tenant, days_back, output_mode, all_users_mode=False)
            result['resolved_user'] = resolved_user_info
            return result
        else:
            # --- All-users mode: enumerate users and fetch per-user ---
            users_resp = client.execute_users_query(query='', flags=5)
            all_users = users_resp.get('Users') or []
            # Filter out service accounts
            active_users = [u for u in all_users if not u.get('ServiceAccount', False)]

            entries: List[Dict[str, Any]] = []
            users_queried = 0
            users_with_logins = 0
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def fetch_user_history(user: Dict[str, Any]) -> List[Dict[str, Any]]:
                uid = user.get('UserId')
                try:
                    resp = client.get_login_history(
                        max_entries=max_entries,
                        timestamp_from=timestamp_from,
                        user_no=uid,
                    )
                    user_entries = resp.get('Entries') or []
                    for entry in user_entries:
                        entry['_UserNo'] = uid
                        entry['_DisplayName'] = user.get('DisplayName') or user.get('UserName') or str(uid)
                        entry['_UserName'] = user.get('UserName') or str(uid)
                    return user_entries
                except Exception:
                    return []

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(fetch_user_history, u): u for u in active_users}
                for future in as_completed(futures):
                    users_queried += 1
                    user_entries = future.result()
                    if user_entries:
                        users_with_logins += 1
                        entries.extend(user_entries)

            # Sort combined entries by timestamp descending
            entries.sort(key=lambda e: e.get('Timestamp', ''), reverse=True)

            result = self._build_login_history_result(entries, tenant, days_back, output_mode, all_users_mode=True)
            result['users_queried'] = users_queried
            result['users_with_logins'] = users_with_logins
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
            'status': 'ok',
            'tenant': tenant,
            'days_back': days_back,
            'total_entries': len(entries),
        }

        # Full mode — return raw entries
        if output_mode == 'full':
            result['entries'] = entries
            return result

        # Analysis mode
        successes = 0
        failures = 0
        daily: Dict[str, Dict[str, int]] = {}       # date -> {success, failure}
        by_client: Dict[str, int] = {}               # client+version -> count
        by_ip: Dict[str, int] = {}                   # IP -> count
        by_node: Dict[str, int] = {}                 # node -> count
        by_error: Dict[int, Dict[str, Any]] = {}     # error_code -> {count, examples}
        by_user: Dict[str, Dict[str, Any]] = {}      # display_name -> {user_no, username, success, failure}

        for entry in entries:
            error_code = entry.get('ErrorCode', 0)
            is_success = (error_code == 0)
            if is_success:
                successes += 1
            else:
                failures += 1

            # Daily breakdown
            ts = entry.get('Timestamp') or ''
            day = ts[:10] if len(ts) >= 10 else 'unknown'
            if day not in daily:
                daily[day] = {'success': 0, 'failure': 0}
            daily[day]['success' if is_success else 'failure'] += 1

            # Client breakdown
            client_name = entry.get('Client') or 'unknown'
            version_str = entry.get('ClientVersionString') or ''
            client_key = f'{client_name} {version_str}'.strip() if version_str else client_name
            by_client[client_key] = by_client.get(client_key, 0) + 1

            # IP breakdown
            ip = entry.get('IPAddress') or 'unknown'
            by_ip[ip] = by_ip.get(ip, 0) + 1

            # Node breakdown
            node = entry.get('NodeName') or 'unknown'
            by_node[node] = by_node.get(node, 0) + 1

            # Per-user breakdown
            display_name = entry.get('_DisplayName') or 'unknown'
            if display_name not in by_user:
                by_user[display_name] = {
                    'user_no': entry.get('_UserNo'),
                    'username': entry.get('_UserName') or '',
                    'success': 0,
                    'failure': 0,
                }
            by_user[display_name]['success' if is_success else 'failure'] += 1

            # Error breakdown
            if not is_success:
                if error_code not in by_error:
                    by_error[error_code] = {'count': 0, 'examples': []}
                by_error[error_code]['count'] += 1
                if len(by_error[error_code]['examples']) < 3:
                    by_error[error_code]['examples'].append({
                        'timestamp': ts,
                        'client': client_key,
                        'ip': ip,
                        'node': node,
                        'user': display_name,
                    })

        total = successes + failures
        result['summary'] = {
            'total_logins': total,
            'successes': successes,
            'failures': failures,
            'failure_rate_pct': round(failures / total * 100, 1) if total else 0,
        }

        # Daily activity sorted by date
        result['daily_activity'] = [
            {'date': d, **counts}
            for d, counts in sorted(daily.items())
        ]

        # Per-user breakdown sorted by total logins desc
        if all_users_mode:
            result['by_user'] = [
                {
                    'display_name': name,
                    'user_no': info['user_no'],
                    'username': info['username'],
                    'success': info['success'],
                    'failure': info['failure'],
                    'total': info['success'] + info['failure'],
                }
                for name, info in sorted(
                    by_user.items(),
                    key=lambda x: x[1]['success'] + x[1]['failure'],
                    reverse=True,
                )
            ]

        # Client breakdown sorted by count desc
        result['by_client'] = [
            {'client': k, 'count': v}
            for k, v in sorted(by_client.items(), key=lambda x: x[1], reverse=True)
        ]

        # IP breakdown top 20
        result['by_ip'] = [
            {'ip': k, 'count': v}
            for k, v in sorted(by_ip.items(), key=lambda x: x[1], reverse=True)[:20]
        ]

        # Node breakdown sorted by count desc
        result['by_node'] = [
            {'node': k, 'count': v}
            for k, v in sorted(by_node.items(), key=lambda x: x[1], reverse=True)
        ]

        # Error breakdown sorted by count desc
        if by_error:
            result['errors'] = [
                {'error_code': code, 'count': info['count'], 'examples': info['examples']}
                for code, info in sorted(by_error.items(), key=lambda x: x[1]['count'], reverse=True)
            ]

        return result

    # Semantic names for pipe-delimited log fields (by positional index)
    _LOG_FIELD_NAMES = {
        0: 'timestamp',
        1: 'user',
        2: 'source',
        3: 'action',
        4: 'error_code',
        5: 'doc_no',
        6: 'version_no',
        7: 'category',
        # 8 is variable/unused — kept as f8
        9: 'detail',
        10: 'extra_info',
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
            if ':' in line:
                key, _, val = line.partition(':')
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

            parts = line.split('|')

            # Pipe-delimited log line (6+ fields)
            if len(parts) >= 6:
                entry: Dict[str, Any] = {}
                if include_raw:
                    entry['raw'] = line
                for idx, val in enumerate(parts):
                    name = MCPServer._LOG_FIELD_NAMES.get(idx, f'f{idx}')
                    entry[name] = val.strip()
                if header:
                    entry['application'] = header.get('application', '')
                    entry['server'] = header.get('server', '')
                entries.append(entry)

            # Non-pipe line starting with a timestamp (e.g. Content Connector logs)
            elif len(parts) <= 2:
                match = re.match(r'^(\d{4}[-/]\d{2}[-/]\d{2}[,T]?\s*\d{2}:\d{2}:\d{2})\s+(.*)$', line)
                if match:
                    entries.append({
                        'timestamp': match.group(1),
                        'message': match.group(2),
                    })
                else:
                    # Unparsed line — include raw so the calling LLM can still use it
                    entries.append({'raw': line})

        return {
            'header': header,
            'entries': entries,
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
        error_groups: Dict[tuple, Dict[str, Any]] = defaultdict(lambda: {
            'count': 0,
            'users': set(),
            'first_seen': '',
            'last_seen': '',
            'daily_distribution': Counter(),
            'example_detail': '',
        })

        service_actions = {
            'Server Start', 'Server Stop',
            'Content Connector Start', 'Content Connector Stop',
            'Migrate Start', 'Migrate Stop',
        }

        for entry in all_entries:
            action = entry.get('action', '')
            error_code = entry.get('error_code', '0')
            timestamp = entry.get('timestamp', '')
            user = entry.get('user', '')

            # Count actions
            if action:
                action_counts[action] += 1

            # Extract date from timestamp (format: "YYYY-MM-DD, HH:MM:SS" or similar)
            date_str = ''
            if timestamp:
                date_str = timestamp.split(',')[0].split('T')[0].strip()
            if date_str:
                daily_events[date_str] = daily_events.get(date_str, 0) + 1

            # Track errors (non-zero, non-empty error_code)
            is_error = error_code and error_code.strip() and error_code.strip() != '0'
            if is_error:
                code = error_code.strip()
                if date_str:
                    daily_errors[date_str] = daily_errors.get(date_str, 0) + 1
                error_by_code[code] += 1
                if action:
                    error_by_action[action] += 1

                # Build detail string
                detail_parts = []
                if entry.get('detail'):
                    detail_parts.append(entry['detail'])
                if entry.get('extra_info'):
                    detail_parts.append(entry['extra_info'])
                detail = '; '.join(detail_parts) if detail_parts else ''

                if compact:
                    # Accumulate into group
                    group_key = (code, action)
                    grp = error_groups[group_key]
                    grp['count'] += 1
                    if user and user.strip():
                        grp['users'].add(user.strip())
                    if not grp['first_seen'] or timestamp < grp['first_seen']:
                        grp['first_seen'] = timestamp
                    if not grp['last_seen'] or timestamp > grp['last_seen']:
                        grp['last_seen'] = timestamp
                    if date_str:
                        grp['daily_distribution'][date_str] += 1
                    if detail and not grp['example_detail']:
                        grp['example_detail'] = detail
                else:
                    error_entries.append({
                        'timestamp': timestamp,
                        'application': entry.get('application', ''),
                        'action': action,
                        'error_code': code,
                        'user': user,
                        'detail': detail,
                    })

            # Track user activity (skip empty/system users)
            if user and user.strip():
                user_counts[user.strip()] += 1

            # Track service events
            if action in service_actions:
                service_events.append({
                    'timestamp': timestamp,
                    'application': entry.get('application', ''),
                    'server': entry.get('server', ''),
                    'event': action,
                })

        # Build daily_activity sorted descending by date
        all_dates = sorted(set(list(daily_events.keys()) + list(daily_errors.keys())), reverse=True)
        daily_activity = [
            {'date': d, 'events': daily_events.get(d, 0), 'errors': daily_errors.get(d, 0)}
            for d in all_dates
        ]

        # Determine period
        period_from = all_dates[-1] if all_dates else ''
        period_to = all_dates[0] if all_dates else ''

        # User activity — capped at top 20 in compact mode
        top_n = 20 if compact else None
        user_activity = [
            {'user': u, 'actions': c}
            for u, c in user_counts.most_common(top_n)
        ]

        # Action counts — capped at top 20 in compact mode
        action_counts_dict = dict(action_counts.most_common(20 if compact else None))

        total_errors = sum(error_by_code.values())

        result_analysis: Dict[str, Any] = {
            'period': {'from': period_from, 'to': period_to},
            'total_entries': len(all_entries),
            'total_errors': total_errors,
            'action_counts': action_counts_dict,
            'daily_activity': daily_activity,
            'error_summary': {
                'by_code': dict(error_by_code.most_common()),
                'by_action': dict(error_by_action.most_common()),
            },
            'user_activity': user_activity,
            'service_events': service_events,
        }

        result: Dict[str, Any] = {
            'analysis': result_analysis,
            'documents': doc_metadata,
        }

        if compact:
            # Build grouped error list sorted by count descending
            grouped_errors = []
            for (code, action), grp in sorted(
                error_groups.items(), key=lambda x: x[1]['count'], reverse=True
            ):
                grouped_errors.append({
                    'error_code': code,
                    'action': action,
                    'count': grp['count'],
                    'example_detail': grp['example_detail'],
                    'users': sorted(grp['users']) if grp['users'] else ['(none)'],
                    'first_seen': grp['first_seen'],
                    'last_seen': grp['last_seen'],
                    'daily_distribution': dict(sorted(grp['daily_distribution'].items())),
                })
            result['grouped_errors'] = grouped_errors
        else:
            result['errors'] = error_entries

        return result

    @staticmethod
    def _normalize_enum_value(value: Any, mapping: Dict[str, int], field_name: str) -> Optional[int]:
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
            key = re.sub(r'[^a-z0-9]+', '', text.lower())
            if key in mapping:
                return mapping[key]
        raise ValueError(f'Invalid {field_name} value: {value}')

    def _normalize_conversion_options(self, options: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not options:
            return None

        # Normalize keys to expected WebAPI names.
        key_map = {
            'annotationmode': 'AnnotationMode',
            'convertto': 'ConvertTo',
            'signaturemode': 'SignatureMode',
            'certificatename': 'CertificateName',
            'timestamppwd': 'TimeStampPwd',
            'timestampserver': 'TimeStampServer',
            'timestampuser': 'TimeStampUser',
            'multipagestreamname': 'MultipageStreamName',
        }

        normalized: Dict[str, Any] = {}
        for k, v in options.items():
            key = re.sub(r'[^a-z0-9]+', '', str(k).lower())
            out_key = key_map.get(key, k)
            normalized[out_key] = v

        convert_to_map = {
            'original': 0,
            'singletiff': 1,
            'singlepdf': 2,
            'multipagetiff': 3,
            'multipagepdf': 4,
            'searchablepdf': 5,
            'searchablepdfa': 6,
            'jpeg': 50,
            'jpg': 50,
        }
        annotation_mode_map = {
            'default': 0,
            'merge': 1,
            'hide': 2,
        }
        signature_mode_map = {
            'nosignature': 0,
            'signatureonly': 1,
            'signatureandtimestamp': 2,
        }

        if 'ConvertTo' in normalized:
            normalized['ConvertTo'] = self._normalize_enum_value(
                normalized.get('ConvertTo'), convert_to_map, 'ConvertTo'
            )
        if 'AnnotationMode' in normalized:
            normalized['AnnotationMode'] = self._normalize_enum_value(
                normalized.get('AnnotationMode'), annotation_mode_map, 'AnnotationMode'
            )
        if 'SignatureMode' in normalized:
            normalized['SignatureMode'] = self._normalize_enum_value(
                normalized.get('SignatureMode'), signature_mode_map, 'SignatureMode'
            )

        return normalized

    @staticmethod
    def _normalize_stream_insert_mode(value: Any) -> int:
        mapping = {
            'append': 0,
            'prepend': 1,
        }
        normalized = MCPServer._normalize_enum_value(value, mapping, 'NewStreamInsertMode')
        return int(normalized) if normalized is not None else 0

    def _normalize_workflow_flags(self, value: Any) -> int:
        mapping = {
            'defaultinstances': 0,
            'runninginstances': 1,
            'finishedinstances': 2,
            'allinstances': 3,
            'errorinstances': 4,
            'overdueinstances': 8,
            'running': 1,
            'finished': 2,
            'all': 3,
            'error': 4,
            'overdue': 8,
            'default': 0,
        }
        normalized = self._normalize_enum_value(value, mapping, 'WorkflowFlags')
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
        tz_name = os.environ.get('THEREFORE_LOCAL_TZ')
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
        if text.startswith('/Date(') and text.endswith(')/'):
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
            r'^(?P<date>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})'
            r'(?P<frac>\.\d+)?'
            r'(?P<tz>Z|[+-]\d{2}:\d{2})?$'
        )
        match = pattern.match(text)
        if not match:
            return None
        base = match.group('date')
        frac = match.group('frac') or ''
        tz = match.group('tz') or '+00:00'
        if tz == 'Z':
            tz = '+00:00'
        if frac:
            # trim to microseconds (6 digits)
            frac_digits = frac[1:]
            if len(frac_digits) > 6:
                frac_digits = frac_digits[:6]
            frac = '.' + frac_digits
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
        tz_name = local_dt.tzname() or ''
        if not tz_name:
            offset = local_dt.utcoffset()
            if offset is None:
                tz_name = 'UTC'
            else:
                total_seconds = int(offset.total_seconds())
                sign = '+' if total_seconds >= 0 else '-'
                total_seconds = abs(total_seconds)
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                tz_name = f'UTC{sign}{hours:02d}:{minutes:02d}'
        return local_dt.strftime('%Y-%m-%d %H:%M:%S ') + tz_name

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
            payload.setdefault('ts', datetime.now(timezone.utc).isoformat())
            with open(path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(payload, ensure_ascii=False) + '\n')
        except Exception:
            # best-effort logging only
            return

    def _extract_user_values(self, user: Dict[str, Any]) -> List[str]:
        values = []
        for key in ('UserName', 'DisplayName', 'SMTP'):
            val = user.get(key)
            if isinstance(val, str) and val.strip():
                values.append(val.strip())
        return values

    def _resolve_user_from_query(self, query: str, tenant: str, client: ThereforeClient, flags: int = 5) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], bool]:
        query = (query or '').strip()
        if not query:
            return None, [], False
        domain_names = []
        try:
            domain_info = client.get_domain_info() or {}
            domain_names = domain_info.get('DomainNames') or []
        except Exception:
            domain_names = []

        try:
            resp = client.execute_users_query(query=query, domain_names=domain_names, flags=flags)
        except Exception:
            resp = client.execute_users_query(query=query, domain_names=None, flags=flags)

        users = resp.get('Users') or []
        if not users:
            return None, [], False

        # Score candidates by query vs user fields.
        scored = []
        for u in users:
            candidate = {
                'UserId': u.get('UserId'),
                'UserName': u.get('UserName'),
                'DisplayName': u.get('DisplayName'),
                'SMTP': u.get('SMTP'),
                'DomainName': u.get('DomainName'),
            }
            score = max(
                self._score(query, str(u.get('DisplayName') or '')),
                self._score(query, str(u.get('UserName') or '')),
                self._score(query, str(u.get('SMTP') or '')),
            )
            scored.append((score, candidate, u))

        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_candidate, best_full = scored[0]
        needs_confirmation = True
        if best_score >= 0.75 and (len(scored) == 1 or best_score - scored[1][0] >= 0.15):
            needs_confirmation = False

        candidates = []
        for score, candidate, _ in scored[:5]:
            cand = dict(candidate)
            cand['score'] = round(score, 4)
            candidates.append(cand)

        return (best_full if not needs_confirmation else best_full), candidates, needs_confirmation

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
                resp = client.get_users_from_group(group_name=name, domain_name=domain_name)
            except Exception:
                membership[name] = False
                continue
            users = resp.get('Users') or []
            found = False
            for user in users:
                if self._match_user_value(user.get('UserName'), match_values):
                    found = True
                    break
                if self._match_user_value(user.get('DisplayName'), match_values):
                    found = True
                    break
                if self._match_user_value(user.get('SMTP'), match_values):
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
        if '\\' in name:
            candidates.append(name.split('\\', 1)[1])
        if '/' in name:
            candidates.append(name.split('/', 1)[1])
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
        wf = resp.get('WorkflowInstance') or {}
        linked_docs = resp.get('LinkedDocuments') or []
        current_task = wf.get('CurrentTask') or {}

        summary['InstanceNo'] = wf.get('InstanceNo')
        summary['TokenNo'] = wf.get('TokenNo')
        summary['WorkflowNo'] = wf.get('WorkflowNo')
        summary['ProcessNo'] = wf.get('ProcessNo')
        summary['ProcessName'] = wf.get('ProcessName')
        summary['VersionNo'] = wf.get('VersionNo')

        summary['AssignedTo'] = wf.get('AssignedTo')
        summary['AssignedToUsers'] = wf.get('AssignedToUsers')
        summary['OriginallyAssignedToUsers'] = wf.get('OriginallyAssignedToUsers')
        summary['Claimed'] = wf.get('Claimed')
        summary['IsAssignedToUser'] = wf.get('IsAssignedToUser')
        summary['IsProcessOwner'] = wf.get('IsProcessOwner')

        summary['CurrTaskName'] = wf.get('CurrTaskName') or current_task.get('Name')
        summary['CurrTaskNo'] = wf.get('CurrTaskNo') or current_task.get('TaskNo')
        summary['CurrTaskType'] = wf.get('CurrTaskType') or current_task.get('Type')
        summary['CurrTaskId'] = wf.get('CurrTaskId') or current_task.get('CurrTaskId')
        summary['CurrTaskGUID'] = wf.get('CurrTaskGUID') or current_task.get('CurrTaskGUID')

        summary['TaskStartDate'] = wf.get('TaskStartDateISO8601') or wf.get('TaskStartDate')
        summary['TaskDueDate'] = wf.get('TaskDueDateISO8601') or wf.get('TaskDueDate')
        summary['ProcessStartDate'] = wf.get('ProcessStartDateISO8601') or wf.get('ProcessStartDate')
        summary['ProcessDueDate'] = wf.get('ProcessDueDateISO8601') or wf.get('ProcessDueDate')

        summary['TaskStartLocal'] = self._format_local_datetime(summary['TaskStartDate'])
        summary['TaskDueLocal'] = self._format_local_datetime(summary['TaskDueDate'])
        summary['ProcessStartLocal'] = self._format_local_datetime(summary['ProcessStartDate'])
        summary['ProcessDueLocal'] = self._format_local_datetime(summary['ProcessDueDate'])

        summary['LinkedDocumentsCount'] = len(linked_docs)
        summary['LinkedDocNos'] = [
            doc.get('DocNo') for doc in linked_docs if isinstance(doc, dict) and doc.get('DocNo') is not None
        ][:10]

        summary['ErrorString'] = wf.get('ErrorString')
        summary['ErrorInfo'] = wf.get('ErrorInfo')
        summary['ErrorTimestamp'] = wf.get('ErrorTimestampISO8601') or wf.get('ErrorTimestamp')
        summary['ErrorTimestampLocal'] = self._format_local_datetime(summary['ErrorTimestamp'])

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
            worker = getattr(thread_local, 'client', None)
            if worker is None:
                # Reuse a client per thread to avoid repeated SSL/context setup.
                worker = ThereforeClient(client.config)
                thread_local.client = worker
            return worker

        def fetch_with_timing(key: Tuple[int, int]) -> Tuple[Tuple[int, int], Optional[Dict[str, Any]], float, Optional[Exception]]:
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
            instance_no = task.get('InstanceNo')
            if instance_no is None:
                continue
            token_no = int(task.get('TokenNo') or 0)
            key = (int(instance_no), token_no)
            if key in seen:
                continue
            seen.add(key)
            keys.append(key)

        details: Dict[Tuple[int, int], Dict[str, Any]] = {}
        errors: List[Dict[str, Any]] = []
        self._debug_log(debug_log_path, {
            'event': 'instance_details_start',
            'requested': len(keys),
            'max_workers': max_workers,
        })

        if not keys:
            return details, errors

        use_workers = max(1, int(max_workers or 1))
        if use_workers <= 1 or len(keys) == 1:
            for key in keys:
                k, resp, elapsed, err = fetch_with_timing(key)
                if err:
                    errors.append({'instance_no': key[0], 'token_no': key[1], 'error': str(err)})
                elif resp is not None:
                    details[k] = resp
                if debug_log_path and len(details) % max(1, debug_progress_every) == 0:
                    self._debug_log(debug_log_path, {
                        'event': 'instance_details_progress',
                        'completed': len(details) + len(errors),
                        'loaded': len(details),
                        'failed': len(errors),
                    })
            self._debug_log(debug_log_path, {
                'event': 'instance_details_done',
                'loaded': len(details),
                'failed': len(errors),
            })
            return details, errors

        max_cap = min(use_workers, len(keys))
        current_workers = min(4, max_cap)
        min_workers = 1 if current_workers == 1 else min(2, current_workers)
        ramp_step = max(1, max_cap // 4)
        window_size = max(current_workers * 5, 50)
        ewma_latency: Optional[float] = None

        if debug_log_path:
            self._debug_log(debug_log_path, {
                'event': 'instance_details_adaptive_start',
                'max_workers_cap': max_cap,
                'initial_workers': current_workers,
                'min_workers': min_workers,
                'ramp_step': ramp_step,
            })

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
                        errors.append({'instance_no': key[0], 'token_no': key[1], 'error': str(err)})
                        window_errors += 1
                    elif resp is not None:
                        details[k] = resp
                    window_count += 1
                    window_latency += float(elapsed or 0.0)

                    if debug_log_path and (len(details) + len(errors)) % max(1, debug_progress_every) == 0:
                        self._debug_log(debug_log_path, {
                            'event': 'instance_details_progress',
                            'completed': len(details) + len(errors),
                            'loaded': len(details),
                            'failed': len(errors),
                            'current_workers': current_workers,
                        })

                should_adjust = window_count >= window_size or (not pending and not in_flight)
                if should_adjust and window_count > 0:
                    avg_latency = window_latency / max(1, window_count)
                    error_rate = window_errors / max(1, window_count)

                    if ewma_latency is None:
                        ewma_latency = avg_latency
                    else:
                        ewma_latency = (ewma_latency * 0.7) + (avg_latency * 0.3)

                    new_workers = current_workers
                    if error_rate > 0.02:
                        new_workers = max(min_workers, int(max(1, current_workers * 0.75)))
                    elif ewma_latency and avg_latency > ewma_latency * 1.5 and current_workers > min_workers:
                        new_workers = max(min_workers, current_workers - max(1, current_workers // 4))
                    elif error_rate == 0 and ewma_latency and avg_latency <= ewma_latency * 1.1 and current_workers < max_cap:
                        new_workers = min(max_cap, current_workers + ramp_step)

                    if new_workers != current_workers:
                        current_workers = new_workers
                        window_size = max(current_workers * 5, 50)
                        if debug_log_path:
                            self._debug_log(debug_log_path, {
                                'event': 'instance_details_throttle',
                                'current_workers': current_workers,
                                'error_rate': round(error_rate, 4),
                                'avg_latency_ms': int(avg_latency * 1000),
                                'ewma_latency_ms': int((ewma_latency or 0) * 1000),
                                'pending': len(pending),
                                'in_flight': len(in_flight),
                            })

                    window_count = 0
                    window_errors = 0
                    window_latency = 0.0

                while len(in_flight) < current_workers and pending:
                    submit_one()

        self._debug_log(debug_log_path, {
            'event': 'instance_details_done',
            'loaded': len(details),
            'failed': len(errors),
        })
        return details, errors

    def _attach_instance_details(
        self,
        tasks: List[Dict[str, Any]],
        details: Dict[Tuple[int, int], Dict[str, Any]],
        errors: List[Dict[str, Any]],
        detail_mode: str,
    ) -> None:
        if detail_mode not in ('summary', 'full'):
            return
        error_map = {(e.get('instance_no'), e.get('token_no')): e.get('error') for e in errors}
        for task in tasks:
            instance_no = task.get('InstanceNo')
            if instance_no is None:
                continue
            token_no = int(task.get('TokenNo') or 0)
            key = (int(instance_no), token_no)
            if key in error_map:
                task['WorkflowInstanceError'] = error_map.get(key)
            detail = details.get(key)
            if not detail:
                continue
            if detail_mode == 'full':
                task['WorkflowInstance'] = detail.get('WorkflowInstance')
                task['LinkedDocuments'] = detail.get('LinkedDocuments')
            else:
                task['WorkflowInstanceSummary'] = self._summarize_workflow_instance(detail)

    def _get_workflow_instances_core(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        debug_enabled = bool(args.get('debug', False))
        debug_log_path = args.get('debug_log_path')
        debug_progress_every = int(args.get('debug_progress_every') or 500)
        two_phase = bool(args.get('two_phase', False))
        fetch_details = bool(args.get('fetch_details', False))
        debug_info: Dict[str, Any] = {
            'workflow_query': {},
            'instance_details': {},
            'filtering': {},
        } if debug_enabled else {}
        if debug_log_path:
            self._debug_log(debug_log_path, {
                'event': 'start',
                'workflow_flags': args.get('workflow_flags'),
                'task_filter': args.get('task_filter'),
                'max_rows': args.get('max_rows'),
                'detail_mode': args.get('instance_detail_mode'),
            })
        task_filter = args.get('task_filter')
        if isinstance(task_filter, str) and task_filter.strip():
            workflow_flags = self._normalize_workflow_flags(task_filter)
        else:
            workflow_flags = self._normalize_workflow_flags(args.get('workflow_flags', 'RunningInstances'))
        if args.get('max_rows') is None:
            max_rows = self._default_workflow_max_rows(client)
        else:
            max_rows = int(args.get('max_rows'))
        filter_to_user_requested = bool(args.get('filter_to_user', True))
        filter_to_user = filter_to_user_requested
        include_unfiltered = bool(args.get('include_unfiltered', False))
        include_overdue_summary = bool(args.get('include_overdue_summary', True))
        resolve_group_membership = bool(args.get('resolve_group_membership', True))
        assignee_values = self._coerce_str_list(args.get('assignee_values')) or []
        assignee_values.extend(self.tenant_assignee_aliases.get(tenant, []))

        detail_mode = str(args.get('instance_detail_mode') or 'summary').strip().lower()
        if detail_mode not in ('none', 'summary', 'full'):
            detail_mode = 'summary'
        max_instance_workers = args.get('max_instance_workers')
        if max_instance_workers is None:
            max_instance_workers = 8 if (two_phase and fetch_details) else 4
        max_instance_workers = int(max_instance_workers)
        is_access_mask_needed = bool(args.get('is_access_mask_needed', False))
        load_history = bool(args.get('load_history', False))

        if two_phase and not fetch_details:
            detail_mode = 'none'
            filter_to_user = False

        user_query = args.get('user_query')
        user_query_flags = int(args.get('user_query_flags', 5))
        user_candidates = []
        user_needs_confirmation = False
        if isinstance(user_query, str) and user_query.strip():
            user, user_candidates, user_needs_confirmation = self._resolve_user_from_query(
                user_query, tenant, client, flags=user_query_flags
            )
            if user is None:
                user = {}
        else:
            connected = client.get_connected_user(False) or {}
            user = connected.get('User') or {}

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
            resp = client.execute_workflow_query_for_all(workflow_flags=workflow_flags, max_rows=max_rows)
        except Exception as exc:
            if debug_enabled:
                debug_info['workflow_query'] = {
                    'workflow_flags': workflow_flags,
                    'max_rows': max_rows,
                    'duration_ms': int((time.time() - start) * 1000),
                    'error': str(exc),
                }
                self._debug_log(debug_log_path, {
                    'event': 'workflow_query_error',
                    'workflow_flags': workflow_flags,
                    'max_rows': max_rows,
                    'error': str(exc),
                })
                return {'error': str(exc), 'debug': debug_info}
            raise
        if debug_enabled:
            debug_info['workflow_query'] = {
                'workflow_flags': workflow_flags,
                'max_rows': max_rows,
                'duration_ms': int((time.time() - start) * 1000),
            }
        if debug_log_path:
            self._debug_log(debug_log_path, {
                'event': 'workflow_query_done',
                'workflow_flags': workflow_flags,
                'max_rows': max_rows,
                'duration_ms': int((time.time() - start) * 1000),
            })
        tasks, user_field_labels, _ = self._extract_workflow_tasks(resp)
        max_rows_reached = len(tasks) == max_rows

        need_instance_details = detail_mode != 'none' or filter_to_user
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
                debug_info['instance_details'] = {
                    'mode': detail_mode,
                    'requested': len(tasks),
                    'loaded': len(details),
                    'failed': len(detail_errors),
                    'duration_ms': int((time.time() - details_start) * 1000),
                    'errors_sample': detail_errors[:10],
                }
            if debug_log_path:
                self._debug_log(debug_log_path, {
                    'event': 'instance_details_done',
                    'requested': len(tasks),
                    'loaded': len(details),
                    'failed': len(detail_errors),
                    'duration_ms': int((time.time() - details_start) * 1000),
                })

        # Precompute group membership for AssignedTo values.
        group_membership: Dict[str, bool] = {}
        group_candidates: List[str] = []
        if filter_to_user and resolve_group_membership and user_values and details:
            for key, detail in details.items():
                wf = (detail or {}).get('WorkflowInstance') or {}
                assigned_to = wf.get('AssignedTo')
                if not assigned_to:
                    continue
                if self._match_user_value(assigned_to, match_values):
                    continue
                for cand in self._group_name_candidates(str(assigned_to)):
                    if cand not in group_candidates:
                        group_candidates.append(cand)
            if group_candidates:
                domain_name = user.get('DomainName') if isinstance(user, dict) else None
                group_membership = self._resolve_group_membership(
                    client, group_candidates, user_values, domain_name=domain_name
                )

        filtered_tasks = tasks
        filter_applied = False
        unresolved_instances: List[Dict[str, Any]] = []
        if filter_to_user:
            if not match_values and not user.get('UserId') and not user.get('UserNo'):
                filtered_tasks = tasks
                filter_applied = False
            elif not details:
                filtered_tasks = []
                filter_applied = True
            else:
                user_id = user.get('UserId') or user.get('UserNo') or user.get('UserID')
                try:
                    user_id = int(user_id) if user_id is not None else None
                except (TypeError, ValueError):
                    user_id = None
                filtered = []
                for task in tasks:
                    instance_no = task.get('InstanceNo')
                    if instance_no is None:
                        continue
                    token_no = int(task.get('TokenNo') or 0)
                    key = (int(instance_no), token_no)
                    detail = details.get(key)
                    if not detail:
                        unresolved_instances.append({'instance_no': key[0], 'token_no': key[1]})
                        continue
                    wf = (detail or {}).get('WorkflowInstance') or {}
                    matched = False
                    if not user_query and wf.get('IsAssignedToUser') is True:
                        matched = True
                    if not matched and user_id is not None:
                        assigned_users = self._coerce_int_list(wf.get('AssignedToUsers'))
                        if user_id in assigned_users:
                            matched = True
                    if not matched:
                        assigned_to = wf.get('AssignedTo')
                        if assigned_to and self._match_user_value(assigned_to, match_values):
                            matched = True
                        elif assigned_to and resolve_group_membership and group_membership:
                            if group_membership.get(assigned_to):
                                matched = True
                            else:
                                for cand in self._group_name_candidates(str(assigned_to)):
                                    if group_membership.get(cand):
                                        matched = True
                                        break
                    if matched:
                        filtered.append(task)
                filtered_tasks = filtered
                filter_applied = True
        if debug_enabled:
            debug_info['filtering'] = {
                'filter_to_user': filter_to_user,
                'filter_to_user_requested': filter_to_user_requested,
                'filter_applied': filter_applied,
                'total_tasks': len(tasks),
                'filtered_tasks': len(filtered_tasks),
                'user_id': user.get('UserId') or user.get('UserNo') or user.get('UserID'),
                'match_values_count': len(match_values),
                'group_candidates': len(group_candidates),
                'group_matches': len([k for k, v in group_membership.items() if v]),
                'unresolved_instances': len(unresolved_instances),
            }
        if debug_log_path:
            self._debug_log(debug_log_path, {
                'event': 'filtering_done',
                'filter_to_user': filter_to_user,
                'filter_applied': filter_applied,
                'total_tasks': len(tasks),
                'filtered_tasks': len(filtered_tasks),
                'group_candidates': len(group_candidates),
                'group_matches': len([k for k, v in group_membership.items() if v]),
                'unresolved_instances': len(unresolved_instances),
            })

        if need_instance_details and detail_mode in ('summary', 'full'):
            self._attach_instance_details(filtered_tasks, details, detail_errors, detail_mode)
            if include_unfiltered and filtered_tasks != tasks:
                self._attach_instance_details(tasks, details, detail_errors, detail_mode)

        output: Dict[str, Any] = {
            'user': user,
            'user_query': user_query,
            'user_candidates': user_candidates,
            'user_needs_confirmation': user_needs_confirmation,
            'workflow_flags': workflow_flags,
            'task_filter': task_filter,
            'max_rows': max_rows,
            'max_rows_reached': max_rows_reached,
            'total_count': len(tasks),
            'filter_to_user': filter_to_user,
            'filter_to_user_requested': filter_to_user_requested,
            'filter_applied': filter_applied,
            'assignee_values': assignee_values,
            'user_field_labels': user_field_labels,
            'group_membership_matches': [k for k, v in group_membership.items() if v],
            'instance_detail_mode': detail_mode,
            'instance_details_requested': need_instance_details,
            'instance_details_loaded': len(details),
            'instance_details_failed': len(detail_errors),
            'instance_detail_errors': detail_errors,
            'unresolved_instances': unresolved_instances,
            'task_count': len(filtered_tasks),
            'instances': filtered_tasks,
            'two_phase': two_phase,
            'fetch_details': fetch_details,
            'suggested_max_instance_workers': 8 if two_phase and not fetch_details else None,
            'debug': debug_info if debug_enabled else None,
        }
        if debug_log_path:
            self._debug_log(debug_log_path, {
                'event': 'done',
                'task_count': len(filtered_tasks),
                'max_rows_reached': max_rows_reached,
                'note': output.get('note'),
            })

        overdue_keys = set()
        if include_overdue_summary:
            overdue_resp = client.execute_workflow_query_for_all(
                workflow_flags=self._normalize_workflow_flags('overdue'),
                max_rows=max_rows,
            )
            overdue_all, _, _ = self._extract_workflow_tasks(overdue_resp)
            overdue_keys = {self._task_key(t) for t in overdue_all}

            on_schedule = 0
            overdue = 0
            for task in filtered_tasks:
                key = self._task_key(task)
                is_overdue = key in overdue_keys
                task['IsOverdue'] = is_overdue
                task['ScheduleStatus'] = 'overdue' if is_overdue else 'on_schedule'
                if is_overdue:
                    overdue += 1
                else:
                    on_schedule += 1

            output['overdue_count'] = overdue
            output['on_schedule_count'] = on_schedule
            output['overdue_tasks_count'] = overdue
            if overdue > 0:
                output['highlight'] = {
                    'message': f'{overdue} overdue task(s) found.',
                    'overdue_count': overdue,
                    'on_schedule_count': on_schedule,
                }

        if include_unfiltered and filtered_tasks != tasks:
            output['all_tasks_count'] = len(tasks)
            output['all_tasks'] = tasks

        if two_phase and not fetch_details:
            output['note'] = 'Two-phase mode: returning overall counts only. Re-run with fetch_details=true to filter by assignment.'
        elif filter_to_user and not match_values and not user.get('UserId') and not user.get('UserNo'):
            output['note'] = 'No assignee values available for filtering.'
        elif filter_to_user and filter_applied and not filtered_tasks and tasks:
            output['note'] = 'No tasks matched the user assignment from GetWorkflowInstance.'
        if max_rows_reached:
            output['note'] = 'Reached max_rows; results may be truncated. Increase max_rows to fetch more.'

        return output

    @staticmethod
    def _task_key(task: Dict[str, Any]) -> Tuple[Any, Any, Any]:
        return (
            task.get('InstanceNo'),
            task.get('TokenNo'),
            task.get('WorkflowNo'),
        )

    def _extract_workflow_tasks(self, resp: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str], set]:
        results = resp.get('WorkflowQueryResultList') or []
        tasks: List[Dict[str, Any]] = []
        user_field_indexes = set()
        user_field_labels: List[str] = []
        user_field_pattern = re.compile(r'(user|assignee|assigned|owner)', re.IGNORECASE)

        for result in results:
            columns = result.get('Columns') or []
            col_labels: List[str] = []
            for col in columns:
                label = col.get('Caption') or col.get('IndexDataFieldName') or col.get('ColName') or ''
                col_labels.append(label)

            for idx, label in enumerate(col_labels):
                if label and user_field_pattern.search(label):
                    user_field_indexes.add(idx)
                    if label not in user_field_labels:
                        user_field_labels.append(label)

            for row in (result.get('ResultRows') or []):
                row_values = row.get('IndexValues') or []
                mapped = {}
                for idx, label in enumerate(col_labels):
                    if idx < len(row_values):
                        mapped[label] = row_values[idx]
                entry = {
                    'CaseDefNo': result.get('CaseDefNo'),
                    'CaseDefName': result.get('CaseDefName'),
                    'CategoryNo': result.get('CategoryNo'),
                    'CategoryName': result.get('CategoryName'),
                    'ProcessNo': result.get('ProcessNo'),
                    'ProcessName': result.get('ProcessName'),
                    'WorkflowNo': row.get('WorkflowNo'),
                    'InstanceNo': row.get('InstanceNo'),
                    'TokenNo': row.get('TokenNo'),
                    'Status': row.get('Status'),
                    'IndexValues': mapped,
                }
                for key in ('DocNo', 'VersionNo', 'Size'):
                    if key in row:
                        entry[key] = row.get(key)
                tasks.append(entry)

        return tasks, user_field_labels, user_field_indexes

    def _get_my_workflow_tasks(self, args: Dict[str, Any], tenant: str, client: ThereforeClient) -> Dict[str, Any]:
        args = dict(args or {})
        if args.get('filter_to_user') is None:
            args['filter_to_user'] = True
        output = self._get_workflow_instances_core(args, tenant, client)
        # preserve legacy key
        output['tasks'] = output.get('instances', [])
        return output

    def _normalize_statistics_query_type(self, value: Any) -> int:
        mapping = {
            'undefined': 0,
            'workflowinstancesbyprocess': 100,
            'workflowinstancesbytask': 101,
            'workflowinstancesrunningbyprocess': 102,
            'workflowinstancesrunningbytask': 103,
            'workflowinstancesfinishedbyprocess': 104,
            'workflowinstancesfinishedbytask': 105,
            'workflowoverdueinstancesbyprocess': 106,
            'workflowoverdueinstancesbytask': 107,
            'workflowerrorinstancesbyprocess': 108,
            'workflowerrorinstancesbytask': 109,
            'documentscreatedbycategory': 200,
            'documentscheckedoutbycategory': 201,
            'documentscreatedtodaybycategory': 202,
            'documentscreatedthisweekbycategory': 203,
            'documentscreatedthismonthbycategory': 204,
            'documentscreatedthisyearbycategory': 205,
            'documentscreatedlastweekbycategory': 206,
            'documentscreatedlastmonthbycategory': 207,
            'documentscreatedlastyearbycategory': 208,
            'taskstodo': 400,
            'tasksstarted': 401,
            'tasksdone': 402,
            'tasksallbystate': 403,
            'tasksoverduetodo': 404,
            'tasksoverduestarted': 405,
        }
        normalized = self._normalize_enum_value(value, mapping, 'QueryType')
        if normalized is None:
            raise ValueError('QueryType is required')
        return int(normalized)

    def _cache_path(self, template: str, tenant: str) -> str:
        safe = re.sub(r'[^a-z0-9]+', '_', tenant.lower()) or 'default'
        return template.format(tenant=safe)

    def _get_cached_categories(self, tenant: str, client: ThereforeClient) -> List[Dict[str, Any]]:
        now = time.time()
        if tenant in self._category_cache and (now - self._category_cache_ts.get(tenant, 0)) < self._category_cache_ttl:
            return self._category_cache[tenant]['items']

        cache_path = self._cache_path(self._category_cache_path, tenant)
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            if (now - cached.get('ts', 0)) < self._category_cache_ttl:
                self._category_cache[tenant] = cached
                self._category_cache_ts[tenant] = cached.get('ts', now)
                return cached.get('items') or []
        except Exception:
            pass

        tree = client.get_categories_tree({})
        items = tree.get('TreeItems') or []
        flat = self._flatten_tree(items)
        payload = {'ts': now, 'items': flat}
        self._category_cache[tenant] = payload
        self._category_cache_ts[tenant] = now
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        return flat

    def _get_cached_keyword_dictionaries(self, tenant: str, client: ThereforeClient) -> List[Dict[str, Any]]:
        now = time.time()
        if tenant in self._keyword_dict_cache and (now - self._keyword_dict_cache_ts.get(tenant, 0)) < self._keyword_dict_cache_ttl:
            return self._keyword_dict_cache[tenant]['items']

        cache_path = self._cache_path(self._keyword_dict_cache_path, tenant)
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            if (now - cached.get('ts', 0)) < self._keyword_dict_cache_ttl:
                self._keyword_dict_cache[tenant] = cached
                self._keyword_dict_cache_ts[tenant] = cached.get('ts', now)
                return cached.get('items') or []
        except Exception:
            pass

        resp = client.get_objects(flags=0, obj_type=22)
        items = self._extract_object_items(resp)
        payload = {'ts': now, 'items': items}
        self._keyword_dict_cache[tenant] = payload
        self._keyword_dict_cache_ts[tenant] = now
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass
        return items

    def _get_cached_fields(self, tenant: str, category_no: int, client: ThereforeClient) -> List[Dict[str, Any]]:
        now = time.time()
        tenant_cache = self._field_cache.setdefault(tenant, {})
        tenant_ts = self._field_cache_ts.setdefault(tenant, {})

        if category_no in tenant_cache and (now - tenant_ts.get(category_no, 0)) < self._field_cache_ttl:
            return tenant_cache[category_no]['fields']

        cache_path = self._cache_path(self._field_cache_path, tenant)
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            item = cached.get(str(category_no))
            if item and (now - item.get('ts', 0)) < self._field_cache_ttl:
                tenant_cache[category_no] = item
                tenant_ts[category_no] = item.get('ts', now)
                return item.get('fields') or []
        except Exception:
            pass

        info = client.get_category_info(category_no)
        fields = info.get('CategoryFields') or []
        payload = {'ts': now, 'fields': fields}
        tenant_cache[category_no] = payload
        tenant_ts[category_no] = now

        disk_cache = {}
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                disk_cache = json.load(f)
        except Exception:
            disk_cache = {}
        disk_cache[str(category_no)] = payload
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(disk_cache, f, indent=2)
        except Exception:
            pass
        return fields


def _parse_aliases(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r'[;,]+', raw) if p.strip()]
    return parts


def load_clients() -> Tuple[Dict[str, ThereforeClient], Optional[str], Dict[str, str], Dict[str, List[str]]]:
    default_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env.local')
    env_path = os.environ.get('THEREFORE_ENV_PATH', default_env_path)
    env_values = load_env(env_path)
    configs, default_tenant, tenant_labels = build_tenant_configs_from_env(env_values)
    clients: Dict[str, ThereforeClient] = {}
    tenant_aliases: Dict[str, List[str]] = {}
    for key, cfg in configs.items():
        if not cfg.base_url:
            raise RuntimeError(f'THEREFORE_BASE_URL is required for tenant {tenant_labels.get(key, key)}')
        clients[key] = ThereforeClient(cfg)
        label = tenant_labels.get(key, key)
        prefix = f"THEREFORE_{str(label).upper()}_"
        raw = (
            env_values.get(prefix + 'ASSIGNEE_ALIASES')
            or env_values.get(prefix + 'USER_GROUPS')
            or env_values.get('THEREFORE_ASSIGNEE_ALIASES')
            or env_values.get('THEREFORE_USER_GROUPS')
        )
        tenant_aliases[key] = _parse_aliases(raw)
    return clients, default_tenant, tenant_labels, tenant_aliases


def run_stdio_mode(server: 'MCPServer') -> None:
    """Run the server in stdio mode (MCP standard)."""
    while True:
        try:
            msg = _read_message()
        except json.JSONDecodeError as e:
            _write_message(_error_response(None, -32700, f'Parse error: {e}'))
            continue
        if msg is None:
            break
        response = server.handle(msg)
        if response is not None:
            _write_message(response)


def _build_http_app(server: 'MCPServer') -> 'FastAPI':
    """Build the FastAPI app for HTTP mode with MCP SSE transport."""
    if not HAS_FASTAPI:
        raise RuntimeError("FastAPI is required for HTTP mode. Install with: pip install fastapi uvicorn")

    app = FastAPI(title="Therefore MCP HTTP Server")

    # Bearer token auth — skip for health check
    auth_token = os.environ.get('THEREFORE_MCP_AUTH_TOKEN', '').strip()
    if auth_token:
        @app.middleware("http")
        async def check_auth(request: Request, call_next):
            if request.url.path == "/health":
                return await call_next(request)
            header = request.headers.get("authorization", "").strip()
            scheme, _, token = header.partition(" ")
            if scheme.lower() == "bearer" and token.strip() == auth_token:
                return await call_next(request)
            return JSONResponse(
                {"error": "Unauthorized"},
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
    _mcp_sessions: Dict[str, Optional[asyncio.Queue]] = {}  # session_id -> queue or None

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
                _error_response(None, -32000, "Accept header must include text/event-stream"),
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


def run_http_mode(server: 'MCPServer', host: str, port: int) -> None:
    """Run the server in HTTP-only mode using FastAPI."""
    app = _build_http_app(server)
    auth_enabled = bool(os.environ.get('THEREFORE_MCP_AUTH_TOKEN', '').strip())
    print(f"Starting Therefore MCP server in HTTP mode on {host}:{port}", file=sys.stderr)
    print(f"Auth: {'Bearer token' if auth_enabled else 'NONE (set THEREFORE_MCP_AUTH_TOKEN to enable)'}", file=sys.stderr)
    print(f"Access at: http://{host}:{port}", file=sys.stderr)
    print(f"Health check: http://{host}:{port}/health", file=sys.stderr)
    uvicorn.run(app, host=host, port=port, log_level="info")


def _start_http_background(server: 'MCPServer', host: str, port: int) -> None:
    """Start the HTTP server in a daemon thread (for dual stdio+http mode)."""
    app = _build_http_app(server)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    uv_server = uvicorn.Server(config)
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    print(f"HTTP server started on {host}:{port}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Therefore MCP Server")
    parser.add_argument(
        "--stdio",
        action="store_true",
        default=False,
        help="Run in stdio mode (default if no mode specified)"
    )
    parser.add_argument(
        "--http",
        type=int,
        metavar="PORT",
        help="Run in HTTP mode on specified port (e.g., --http 8000)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="HTTP host to bind to (default: 0.0.0.0)"
    )

    args = parser.parse_args()

    # Load clients and create server
    clients, default_tenant, tenant_labels, tenant_aliases = load_clients()
    server = MCPServer(clients, default_tenant, tenant_labels, tenant_aliases)

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


if __name__ == '__main__':
    main()
