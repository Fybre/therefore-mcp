# Therefore API Knowledge System for AI Assistance

This document explains how the Therefore MCP project distills API knowledge to enable AI assistants to provide expert guidance on Therefore WebAPI usage.

## Overview

We use a **multi-tiered approach** to make Therefore API knowledge accessible to AI:

1. **Human-Readable Documentation** - Comprehensive guides with examples
2. **Machine-Readable Knowledge Base** - Structured JSON for programmatic access
3. **Knowledge Access Tools** - Python utilities for querying knowledge
4. **Context in CLAUDE.md** - Quick reference for AI assistants

## Knowledge Resources

### Tier 1: Human-Readable Documentation

**File:** `docs/therefore-api-complete-guide.md`

**Contents:**
- Authentication patterns (Basic, Bearer)
- Complete workflows with step-by-step instructions
- Query operations and condition syntax
- Document CRUD operations
- Table field handling
- Field type mappings and structures
- Error handling patterns
- API quirks and workarounds
- Code examples (Python, JavaScript, curl)

**Usage:**
```markdown
When helping users with Therefore API:
1. Reference this guide for complete workflows
2. Copy example payloads and adapt to user needs
3. Check quirks section for known issues
4. Use code examples as templates
```

**Example Queries:**
- "How do I query documents with a filter?"
- "What's the structure for creating a document?"
- "How do I work with table fields?"
- "What are the field types and their data structures?"

### Tier 2: Machine-Readable Knowledge Base

**File:** `docs/knowledge-base.json`

**Structure:**
```json
{
  "workflows": {
    "query_documents_with_filter": {
      "name": "...",
      "steps": [...],
      "code_examples": {...}
    }
  },
  "field_types": {
    "0": {
      "name": "StringField",
      "index_data_type": "StringIndexData",
      "example": {...}
    }
  },
  "common_patterns": {
    "map_index_values_to_columns": {...}
  },
  "api_quirks": [...],
  "endpoints": {...}
}
```

**Usage:**
```python
import json

# Load knowledge base
with open('docs/knowledge-base.json') as f:
    kb = json.load(f)

# Get workflow
workflow = kb['workflows']['query_documents_with_filter']
for step in workflow['steps']:
    print(f"Step {step['step']}: {step['description']}")

# Get field type info
string_field = kb['field_types']['0']
print(string_field['example'])

# Find quirks
for quirk in kb['api_quirks']:
    if 'keyword' in quirk['issue'].lower():
        print(f"Issue: {quirk['issue']}")
        print(f"Workaround: {quirk['workaround']}")
```

### Tier 3: Knowledge Access Tools

**File:** `src/knowledge_tools.py`

**Functions:**
- `get_workflow_guide(workflow_name)` - Get complete workflow with steps
- `get_field_type_info(field_type)` - Get field type details
- `get_common_pattern(pattern_name)` - Get coding patterns
- `get_api_quirks(search)` - Get quirks and workarounds
- `get_endpoint_info(operation_name)` - Get endpoint details
- `search_knowledge(query)` - Semantic search across all knowledge
- `list_available_knowledge()` - List all available knowledge items

**Usage:**
```python
from src.knowledge_tools import (
    get_workflow_guide,
    get_field_type_info,
    search_knowledge
)

# Get workflow guide
workflow = get_workflow_guide('query_documents_with_filter')
print(workflow['name'])
for step in workflow['steps']:
    print(f"Step {step['step']}: {step['operation']}")

# Get field type info
string_field = get_field_type_info('StringField')
print(string_field['index_data_type'])
print(string_field['example'])

# Search knowledge
results = search_knowledge('how to filter by date', limit=5)
for result in results:
    print(f"{result['type']}: {result.get('title', result.get('name'))}")
```

**CLI Usage:**
```bash
# Run knowledge tools interactively
python3 src/knowledge_tools.py

# Output:
# === Therefore API Knowledge Tools ===
# Available Knowledge:
#   Workflows: ['query_documents_with_filter', ...]
#   Field Types: {'0': 'StringField', '1': 'IntField', ...}
#   Patterns: ['map_index_values_to_columns', ...]
```

### Tier 4: AI Context (CLAUDE.md)

**File:** `CLAUDE.md`

**Contains:**
- Quick reference to knowledge resources
- Key patterns and mappings
- Critical quirks to remember
- Links to detailed guides

**Usage:** Automatically loaded by Claude Code for context.

## How AI Should Use These Resources

### For General Questions

**User:** "How do I query documents in Therefore?"

**AI Approach:**
1. Reference `therefore-api-complete-guide.md` for the complete workflow
2. Provide step-by-step instructions with payload examples
3. Mention common patterns (IndexValues mapping)
4. Warn about quirks (query session release)

### For Specific Examples

**User:** "Give me the exact REST API call to query category 270 for Order_No = '12345'"

**AI Approach:**
1. Use workflow from `knowledge-base.json`
2. Substitute user values into request template
3. Provide complete curl example with auth headers
4. Explain response structure and field mapping

### For Troubleshooting

**User:** "My keyword field isn't working, it says conversion error"

**AI Approach:**
1. Search `api_quirks` for "keyword"
2. Find quirk: "Keyword fields require KeywordNos, not strings"
3. Explain workaround: resolve string to KeywordNo first
4. Provide example of GetDictionaryInfo usage

