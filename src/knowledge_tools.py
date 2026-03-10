#!/usr/bin/env python3
"""
Therefore API Knowledge Tools

MCP tools that expose Therefore API knowledge and examples to AI assistants.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load knowledge base
DOCS_DIR = Path(__file__).parent.parent / "docs"
KNOWLEDGE_BASE_PATH = DOCS_DIR / "knowledge-base.json"

_KNOWLEDGE_BASE: Optional[Dict[str, Any]] = None


def load_knowledge_base() -> Dict[str, Any]:
    """Load the Therefore API knowledge base."""
    global _KNOWLEDGE_BASE
    if _KNOWLEDGE_BASE is None:
        with open(KNOWLEDGE_BASE_PATH, 'r', encoding='utf-8') as f:
            _KNOWLEDGE_BASE = json.load(f)
    return _KNOWLEDGE_BASE


def get_workflow_guide(workflow_name: str) -> Dict[str, Any]:
    """
    Get step-by-step workflow guide for common Therefore operations.

    Args:
        workflow_name: Workflow identifier (e.g., 'query_documents_with_filter')

    Returns:
        Complete workflow with steps, templates, and examples

    Example:
        >>> guide = get_workflow_guide('query_documents_with_filter')
        >>> for step in guide['steps']:
        ...     print(f"Step {step['step']}: {step['description']}")
    """
    kb = load_knowledge_base()
    workflows = kb.get('workflows', {})

    if workflow_name not in workflows:
        available = list(workflows.keys())
        return {
            'error': f'Workflow "{workflow_name}" not found',
            'available_workflows': available
        }

    return workflows[workflow_name]


def get_field_type_info(field_type: str | int) -> Dict[str, Any]:
    """
    Get information about Therefore field types and their data structures.

    Args:
        field_type: Field type number (0-9) or name (e.g., 'StringField')

    Returns:
        Field type details, structure, example, and validation rules

    Example:
        >>> info = get_field_type_info(0)
        >>> print(info['name'])  # 'StringField'
        >>> print(info['index_data_type'])  # 'StringIndexData'
    """
    kb = load_knowledge_base()
    field_types = kb.get('field_types', {})

    # Convert name to number if needed
    if isinstance(field_type, str):
        for num, info in field_types.items():
            if info.get('name') == field_type:
                field_type = num
                break

    field_type_str = str(field_type)
    if field_type_str not in field_types:
        return {
            'error': f'Field type "{field_type}" not found',
            'available_types': {k: v['name'] for k, v in field_types.items()}
        }

    return field_types[field_type_str]


def get_common_pattern(pattern_name: str) -> Dict[str, Any]:
    """
    Get common coding patterns for Therefore API operations.

    Args:
        pattern_name: Pattern identifier (e.g., 'map_index_values_to_columns')

    Returns:
        Pattern description and code examples in multiple languages

    Example:
        >>> pattern = get_common_pattern('map_index_values_to_columns')
        >>> print(pattern['example_python'])
    """
    kb = load_knowledge_base()
    patterns = kb.get('common_patterns', {})

    if pattern_name not in patterns:
        available = list(patterns.keys())
        return {
            'error': f'Pattern "{pattern_name}" not found',
            'available_patterns': available
        }

    return patterns[pattern_name]


def get_api_quirks(search: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Get known API quirks, gotchas, and workarounds.

    Args:
        search: Optional search term to filter quirks

    Returns:
        List of quirks with explanations and workarounds

    Example:
        >>> quirks = get_api_quirks('keyword')
        >>> for quirk in quirks:
        ...     print(f"Issue: {quirk['issue']}")
        ...     print(f"Workaround: {quirk['workaround']}")
    """
    kb = load_knowledge_base()
    quirks = kb.get('api_quirks', [])

    if search:
        search_lower = search.lower()
        quirks = [
            q for q in quirks
            if search_lower in q.get('issue', '').lower()
            or search_lower in q.get('explanation', '').lower()
            or search_lower in str(q.get('affected_operations', [])).lower()
        ]

    return quirks


