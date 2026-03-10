#!/usr/bin/env python3
"""
Test the exact question: "what is the client id for the craigdemo therefore client"
"""
import json
import sys

sys.path.insert(0, 'src')
from mcp_server import MCPServer
from therefore_client import ThereforeClient, build_tenant_configs_from_env, load_env

def test_client_id_question():
    """Test client ID question variants."""
    print("=== Testing Client ID Questions ===\n")

    # Load environment
    import os
    env_path = os.environ.get('THEREFORE_ENV_PATH', 'docs/reference/user/.env.local')
    env = load_env(env_path)
    configs, default_tenant, labels = build_tenant_configs_from_env(env)

    tenant_name = 'craigdemo'
    if tenant_name not in configs:
        tenant_name = default_tenant or list(configs.keys())[0]

    config = configs[tenant_name]
    client = ThereforeClient(config)

    server = MCPServer(
        clients={tenant_name: client},
        default_tenant=tenant_name,
        tenant_labels=labels
    )

    print(f"Using tenant: {tenant_name}\n")

    # Test different phrasings
    questions = [
        "what is the client id for the craigdemo therefore client",
        "what is the customer id",
        "what is the system id",
        "what is the tenant id",
        "get the client id",
    ]

    for i, question in enumerate(questions, 1):
        print(f"{i}. Question: \"{question}\"")
        print("-" * 70)

        # Test with ask_therefore_expert
        result = server._call_tool('ask_therefore_expert', {
            'question': question,
            'tenant': tenant_name
        })

        answer = result.get('answer', 'No answer')
        customer_id = result.get('customer_id')

        print(f"✅ Answer: {answer}")
        if customer_id:
            print(f"   Customer ID: {customer_id}")

        # Show what tool it would have used
        if 'how_to_get_it' in result:
            print(f"   Tool: {result['how_to_get_it']['tool']}")

        print()

    # Also test the direct tool
    print("=" * 70)
    print("Direct tool call: get_system_customer_id")
    print("-" * 70)
    result = server._call_tool('get_system_customer_id', {'tenant': tenant_name})
    customer_id = result.get('CustomerId') or result.get('CustomerID')
    print(f"✅ Customer ID: {customer_id}")
    print(f"   Full response: {json.dumps(result, indent=2)}")

if __name__ == '__main__':
    try:
        test_client_id_question()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
