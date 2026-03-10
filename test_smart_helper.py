#!/usr/bin/env python3
"""
Test the ask_therefore_expert smart helper tool.
"""
import json
import sys

sys.path.insert(0, 'src')
from mcp_server import MCPServer
from therefore_client import ThereforeClient, build_tenant_configs_from_env, load_env

def test_smart_helper():
    """Test the smart helper with common questions."""
    print("=== Testing ask_therefore_expert Smart Helper ===\n")

    # Load environment
    import os
    env_path = os.environ.get('THEREFORE_ENV_PATH', 'docs/reference/user/.env.local')
    env = load_env(env_path)
    configs, default_tenant, labels = build_tenant_configs_from_env(env)

    if not configs:
        print("ERROR: No tenants configured")
        sys.exit(1)

    tenant_name = default_tenant or list(configs.keys())[0]
    config = configs[tenant_name]
    client = ThereforeClient(config)

    server = MCPServer(
        clients={tenant_name: client},
        default_tenant=tenant_name,
        tenant_labels=labels
    )

    print(f"Using tenant: {tenant_name}\n")

    # Test questions
    questions = [
        "How do I get the customer ID?",
        "How do I query documents with a filter?",
        "What's the structure for table fields?",
        "How to create a document?",
        "Summarize logs for the last 7 days",
        "Why isn't my keyword field working?",
    ]

    for i, question in enumerate(questions, 1):
        print(f"{i}. Question: \"{question}\"")
        print("-" * 60)

        try:
            result = server._call_tool('ask_therefore_expert', {
                'question': question,
                'tenant': tenant_name
            })

            answer = result.get('answer', 'No answer provided')
            print(f"Answer: {answer[:200]}{'...' if len(answer) > 200 else ''}")

            # Show relevant metadata
            if 'customer_id' in result:
                print(f"  → Customer ID: {result['customer_id']}")
            if 'workflow_name' in result:
                print(f"  → Workflow: {result['workflow_name']}")
            if 'total_steps' in result:
                print(f"  → Steps: {result['total_steps']}")
            if 'days' in result:
                print(f"  → Days: {result['days']}")
            if 'error' in result:
                print(f"  ⚠ Error: {result['error']}")

            print()

        except Exception as e:
            print(f"  ✗ Error: {str(e)}\n")

    print("=== Smart Helper Test Complete ===\n")

    # Show tool count
    print(f"Total MCP Tools: {len(server.tools)}")
    print(f"Total MCP Prompts: {len(server.prompts)}")

    knowledge_tools = [t['name'] for t in server.tools if 'therefore' in t['name'] or 'ask' in t['name']]
    print(f"\nKnowledge/Helper Tools ({len(knowledge_tools)}):")
    for tool in knowledge_tools:
        print(f"  - {tool}")

if __name__ == '__main__':
    try:
        test_smart_helper()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
