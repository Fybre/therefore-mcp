# Therefore Knowledge Tools - Usage Guide

The Therefore MCP server now includes **7 knowledge tools** that enable natural language queries to access Therefore API documentation, workflows, patterns, and examples.

## Available Tools

### 1. `search_therefore_knowledge`

**Purpose:** Search the knowledge base using natural language queries.

**Use when:** You need to find relevant documentation but don't know which specific resource to use.

**Parameters:**
- `query` (required): Natural language search query
- `limit` (optional): Max results to return (default: 5)

**Example Queries:**
```json
{"query": "how to filter by date range"}
{"query": "working with table fields"}
{"query": "mapping query results to field names"}
{"query": "keyword field issues"}
```

**Response:**
```json
{
  "query": "how to filter by date range",
  "results_count": 3,
  "results": [
    {
      "type": "pattern",
      "name": "query_condition_syntax",
      "description": "Query condition syntax patterns",
      "score": 10,
      "data": {
        "date_range": "FieldName >= '2024-01-01' AND FieldName <= '2024-12-31'"
      }
    }
  ]
}
```

---

### 2. `get_therefore_workflow`

**Purpose:** Get complete step-by-step workflow guides for common operations.

**Use when:** You need detailed instructions for multi-step Therefore API operations.

**Parameters:**
- `workflow_name` (required): Workflow identifier

**Available Workflows:**
- `query_documents_with_filter` - Query documents with filters and iterate results
- `create_document_web_client_flow` - 4-step document creation process

**Example:**
```json
{"workflow_name": "query_documents_with_filter"}
```

**Response:**
```json
{
  "workflow_name": "query_documents_with_filter",
  "name": "Query Documents with Field Filter",
  "description": "Search for documents in a category matching specific field criteria...",
  "use_cases": [
    "Find all orders for a customer",
    "Search invoices by date range"
  ],
  "steps": [
    {
      "step": 1,
      "operation": "ExecuteAsyncSingleQuery",
      "description": "Initiate async query with filter conditions",
      "endpoint": "/ExecuteAsyncSingleQuery",
      "request_template": {
        "Query": {
          "CategoryNo": "<category_number>",
          "Conditions": [...]
        }
      }
    }
  ]
}
```

---

### 3. `get_therefore_field_type_info`

**Purpose:** Get detailed information about Therefore field types and their data structures.

**Use when:** You need to know how to structure index data for a specific field type.

**Parameters:**
- `field_type` (required): Field type number (0-9) or name (e.g., "StringField")

**Available Field Types:**
- `0` or `"StringField"` - String/text fields
- `1` or `"IntField"` - Integer/numeric fields
- `2` or `"DateField"` - Date-only fields
- `3` or `"DateTimeField"` - Date+time fields
- `5` or `"MoneyField"` - Currency/decimal fields
- `6` or `"KeywordField"` - Single keyword selection
- `9` or `"TableField"` - Table/grid fields

**Example:**
```json
{"field_type": "TableField"}
```

**Response:**
```json
{
  "field_type": "TableField",
  "name": "TableField",
  "index_data_type": "TableData",
  "structure": {
    "FieldNo": "integer",
    "FieldName": "string (optional)",
    "Rows": [...]
  },
  "example": {
    "TableData": {
      "FieldNo": 150,
      "Rows": [
        {
          "RowNo": 1,
          "Values": [...]
        }
      ]
    }
  }
}
```

---

### 4. `get_therefore_common_pattern`

**Purpose:** Get common coding patterns with examples in multiple languages.

**Use when:** You need code examples for common Therefore API operations.

**Parameters:**
- `pattern_name` (required): Pattern identifier

**Available Patterns:**
- `map_index_values_to_columns` - Map query results to field names
- `query_condition_syntax` - Filter condition syntax
- `batch_query_results` - Paginate through large result sets

**Example:**
```json
{"pattern_name": "map_index_values_to_columns"}
```

**Response:**
```json
{
  "pattern_name": "map_index_values_to_columns",
  "description": "Map query result IndexValues array to field names using Columns array",
  "pattern": "IndexValues[i] corresponds to Columns[i].ColName (positional mapping)",
  "example_python": "def map_row(row, columns):\n    return {columns[i]['ColName']: value for i, value in enumerate(row['IndexValues'])}",
  "example_javascript": "const mapRow = (row, columns) => ..."
}
```

---

### 5. `get_therefore_api_quirks`

**Purpose:** Get known API quirks, gotchas, and workarounds.

**Use when:** Something isn't working as expected or you want to avoid common pitfalls.

**Parameters:**
- `search` (optional): Filter quirks by keyword

