#!/usr/bin/env python3
"""
Test the expert router with various queries.
Demonstrates how ask_therefore_expert returns exact tool/operation/parameters.
"""
import sys
import json
sys.path.insert(0, 'src')

from mcp_server import OPERATION_REGISTRY

def simulate_expert_query(question):
    """Simulate the expert routing logic (simplified version)."""
    question_lower = question.lower()

    # Keyword matching (from actual implementation)
    tool_suggestions = {
        "customer id": ("therefore_system", "get_customer_id"),
        "create document": ("therefore_documents", "create"),
        "create a document": ("therefore_documents", "create"),
        "query documents": ("therefore_query", "search"),
        "search documents": ("therefore_query", "search"),
        "my tasks": ("therefore_workflow", "get_my_tasks"),
        "workflow tasks": ("therefore_workflow", "get_my_tasks"),
        "get document": ("therefore_documents", "get"),
        "get a document": ("therefore_documents", "get"),
        "categories": ("therefore_categories", "get_tree"),
        "users": ("therefore_users", "search"),
        "search users": ("therefore_users", "search"),
    }

    # Find match
    suggested = None
    for keyword, (tool, op) in tool_suggestions.items():
        if keyword in question_lower:
            suggested = (tool, op)
            break

    if not suggested:
        return None

    # Get info from registry
    tool_name, operation = suggested
    registry_key = (tool_name, operation)
    param_info = OPERATION_REGISTRY.get(registry_key, {})

    # Build response
    call_with = {"operation": operation}
    for req_param in param_info.get("required", []):
        call_with[req_param] = f"<required - {req_param}>"

    return {
        "question": question,
        "suggested_tool": tool_name,
        "suggested_operation": operation,
        "description": param_info.get("description", ""),
        "call_with": call_with,
        "all_parameters": {
            "required": param_info.get("required", []),
            "optional": param_info.get("optional", {}),
        },
    }

# Test cases
test_questions = [
    "What is the customer ID?",
    "How do I create a document?",
    "I need to query documents",
    "Show me my workflow tasks",
    "Get a document by number",
    "List all categories",
    "Search for users",
]

print("Expert Router Test Results")
print("=" * 80)

for question in test_questions:
    print(f"\nQuestion: {question}")
    print("-" * 80)

    result = simulate_expert_query(question)

    if result:
        print(f"✅ Tool: {result['suggested_tool']}")
        print(f"✅ Operation: {result['suggested_operation']}")
        print(f"✅ Description: {result['description']}")
        print(f"✅ Required params: {result['all_parameters']['required']}")
        print(f"✅ Optional params: {len(result['all_parameters']['optional'])} available")
        print(f"\n📝 Call with:")
        print(json.dumps(result['call_with'], indent=2))
    else:
        print("❌ No match found")

print("\n" + "=" * 80)
print("✅ Test complete!")