### For Learning

**User:** "Teach me how to work with table fields"

**AI Approach:**
1. Get workflow for table operations
2. Show structure of TableData
3. Demonstrate GetDocumentIndexData vs query IndexValues
4. Provide complete example with table creation

## Example: AI Assistance Flow

### User Request:
> "I want to query all documents in category 270 where Order_No matches '12345', then iterate through the table values in each document."

### AI Response Flow:

1. **Load Workflow Knowledge:**
```python
workflow = get_workflow_guide('query_documents_with_filter')
```

2. **Construct Response:**
```markdown
Here's how to query category 270 and iterate table values:

## Step 1: Execute Async Query
[Provide payload from workflow.steps[0].request_template with user values]

## Step 2: Map Results
[Explain IndexValues → Columns mapping from common_patterns]

## Step 3: Get Table Data
[Show GetDocumentIndexData call from workflow.steps[2]]

## Step 4: Release Query
[Emphasize this is CRITICAL from workflow.steps[3]]

## Complete Example
[Provide working Python code combining all steps]
```

3. **Add Context from Quirks:**
```markdown
## Important Notes:
- Table data in query IndexValues is concatenated; use GetDocumentIndexData for structured access
- Always release query in finally block to avoid resource leaks
```

## Extending the Knowledge Base

### Adding New Workflows

Edit `docs/knowledge-base.json`:

```json
{
  "workflows": {
    "your_new_workflow": {
      "name": "Workflow Name",
      "description": "What this workflow does",
      "use_cases": ["Use case 1", "Use case 2"],
      "steps": [
        {
          "step": 1,
          "operation": "OperationName",
          "description": "What this step does",
          "endpoint": "/EndpointPath",
          "request_template": {...},
          "response_fields": {...}
        }
      ],
      "code_examples": {
        "python": "...",
        "javascript": "..."
      }
    }
  }
}
```

### Adding New Patterns

```json
{
  "common_patterns": {
    "your_pattern_name": {
      "description": "What this pattern solves",
      "pattern": "High-level description",
      "example_python": "code here",
      "example_javascript": "code here"
    }
  }
}
```

### Adding New Quirks

```json
{
  "api_quirks": [
    {
      "issue": "Short description of the problem",
      "explanation": "Why this happens",
      "affected_operations": ["Op1", "Op2"],
      "workaround": "How to work around it",
      "example": {
        "wrong": {...},
        "correct": {...}
      }
    }
  ]
}
```

## Future Enhancements

### Option 1: Add to MCP Server (Recommended Next Step)

Integrate knowledge tools directly into the MCP server:

```python
# In src/mcp_server.py

@mcp_tool
def get_therefore_api_workflow(workflow_name: str) -> dict:
    """Get step-by-step workflow guide for Therefore API operations."""
    from knowledge_tools import get_workflow_guide
    return get_workflow_guide(workflow_name)

@mcp_tool
def search_therefore_knowledge(query: str, limit: int = 5) -> list:
    """Search Therefore API documentation and examples."""
    from knowledge_tools import search_knowledge
    return search_knowledge(query, limit)
```

### Option 2: Vector Search (RAG)

Build semantic search over documentation:

1. Embed all documentation using text-embedding models
2. Store in vector database (ChromaDB, Pinecone)
3. Add MCP tool for semantic search
4. Return most relevant docs/examples for any query

```python
@mcp_tool
def semantic_search_therefore_docs(query: str, limit: int = 3) -> list:
    """Semantic search over all Therefore API documentation."""
    # Vector search implementation
    embeddings = embed(query)
    results = vector_db.query(embeddings, limit=limit)
    return results
```

### Option 3: Interactive Tutorials

Create step-by-step interactive tutorials:

```python
@mcp_tool
def therefore_tutorial(topic: str, step: int = 0) -> dict:
    """Interactive tutorial for Therefore API operations."""
    tutorials = load_tutorials()
    tutorial = tutorials[topic]

    if step == 0:
        return {"overview": tutorial["overview"], "total_steps": len(tutorial["steps"])}

    current_step = tutorial["steps"][step - 1]
    return {
        "step": step,
        "instruction": current_step["instruction"],
        "code": current_step["code"],
        "try_it": current_step["example"],
        "expected": current_step["expected_output"],
        "next": step + 1 if step < len(tutorial["steps"]) else None
    }
```

## Summary

The Therefore MCP project uses a **layered knowledge system**:

| Layer | Format | Use Case | Audience |
|-------|--------|----------|----------|
| Complete Guide | Markdown | Human reading, AI reference | Developers, AI |
| Knowledge Base | JSON | Programmatic access | Scripts, AI |
| Access Tools | Python | Querying knowledge | Applications, AI |
| CLAUDE.md | Markdown | AI context | Claude Code |

This multi-tiered approach ensures:
- ✅ **Human accessibility** - Readable guides and examples
- ✅ **AI accessibility** - Structured, searchable knowledge
- ✅ **Maintainability** - Single source of truth
- ✅ **Extensibility** - Easy to add new workflows/patterns
- ✅ **Programmatic access** - Tools for building on top

The system enables AI assistants to provide **expert-level guidance** on Therefore API usage without requiring the AI to have prior training on Therefore-specific APIs.