**Example:**
```json
{"search": "keyword"}
```

**Response:**
```json
{
  "search": "keyword",
  "quirks_count": 2,
  "quirks": [
    {
      "issue": "Keyword fields require KeywordNos, not string values",
      "explanation": "Passing string values to keyword fields causes conversion errors",
      "affected_operations": ["CreateDocument", "UpdateDocument"],
      "workaround": "Resolve keyword strings to KeywordNos using GetDictionaryInfo first",
      "example": {
        "wrong": {"SingleKeywordData": {"FieldNo": 105, "DataValue": "Active"}},
        "correct": {"SingleKeywordData": {"FieldNo": 105, "KeywordNo": 42}}
      }
    }
  ]
}
```

---

### 6. `list_therefore_knowledge`

**Purpose:** List all available knowledge resources.

**Use when:** You want to discover what knowledge is available.

**Parameters:** None

**Example:**
```json
{}
```

**Response:**
```json
{
  "available_knowledge": {
    "workflows": ["query_documents_with_filter", "create_document_web_client_flow"],
    "field_types": {
      "0": "StringField",
      "1": "IntField",
      "9": "TableField"
    },
    "common_patterns": ["map_index_values_to_columns", "query_condition_syntax", "batch_query_results"],
    "endpoints": ["ExecuteAsyncSingleQuery", "GetDocumentIndexData"],
    "quirks_count": 4
  }
}
```

---

### 7. `get_therefore_api_help`

**Purpose:** Fetch live Therefore API documentation from the help endpoint.

**Use when:** You need the most up-to-date API documentation with request/response examples directly from the Therefore server.

**Parameters:**
- `operation` (optional): Operation name to get specific documentation (e.g., "ExecuteAsyncSingleQuery")
- `format` (optional): Format for the content - "html", "text", or "json" (default: "text")

**Example - Get help index:**
```json
{}
```

**Example - Get specific operation:**
```json
{"operation": "ExecuteAsyncSingleQuery", "format": "text"}
```

**Example - Get raw HTML:**
```json
{"operation": "CreateDocument", "format": "html"}
```

**Response:**
```json
{
  "url": "https://tenant.thereforeonline.com/theservice/v0001/restun/help/operations/ExecuteAsyncSingleQuery",
  "operation": "ExecuteAsyncSingleQuery",
  "content": "Reference for ExecuteAsyncSingleQuery\n\nService at https://...\n\n...",
  "format": "text",
  "note": "Parsed text from Therefore API help. For structured data, see docs/export/tenant_operations.json or use format=\"html\"."
}
```

**Available Formats:**
- `text` - Parsed, readable text (default)
- `html` - Raw HTML from the help page
- `json` - Attempts to extract JSON code blocks

**Use Cases:**
- Get the latest API documentation for an operation
- View request/response examples from the live server
- Access help when offline docs may be outdated
- Browse all available operations

**Note:** This tool fetches documentation from the live Therefore server, so:
- Requires active tenant connection
- Reflects the exact API version running on that server
- May include server-specific customizations

---

## Common Use Cases

### Use Case 1: "How do I query documents with a filter?"

**Step 1:** Search for relevant information
```json
{"tool": "search_therefore_knowledge", "args": {"query": "query with filter", "limit": 3}}
```

**Step 2:** Get the workflow guide
```json
{"tool": "get_therefore_workflow", "args": {"workflow_name": "query_documents_with_filter"}}
```

**Result:** Complete step-by-step guide with request templates and code examples.

---

### Use Case 2: "What's the structure for table fields?"

**Step 1:** Get field type info
```json
{"tool": "get_therefore_field_type_info", "args": {"field_type": "TableField"}}
```

**Result:** Complete structure definition with examples.

---

### Use Case 3: "Why is my keyword field failing?"

**Step 1:** Search quirks
```json
{"tool": "get_therefore_api_quirks", "args": {"search": "keyword"}}
```

**Result:** Known issues with keyword fields and workarounds.

---

### Use Case 4: "How do I map query results to field names?"

**Step 1:** Get the pattern
```json
{"tool": "get_therefore_common_pattern", "args": {"pattern_name": "map_index_values_to_columns"}}
```

**Result:** Code examples in Python and JavaScript.

---

## Integration Examples

### Python Client
```python
import json

# Connect to Therefore MCP server
# (use your MCP client library)

# Search knowledge
result = mcp_client.call_tool('search_therefore_knowledge', {
    'query': 'how to create document with table'
})

print(f"Found {result['results_count']} results")
for item in result['results']:
    print(f"- {item['type']}: {item.get('name', item.get('title'))}")

# Get workflow guide
workflow = mcp_client.call_tool('get_therefore_workflow', {
    'workflow_name': 'create_document_web_client_flow'
})

print(f"\nWorkflow: {workflow['name']}")
for step in workflow['steps']:
    print(f"Step {step['step']}: {step['operation']}")
```