def get_endpoint_info(operation_name: str) -> Dict[str, Any]:
    """
    Get detailed information about a specific API endpoint.

    Args:
        operation_name: Operation name (e.g., 'ExecuteAsyncSingleQuery')

    Returns:
        Endpoint details including request/response schemas and related operations

    Example:
        >>> info = get_endpoint_info('ExecuteAsyncSingleQuery')
        >>> print(info['description'])
        >>> print(info['request_schema'])
    """
    kb = load_knowledge_base()
    endpoints = kb.get('endpoints', {})

    if operation_name not in endpoints:
        available = list(endpoints.keys())
        return {
            'error': f'Endpoint "{operation_name}" not found',
            'available_endpoints': available
        }

    return endpoints[operation_name]


def search_knowledge(query: str, limit: int = 5) -> List[Dict[str, Any]]:
    """
    Search across all Therefore API knowledge (workflows, patterns, quirks, endpoints).

    Args:
        query: Search query
        limit: Maximum number of results

    Returns:
        Relevant knowledge items ranked by relevance

    Example:
        >>> results = search_knowledge('how to query with date filter')
        >>> for result in results:
        ...     print(f"{result['type']}: {result['title']}")
    """
    kb = load_knowledge_base()
    query_lower = query.lower()
    results = []

    # Search workflows
    for name, workflow in kb.get('workflows', {}).items():
        score = 0
        if query_lower in workflow.get('name', '').lower():
            score += 10
        if query_lower in workflow.get('description', '').lower():
            score += 5
        if any(query_lower in uc.lower() for uc in workflow.get('use_cases', [])):
            score += 3

        if score > 0:
            results.append({
                'type': 'workflow',
                'name': name,
                'title': workflow.get('name'),
                'description': workflow.get('description'),
                'score': score,
                'data': workflow
            })

    # Search patterns
    for name, pattern in kb.get('common_patterns', {}).items():
        score = 0
        if query_lower in name.lower():
            score += 10
        if query_lower in pattern.get('description', '').lower():
            score += 5

        if score > 0:
            results.append({
                'type': 'pattern',
                'name': name,
                'description': pattern.get('description'),
                'score': score,
                'data': pattern
            })

    # Search quirks
    for quirk in kb.get('api_quirks', []):
        score = 0
        if query_lower in quirk.get('issue', '').lower():
            score += 10
        if query_lower in quirk.get('explanation', '').lower():
            score += 5

        if score > 0:
            results.append({
                'type': 'quirk',
                'issue': quirk.get('issue'),
                'explanation': quirk.get('explanation'),
                'score': score,
                'data': quirk
            })

    # Sort by score and limit
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:limit]


def list_available_knowledge() -> Dict[str, List[str]]:
    """
    List all available knowledge items by category.

    Returns:
        Dictionary mapping categories to available items

    Example:
        >>> available = list_available_knowledge()
        >>> print(available['workflows'])
        >>> print(available['field_types'])
    """
    kb = load_knowledge_base()

    return {
        'workflows': list(kb.get('workflows', {}).keys()),
        'field_types': {
            k: v.get('name') for k, v in kb.get('field_types', {}).items()
        },
        'common_patterns': list(kb.get('common_patterns', {}).keys()),
        'endpoints': list(kb.get('endpoints', {}).keys()),
        'quirks_count': len(kb.get('api_quirks', []))
    }


# Example usage and testing
if __name__ == '__main__':
    print("=== Therefore API Knowledge Tools ===\n")

    # List available knowledge
    print("Available Knowledge:")
    knowledge = list_available_knowledge()
    print(f"  Workflows: {knowledge['workflows']}")
    print(f"  Field Types: {knowledge['field_types']}")
    print(f"  Patterns: {knowledge['common_patterns']}")
    print(f"  Quirks: {knowledge['quirks_count']}\n")

    # Get workflow guide
    print("=== Query Workflow ===")
    workflow = get_workflow_guide('query_documents_with_filter')
    print(f"Name: {workflow.get('name')}")
    print(f"Steps: {len(workflow.get('steps', []))}\n")

    # Get field type info
    print("=== String Field Type ===")
    field_info = get_field_type_info(0)
    print(f"Name: {field_info.get('name')}")
    print(f"Index Data Type: {field_info.get('index_data_type')}\n")

    # Search knowledge
    print("=== Search: 'query filter' ===")
    results = search_knowledge('query filter', limit=3)
    for result in results:
        print(f"  [{result['type']}] {result.get('title') or result.get('name')}")
    print()

    # Get quirks
    print("=== API Quirks ===")
    quirks = get_api_quirks()
    for quirk in quirks[:2]:
        print(f"  - {quirk['issue']}")
