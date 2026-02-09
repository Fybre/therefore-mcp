#!/usr/bin/env python3
import base64
import json
import os
import re
import sys
import traceback
import difflib
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

from therefore_client import (
    ThereforeClient,
    build_tenant_configs_from_env,
    load_env,
    normalize_tenant_key,
)


def _read_message() -> Optional[Dict[str, Any]]:
    """Read LSP-style framed JSON message from stdin."""
    header_bytes = b""
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        header_bytes += line
    headers = header_bytes.decode('utf-8', errors='replace').splitlines()
    content_length = None
    for h in headers:
        if h.lower().startswith('content-length:'):
            content_length = int(h.split(':', 1)[1].strip())
            break
    if content_length is None:
        return None
    body = sys.stdin.buffer.read(content_length)
    if not body:
        return None
    return json.loads(body.decode('utf-8', errors='replace'))


def _write_message(payload: Dict[str, Any]) -> None:
    data = json.dumps(payload).encode('utf-8')
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode('utf-8'))
    sys.stdout.buffer.write(data)
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
            "description": "List fields for a category with key metadata (FieldNo, Caption, FieldID, etc.).",
            "inputSchema": {
                "type": "object",
                "required": ["category_no"],
                "properties": {
                    "category_no": {"type": "integer"}
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
            "description": "Get category definition and field metadata by category number.",
            "inputSchema": {
                "type": "object",
                "required": ["category_no"],
                "properties": {
                    "category_no": {"type": "integer"}
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
            "description": "Query users by name or other text.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "domain_names": {"type": "array", "items": {"type": "string"}},
                    "flags": {"type": "integer", "default": 0}
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
            "description": "List keywords for a keyword field (dictionary) by field number.",
            "inputSchema": {
                "type": "object",
                "required": ["field_no"],
                "properties": {
                    "field_no": {"type": "integer"},
                    "category_no": {"type": "integer"},
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
            "description": "Validate keywords for a keyword field; returns invalid keywords.",
            "inputSchema": {
                "type": "object",
                "required": ["field_no", "keywords"],
                "properties": {
                    "field_no": {"type": "integer"},
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
                    "check_existing": {"type": "boolean", "default": true},
                    "ignore_if_exists": {"type": "boolean", "default": true},
                    "include_deactivated_keywords": {"type": "boolean", "default": true}
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
                    "check_existing": {"type": "boolean", "default": true},
                    "ignore_if_exists": {"type": "boolean", "default": true},
                    "include_deactivated_keywords": {"type": "boolean", "default": true}
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
                    "include_deactivated_keywords": {"type": "boolean", "default": true}
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
                    "include_deactivated_keywords": {"type": "boolean", "default": true}
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
                    "max_rows": {"type": "integer", "description": "Optional override; defaults to THEREFORE_WORKFLOW_MAX_ROWS or 1000."}
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
                    "max_rows": {"type": "integer", "description": "Optional override; defaults to THEREFORE_WORKFLOW_MAX_ROWS or 1000."}
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
            "description": "List workflow tasks for the connected user. Defaults to running instances.",
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
                    "user_query_flags": {"type": "integer", "default": 5}
                }
            },
        },
        {
            "name": "execute_single_query",
            "description": "Execute a single query. If the query contains multiple category numbers, automatically runs an async multi-query and returns merged results.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "object"},
                    "full_text": {"type": "string"}
                }
            },
        },
        {
            "name": "execute_async_single_query",
            "description": "Execute an async single query with batching. If auto_fetch_all=true, fetches all rows and releases the query.",
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "object"},
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
            "description": "Execute a full-text search query.",
            "inputSchema": {
                "type": "object",
                "required": ["search"],
                "properties": {
                    "search": {"type": "string"},
                    "categories": {"type": "array", "items": {"type": "integer"}},
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
            "description": "Execute an async multi-query with batching. If auto_fetch_all=true, fetches all rows and releases the query.",
            "inputSchema": {
                "type": "object",
                "required": ["queries"],
                "properties": {
                    "queries": {"type": "array", "items": {"type": "object"}},
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
            "description": "Create a document using the web-client flow (GetCategoryInfo -> PreprocessIndexData -> EvaluateConditionalProperties -> CreateDocument). Default auto-append mode is 0.",
            "inputSchema": {
                "type": "object",
                "required": ["category_no"],
                "properties": {
                    "category_no": {"type": "integer"},
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
            "description": "Update index fields for a document (uses SaveDocumentIndexData).",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"},
                    "updates": {
                        "type": "array",
                        "description": "List of field updates by field number.",
                        "items": {
                            "type": "object",
                            "required": ["value"],
                            "anyOf": [
                                {"required": ["field_no"]},
                                {"required": ["field_name"]}
                            ],
                            "properties": {
                                "field_no": {"type": "integer"},
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
            "description": "Update a document's streams and/or index data (uses UpdateDocument).",
            "inputSchema": {
                "type": "object",
                "required": ["doc_no"],
                "properties": {
                    "doc_no": {"type": "integer"},
                    "updates": {
                        "type": "array",
                        "description": "Optional index field updates by field number.",
                        "items": {
                            "type": "object",
                            "required": ["value"],
                            "anyOf": [
                                {"required": ["field_no"]},
                                {"required": ["field_name"]}
                            ],
                            "properties": {
                                "field_no": {"type": "integer"},
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
        self._category_cache: Dict[str, Dict[str, Any]] = {}
        self._category_cache_ts: Dict[str, float] = {}
        self._category_cache_ttl: int = 300
        self._category_cache_path = '/Volumes/DataSSD/source/therefore-mcp/docs/notes/category_cache_{tenant}.json'
        self._field_cache: Dict[str, Dict[int, Dict[str, Any]]] = {}
        self._field_cache_ts: Dict[str, Dict[int, float]] = {}
        self._field_cache_ttl: int = 300
        self._field_cache_path = '/Volumes/DataSSD/source/therefore-mcp/docs/notes/field_cache_{tenant}.json'
        self._keyword_dict_cache: Dict[str, Dict[str, Any]] = {}
        self._keyword_dict_cache_ts: Dict[str, float] = {}
        self._keyword_dict_cache_ttl: int = 300
        self._keyword_dict_cache_path = '/Volumes/DataSSD/source/therefore-mcp/docs/notes/keyword_dictionary_cache_{tenant}.json'

    def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = msg.get('method')
        msg_id = msg.get('id')
        params = msg.get('params') or {}

        if method == 'initialize':
            return _result_response(msg_id, {
                'protocolVersion': '2024-11-05',
                'capabilities': {
                    'tools': {'listChanged': False}
                },
                'serverInfo': {
                    'name': 'therefore-mcp',
                    'version': '0.1.0'
                }
            })
        if method == 'initialized':
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

        return _error_response(msg_id, -32601, f"Method not found: {method}")

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
            return client.get_objects(
                flags=int(args['flags']),
                obj_type=int(args['obj_type']),
            )
        if name == 'execute_users_query':
            return client.execute_users_query(
                query=args['query'],
                domain_names=args.get('domain_names'),
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
            if args.get('max_rows') is None:
                max_rows = int(client.config.workflow_max_rows or 1000)
            else:
                max_rows = int(args.get('max_rows', 1000))
            return client.execute_workflow_query_for_all(
                workflow_flags=self._normalize_workflow_flags(args.get('workflow_flags', 0)),
                max_rows=max_rows,
            )
        if name == 'execute_workflow_query_for_process':
            if args.get('max_rows') is None:
                max_rows = int(client.config.workflow_max_rows or 1000)
            else:
                max_rows = int(args.get('max_rows', 1000))
            return client.execute_workflow_query_for_process(
                process_no=int(args['process_no']),
                workflow_flags=self._normalize_workflow_flags(args.get('workflow_flags', 0)),
                max_rows=max_rows,
            )
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

        raise ValueError(f'Unknown tool: {name}')

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
        task_filter = args.get('task_filter')
        if isinstance(task_filter, str) and task_filter.strip():
            workflow_flags = self._normalize_workflow_flags(task_filter)
        else:
            workflow_flags = self._normalize_workflow_flags(args.get('workflow_flags', 'RunningInstances'))
        if args.get('max_rows') is None:
            max_rows = int(client.config.workflow_max_rows or 1000)
        else:
            max_rows = int(args.get('max_rows'))
        filter_to_user = bool(args.get('filter_to_user', True))
        include_unfiltered = bool(args.get('include_unfiltered', False))
        include_overdue_summary = bool(args.get('include_overdue_summary', True))
        resolve_group_membership = bool(args.get('resolve_group_membership', True))
        assignee_values = self._coerce_str_list(args.get('assignee_values')) or []
        assignee_values.extend(self.tenant_assignee_aliases.get(tenant, []))

        user_query = args.get('user_query')
        user_query_flags = int(args.get('user_query_flags', 5))
        user_candidates = []
        user_needs_confirmation = False
        if isinstance(user_query, str) and user_query.strip():
            user, user_candidates, user_needs_confirmation = self._resolve_user_from_query(user_query, tenant, client, flags=user_query_flags)
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

        resp = client.execute_workflow_query_for_all(workflow_flags=workflow_flags, max_rows=max_rows)
        tasks, user_field_labels, user_field_indexes = self._extract_workflow_tasks(resp)

        filtered_tasks = tasks
        filter_applied = False
        group_membership = {}
        if filter_to_user and match_values and user_field_indexes:
            # precompute group membership for any assignee values that don't directly match the user
            if resolve_group_membership:
                assignee_values_found = []
                for task in tasks:
                    values = list(task.get('IndexValues', {}).values())
                    for idx in user_field_indexes:
                        if idx < len(values):
                            val = values[idx]
                            if val is None:
                                continue
                            text = str(val).strip()
                            if not text:
                                continue
                            if self._match_user_value(text, match_values):
                                continue
                            if text not in assignee_values_found:
                                assignee_values_found.append(text)
                if assignee_values_found:
                    group_membership = self._resolve_group_membership(client, assignee_values_found, match_values)

            filtered = []
            for task in tasks:
                values = list(task.get('IndexValues', {}).values())
                matched = False
                for idx in user_field_indexes:
                    if idx < len(values) and self._match_user_value(values[idx], match_values):
                        matched = True
                        break
                    if idx < len(values) and resolve_group_membership:
                        val = values[idx]
                        if val is not None:
                            text = str(val).strip()
                            if text and group_membership.get(text):
                                matched = True
                                break
                if matched:
                    filtered.append(task)
            filtered_tasks = filtered
            filter_applied = True

        output = {
            'user': user,
            'user_query': user_query,
            'user_candidates': user_candidates,
            'user_needs_confirmation': user_needs_confirmation,
            'workflow_flags': workflow_flags,
            'task_filter': task_filter,
            'max_rows': max_rows,
            'filter_to_user': filter_to_user,
            'filter_applied': filter_applied,
            'assignee_values': assignee_values,
            'user_field_labels': user_field_labels,
            'group_membership_matches': [k for k, v in group_membership.items() if v],
            'task_count': len(filtered_tasks),
            'tasks': filtered_tasks,
        }

        overdue_tasks = None
        overdue_keys = set()
        if include_overdue_summary:
            if workflow_flags == self._normalize_workflow_flags('overdue'):
                overdue_tasks = filtered_tasks
                overdue_keys = {self._task_key(t) for t in filtered_tasks}
            else:
                overdue_resp = client.execute_workflow_query_for_all(
                    workflow_flags=self._normalize_workflow_flags('overdue'),
                    max_rows=max_rows,
                )
                overdue_all, _, overdue_user_field_indexes = self._extract_workflow_tasks(overdue_resp)
                overdue_filtered = overdue_all
                if filter_to_user and match_values and overdue_user_field_indexes:
                    group_membership_overdue = {}
                    if resolve_group_membership:
                        assignee_values_found = []
                        for task in overdue_all:
                            values = list(task.get('IndexValues', {}).values())
                            for idx in overdue_user_field_indexes:
                                if idx < len(values):
                                    val = values[idx]
                                    if val is None:
                                        continue
                                    text = str(val).strip()
                                    if not text:
                                        continue
                                    if self._match_user_value(text, match_values):
                                        continue
                                    if text not in assignee_values_found:
                                        assignee_values_found.append(text)
                        if assignee_values_found:
                            group_membership_overdue = self._resolve_group_membership(client, assignee_values_found, match_values)
                    tmp = []
                    for task in overdue_all:
                        values = list(task.get('IndexValues', {}).values())
                        matched = False
                        for idx in overdue_user_field_indexes:
                            if idx < len(values) and self._match_user_value(values[idx], match_values):
                                matched = True
                                break
                            if idx < len(values) and resolve_group_membership:
                                val = values[idx]
                                if val is not None:
                                    text = str(val).strip()
                                    if text and group_membership_overdue.get(text):
                                        matched = True
                                        break
                        if matched:
                            tmp.append(task)
                    overdue_filtered = tmp
                overdue_tasks = overdue_filtered
                overdue_keys = {self._task_key(t) for t in overdue_filtered}

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
            if overdue_tasks is not None:
                output['overdue_tasks_count'] = len(overdue_tasks)
            if overdue > 0:
                output['highlight'] = {
                    'message': f'{overdue} overdue task(s) found.',
                    'overdue_count': overdue,
                    'on_schedule_count': on_schedule,
                }

        if include_unfiltered and filtered_tasks != tasks:
            output['all_tasks_count'] = len(tasks)
            output['all_tasks'] = tasks

        if filter_to_user and match_values and not user_field_indexes:
            output['note'] = 'No user-related columns detected; returning unfiltered tasks.'
        elif filter_to_user and not match_values:
            output['note'] = 'No assignee values available for filtering.'
        elif filter_to_user and user_field_indexes and filter_applied and not filtered_tasks and tasks:
            output['note'] = 'No tasks matched the user/assignee values. Group membership was checked; consider verifying the assignee value or user selection.'

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
    env_path = os.environ.get('THEREFORE_ENV_PATH', '/Volumes/DataSSD/source/therefore-mcp/docs/reference/user/.env.local')
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


def main() -> None:
    clients, default_tenant, tenant_labels, tenant_aliases = load_clients()
    server = MCPServer(clients, default_tenant, tenant_labels, tenant_aliases)

    reload_flag = {'requested': False, 'reason': None}
    reload_mode = str(os.environ.get('THEREFORE_MCP_HOT_RELOAD', '')).lower().strip()
    if reload_mode in ('1', 'true', 'yes', 'restart', 'exit', 'quit'):
        reload_mode = 'restart' if reload_mode in ('1', 'true', 'yes', 'restart') else 'exit'
        watch_files = [
            os.path.abspath(__file__),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'therefore_client.py'),
        ]

        def watch_files_for_changes() -> None:
            mtimes: Dict[str, Optional[float]] = {}
            for path in watch_files:
                try:
                    mtimes[path] = os.path.getmtime(path)
                except OSError:
                    mtimes[path] = None
            while True:
                time.sleep(1.0)
                for path in watch_files:
                    try:
                        current = os.path.getmtime(path)
                    except OSError:
                        current = None
                    previous = mtimes.get(path)
                    if previous is None and current is None:
                        continue
                    if previous is None and current is not None:
                        reload_flag['requested'] = True
                        reload_flag['reason'] = path
                        return
                    if current is None and previous is not None:
                        reload_flag['requested'] = True
                        reload_flag['reason'] = path
                        return
                    if current is not None and previous is not None and current > previous:
                        reload_flag['requested'] = True
                        reload_flag['reason'] = path
                        return
                    mtimes[path] = current

        thread = threading.Thread(target=watch_files_for_changes, daemon=True)
        thread.start()
    else:
        reload_mode = ''

    while True:
        if reload_flag['requested'] and reload_mode:
            reason = reload_flag.get('reason') or 'unknown file'
            print(f'[therefore-mcp] Hot reload triggered by change: {reason}', file=sys.stderr)
            if reload_mode == 'restart':
                sys.stderr.flush()
                sys.stdout.flush()
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
            break
        msg = _read_message()
        if msg is None:
            break
        response = server.handle(msg)
        if response is not None:
            _write_message(response)
        if reload_flag['requested'] and reload_mode:
            reason = reload_flag.get('reason') or 'unknown file'
            print(f'[therefore-mcp] Hot reload triggered by change: {reason}', file=sys.stderr)
            if reload_mode == 'restart':
                sys.stderr.flush()
                sys.stdout.flush()
                os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)] + sys.argv[1:])
            break


if __name__ == '__main__':
    main()