### Claude Desktop / AI Assistant
```
User: How do I query Therefore for documents with a specific order number?

AI: Let me search the Therefore knowledge base.
[Calls search_therefore_knowledge with query: "query documents filter"]
[Calls get_therefore_workflow with workflow_name: "query_documents_with_filter"]

AI: Here's how to query documents with a filter:

1. Execute Async Query (POST /ExecuteAsyncSingleQuery)
   Request:
   {
     "Query": {
       "CategoryNo": 270,
       "Conditions": [{
         "FieldNoOrName": "Order_No",
         "Condition": "Order_No = '12345'"
       }]
     }
   }

2. Map results using IndexValues → Columns positional mapping
3. Fetch additional batches if HasRemainingRows = true
4. Always release query in finally block

[Provides complete code example from workflow]
```

---

## AI Assistant Best Practices

### For General Questions
1. Use `search_therefore_knowledge` first to find relevant resources
2. Follow up with specific tools for detailed information
3. Provide complete examples from the knowledge base

### For Specific Tasks
1. Use `get_therefore_workflow` for multi-step operations
2. Use `get_therefore_field_type_info` for data structure questions
3. Use `get_therefore_common_pattern` for code examples

### For Troubleshooting
1. Use `get_therefore_api_quirks` to find known issues
2. Cross-reference with workflow guides for correct patterns
3. Provide workarounds from quirks database

### For Discovery
1. Use `list_therefore_knowledge` to show available resources
2. Use `search_therefore_knowledge` for open-ended exploration
3. Guide users to more specific tools based on search results

---

## Architecture

The knowledge system consists of:

1. **Knowledge Base** (`docs/knowledge-base.json`)
   - Structured workflows, patterns, field types, quirks
   - Machine-readable JSON format

2. **Access Layer** (`src/knowledge_tools.py`)
   - Python utilities for querying knowledge
   - Search, retrieval, and listing functions

3. **MCP Tools** (`src/mcp_server.py`)
   - 6 MCP tools exposing knowledge via natural language
   - No Therefore tenant/client required

4. **Documentation** (`docs/therefore-api-complete-guide.md`)
   - Human-readable comprehensive guide
   - Reference for workflows and examples

---

## Extending the Knowledge Base

To add new workflows, patterns, or quirks, edit `docs/knowledge-base.json`:

**Add a workflow:**
```json
{
  "workflows": {
    "your_workflow_name": {
      "name": "Workflow Display Name",
      "description": "What this workflow does",
      "use_cases": ["Use case 1", "Use case 2"],
      "steps": [...]
    }
  }
}
```

**Add a pattern:**
```json
{
  "common_patterns": {
    "your_pattern_name": {
      "description": "What this pattern does",
      "pattern": "High-level description",
      "example_python": "code here"
    }
  }
}
```

**Add a quirk:**
```json
{
  "api_quirks": [
    {
      "issue": "Brief issue description",
      "explanation": "Why this happens",
      "affected_operations": ["Op1", "Op2"],
      "workaround": "How to fix it"
    }
  ]
}
```

Changes to `knowledge-base.json` are immediately available via the MCP tools.

---

## Testing

Run the test suite:
```bash
python3 test_knowledge_tools.py
```

Expected output:
```
=== Testing Therefore Knowledge Tools ===

1. Testing list_therefore_knowledge...
   ✓ Success

2. Testing search_therefore_knowledge...
   ✓ Success

...

=== All Knowledge Tools Tests Passed! ===
```

---

## Troubleshooting

**Error: "Knowledge search failed"**
- Ensure `docs/knowledge-base.json` exists
- Ensure `src/knowledge_tools.py` is in the Python path

**Error: "Workflow not found"**
- Use `list_therefore_knowledge` to see available workflows
- Check spelling of workflow_name

**Empty search results:**
- Try broader search terms
- Use `list_therefore_knowledge` to discover resources
- Search is keyword-based, not semantic

---

## Summary

The knowledge tools enable:
- ✅ **Natural language queries** to access Therefore API documentation
- ✅ **Complete workflow guides** with step-by-step instructions
- ✅ **Field type references** with structure examples
- ✅ **Common patterns** with code in multiple languages
- ✅ **Quirks database** for troubleshooting
- ✅ **Knowledge discovery** via search and listing

Use these tools to provide expert-level Therefore API guidance without requiring prior API knowledge!
