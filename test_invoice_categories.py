#!/usr/bin/env python3
"""
Test: "get a list of the invoice categories on the demo therefore tenant"
"""
import json
import sys

sys.path.insert(0, 'src')
from mcp_server import MCPServer
from therefore_client import ThereforeClient, build_tenant_configs_from_env, load_env

def test_invoice_categories():
    """Test getting invoice categories."""
    print("=== Testing Invoice Categories Question ===\n")

    # Load environment
    import os
    env_path = os.environ.get('THEREFORE_ENV_PATH', 'docs/reference/user/.env.local')
    env = load_env(env_path)
    configs, default_tenant, labels = build_tenant_configs_from_env(env)

    # Use demo tenant or fallback
    tenant_name = 'demo'
    if tenant_name not in configs:
        tenant_name = 'craigdemo'  # fallback
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

    # Test the exact question
    question = "get a list of the invoice categories on the demo therefore tenant"

    print(f"Question: \"{question}\"")
    print("=" * 70)

    # Use ask_therefore_expert
    result = server._call_tool('ask_therefore_expert', {
        'question': question,
        'tenant': tenant_name
    })

    answer = result.get('answer', 'No answer')
    print(f"\n✅ Answer:\n{answer}\n")

    # Show metadata
    if 'total_categories' in result:
        print(f"Total matching categories: {result['total_categories']}")

    if 'categories' in result:
        print(f"\nDetailed category list:")
        for cat in result['categories'][:10]:  # First 10
            print(f"  - {cat['name']} (CategoryNo: {cat['category_no']})")
            if cat.get('full_path') and cat['full_path'] != cat['name']:
                print(f"    Path: {cat['full_path']}")

    print("\n" + "=" * 70)

    # Also test direct tool
    print("\nDirect tool comparison: get_categories_tree")
    print("-" * 70)

    try:
        tree_result = server._call_tool('get_categories_tree', {'tenant': tenant_name})
        total_cats = len(tree_result.get('Categories', []))
        print(f"Total categories in tree: {total_cats}")

        # Count invoice-related
        def count_invoice_cats(cats, count=0):
            for cat in cats:
                if 'invoice' in cat.get('Name', '').lower():
                    count += 1
                if cat.get('Children'):
                    count = count_invoice_cats(cat['Children'], count)
            return count

        invoice_count = count_invoice_cats(tree_result.get('Categories', []))
        print(f"Invoice-related categories: {invoice_count}")

    except Exception as e:
        print(f"Error getting tree: {e}")

if __name__ == '__main__':
    try:
        test_invoice_categories()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
