#!/usr/bin/env python3
"""
Test the Therefore knowledge tools via MCP.
"""
import json
import sys

sys.path.insert(0, 'src')
from mcp_server import MCPServer

def test_knowledge_tools():
    """Test all knowledge tools."""
    print("=== Testing Therefore Knowledge Tools ===\n")

    # Create minimal server instance (knowledge tools don't need Therefore clients)
    from therefore_client import ThereforeClient, ThereforeConfig
    dummy_config = ThereforeConfig(
        base_url='https://dummy.thereforeonline.com/theservice/v0001/restun',
        auth_method='basic',
        username='test',
        password='test'
    )
    dummy_client = ThereforeClient(dummy_config)
    server = MCPServer(
        clients={'dummy': dummy_client},
        default_tenant='dummy',
        tenant_labels={'dummy': 'Dummy'}
    )

    def call(operation, **kwargs):
        return server._call_tool('therefore_knowledge', {
            'operation': operation,
            'tenant': 'dummy',
            **kwargs,
        })

    # Test 1: List available knowledge
    print("1. Testing therefore_knowledge/list_all...")
    result = call('list_all')
    print(f"   Available workflows: {len(result['available_knowledge']['workflows'])}")
    print(f"   Available field types: {len(result['available_knowledge']['field_types'])}")
    print(f"   Available patterns: {len(result['available_knowledge']['common_patterns'])}")
    print(f"   ✓ Success\n")

    # Test 2: Search knowledge
    print("2. Testing therefore_knowledge/search...")
    result = call('search', query='how to query with filter', limit=3)
    print(f"   Query: '{result['query']}'")
    print(f"   Results found: {result['results_count']}")
    if result['results_count'] > 0:
        print(f"   First result type: {result['results'][0]['type']}")
    print(f"   ✓ Success\n")

    # Test 3: Get workflow
    print("3. Testing therefore_knowledge/get_workflow...")
    result = call('get_workflow', workflow_name='query_documents_with_filter')
    print(f"   Workflow: {result.get('name')}")
    print(f"   Steps: {len(result.get('steps', []))}")
    print(f"   Use cases: {len(result.get('use_cases', []))}")
    print(f"   ✓ Success\n")

    # Test 4: Get field type info
    print("4. Testing therefore_knowledge/get_field_types...")
    result = call('get_field_types', field_type='StringField')
    print(f"   Field type name: {result.get('name')}")
    print(f"   Index data type: {result.get('index_data_type')}")
    print(f"   ✓ Success\n")

    # Test 5: Get common pattern
    print("5. Testing therefore_knowledge/get_pattern...")
    result = call('get_pattern', pattern_name='map_index_values_to_columns')
    print(f"   Pattern: {result.get('description')[:60]}...")
    print(f"   Has Python example: {bool(result.get('example_python'))}")
    print(f"   ✓ Success\n")

    # Test 6: Get API quirks
    print("6. Testing therefore_knowledge/get_quirks...")
    result = call('get_quirks', search_term='keyword')
    print(f"   Search: '{result.get('search')}'")
    print(f"   Quirks found: {result['quirks_count']}")
    if result['quirks_count'] > 0:
        print(f"   First quirk: {result['quirks'][0]['issue'][:50]}...")
    print(f"   ✓ Success\n")

    print("=== All Knowledge Tools Tests Passed! ===\n")

    # Show example usage
    print("=== Example Natural Language Query ===")
    query = "how do I map IndexValues to column names"
    print(f"Query: '{query}'\n")

    result = call('search', query=query, limit=1)

    if result['results_count'] > 0:
        top_result = result['results'][0]
        print(f"Top Result Type: {top_result['type']}")
        print(f"Name: {top_result.get('name', top_result.get('title', 'N/A'))}")
        print(f"Description: {top_result.get('description', 'N/A')[:100]}...")
        print(f"\nFull data available in: result['results'][0]['data']")

if __name__ == '__main__':
    try:
        test_knowledge_tools()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
