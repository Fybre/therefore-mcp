#!/usr/bin/env python3
"""
Test the get_therefore_api_help tool.
"""
import json
import sys

sys.path.insert(0, 'src')
from mcp_server import MCPServer
from therefore_client import ThereforeClient, build_tenant_configs_from_env, load_env

def test_help_endpoint():
    """Test the Therefore API help endpoint tool."""
    print("=== Testing get_therefore_api_help ===\n")

    # Load environment and build configs
    import os
    env_path = os.environ.get('THEREFORE_ENV_PATH', 'docs/reference/user/.env.local')
    env = load_env(env_path)
    configs, default_tenant, labels = build_tenant_configs_from_env(env)

    if not configs:
        print("ERROR: No tenants configured in environment")
        sys.exit(1)

    # Get first tenant
    tenant_name = default_tenant or list(configs.keys())[0]
    config = configs[tenant_name]
    client = ThereforeClient(config)

    # Create server
    server = MCPServer(
        clients={tenant_name: client},
        default_tenant=tenant_name,
        tenant_labels={tenant_name: tenant_name.title()}
    )

    print(f"Using tenant: {tenant_name}")
    print(f"Base URL: {config.base_url}\n")

    # Test 1: Get help index
    print("1. Testing help index (all operations)...")
    result = server._call_tool('get_therefore_api_help', {
        'format': 'text',
        'tenant': tenant_name
    })

    if 'error' in result:
        print(f"   ⚠ Error: {result['error']}")
        if result.get('status_code') == 404:
            print(f"   Note: Help endpoint may not be available on this server")
            print(f"   URL attempted: {result.get('url')}")
    else:
        content_preview = result.get('content', '')[:200].replace('\n', ' ')
        print(f"   URL: {result.get('url')}")
        print(f"   Format: {result.get('format')}")
        print(f"   Content preview: {content_preview}...")
        print(f"   ✓ Success\n")

    # Test 2: Get specific operation help
    print("2. Testing specific operation help (ExecuteAsyncSingleQuery)...")
    result = server._call_tool('get_therefore_api_help', {
        'operation': 'ExecuteAsyncSingleQuery',
        'format': 'text',
        'tenant': tenant_name
    })

    if 'error' in result:
        print(f"   ⚠ Error: {result['error']}")
        print(f"   URL attempted: {result.get('url')}")
    else:
        content_preview = result.get('content', '')[:200].replace('\n', ' ')
        print(f"   URL: {result.get('url')}")
        print(f"   Operation: {result.get('operation')}")
        print(f"   Format: {result.get('format')}")
        print(f"   Content preview: {content_preview}...")
        print(f"   ✓ Success\n")

    # Test 3: Get help in HTML format
    print("3. Testing HTML format...")
    result = server._call_tool('get_therefore_api_help', {
        'operation': 'GetCategoryInfo',
        'format': 'html',
        'tenant': tenant_name
    })

    if 'error' in result:
        print(f"   ⚠ Error: {result['error']}")
    else:
        html_preview = result.get('content', '')[:100]
        print(f"   URL: {result.get('url')}")
        print(f"   Format: {result.get('format')}")
        print(f"   HTML length: {len(result.get('content', ''))} characters")
        print(f"   HTML preview: {html_preview}...")
        print(f"   ✓ Success\n")

    # Test 4: Test invalid operation
    print("4. Testing invalid operation (should return 404)...")
    result = server._call_tool('get_therefore_api_help', {
        'operation': 'InvalidOperationName',
        'format': 'text',
        'tenant': tenant_name
    })

    if 'error' in result:
        print(f"   Expected error: {result['error']}")
        print(f"   Status: {result.get('status_code')}")
        print(f"   ✓ Handled correctly\n")
    else:
        print(f"   ⚠ Expected error but got success\n")

    print("=== Help Endpoint Tests Complete ===\n")

if __name__ == '__main__':
    try:
        test_help_endpoint()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
