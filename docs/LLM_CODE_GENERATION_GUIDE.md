# Enabling LLMs to Write Therefore Python Code

This guide explains what information LLMs need to write Python code for Therefore operations.

## What LLMs Need

For an LLM to write Python code that uses the Therefore API (not just call MCP tools), they need:

### 1. **Python Client API Reference** ✅
- Location: `src/therefore_client.py`
- What: The `ThereforeClient` class with all methods
- Usage: Import and reference this file

### 2. **Code Examples** ✅
- Location: `docs/PYTHON_EXAMPLES.md`
- What: Complete working examples of common tasks
- Includes:
  - Parsing XML and checking if documents exist
  - Querying by index data
  - Creating documents from data
  - Batch processing
  - Error handling patterns

### 3. **API Concepts & Patterns** ✅
- Location: `docs/therefore-api-complete-guide.md`
- What: Core concepts, field types, workflows
- Usage: Understanding Therefore-specific patterns

### 4. **Knowledge Base** ✅
- Location: `docs/knowledge-base.json`
- What: Structured knowledge with code examples
- Usage: Programmatic access to API info

### 5. **MCP Server as Reference** ✅
- Location: `src/mcp_server.py`
- What: Real-world usage of ThereforeClient
- Usage: See how the MCP server uses the client

## How to Provide This to an LLM

### Option 1: Direct File Access (Best for Claude Code)

Give the LLM access to these files:
```
src/therefore_client.py          # Python client API
docs/PYTHON_EXAMPLES.md          # Code examples
docs/therefore-api-complete-guide.md  # API concepts
```

Example prompt:
```
I need to write Python code that:
1. Parses an XML file containing invoice numbers
2. Checks if documents with those invoice numbers exist in Therefore
3. Creates documents for any missing invoices

Please reference:
- src/therefore_client.py for the ThereforeClient API
- docs/PYTHON_EXAMPLES.md for example patterns
```

### Option 2: MCP Server (For Runtime Operations)

**Note:** The MCP server is for *calling* Therefore operations, not for *writing Python code*.

Use MCP when:
- ✅ You want the LLM to execute Therefore operations
- ✅ You're building an interactive assistant
- ✅ You need real-time data from Therefore

Don't use MCP when:
- ❌ You want the LLM to write standalone Python scripts
- ❌ You need code that runs without the MCP server
- ❌ You're generating code for a separate application

### Option 3: Combined Approach (Recommended)

1. **For understanding:** Provide documentation files
2. **For verification:** Use MCP to test operations
3. **For code generation:** Reference examples + client API

Example workflow:
```
User: "Write Python code to check if invoice documents exist"

LLM:
1. Reads PYTHON_EXAMPLES.md for patterns
2. Reads therefore_client.py for API methods
3. Writes the code
4. (Optional) Uses MCP to test against real Therefore tenant
```

## What's Different: MCP vs Direct Python

### Using MCP Server (Tool Calls)
```python
# LLM calls MCP tools - no Python code written
result = therefore_query(
    operation="search",
    tenant="demo",
    query={"WhereClause": "[InvoiceNumber] = 'INV-001'"}
)
```

### Writing Python Code (What You Want)
```python
# LLM writes Python code using ThereforeClient
from therefore_client import ThereforeClient

client = ThereforeClient(
    base_url="https://demo.thereforeonline.com/...",
    username="user",
    password="pass"
)

result = client.execute_single_query({
    "WhereClause": "[InvoiceNumber] = 'INV-001'"
})
```

## Complete Example Prompt

Here's a complete prompt for an LLM to write Therefore Python code:

```
I need a Python script that:

1. Reads an XML file with this structure:
   <Invoices>
     <Invoice Number="INV-001" Date="2024-01-15" Amount="1500.00">
       <Customer>Acme Corp</Customer>
     </Invoice>
   </Invoices>

2. For each invoice, checks if a document exists in Therefore category 123
   with matching InvoiceNumber field

3. Creates new documents for any invoices that don't exist

Please use the ThereforeClient class from src/therefore_client.py.

Reference files:
- src/therefore_client.py - ThereforeClient API methods
- docs/PYTHON_EXAMPLES.md - See "Batch Process XML Documents" section
- docs/therefore-api-complete-guide.md - Field types and index data structure

The script should:
- Handle errors gracefully
- Log progress
- Return a summary of created/skipped/failed documents
```

## Gap Analysis

### ✅ What You Have
- ThereforeClient with comprehensive methods
- API documentation and workflows
- Knowledge base with patterns
- Python code examples (NEW)
- MCP server as reference implementation

### ⚠️ What Could Be Better
1. **Auto-generated API Reference** - Extract docstrings from ThereforeClient into markdown
2. **More Example Scripts** - Add to `examples/` directory
3. **Type Hints** - Add type annotations to ThereforeClient methods
4. **Integration Tests** - Show real-world usage patterns

### ❌ What's Not Needed
- The MCP server is NOT needed for LLMs to write Python code
- The OPERATION_REGISTRY is for MCP tools, not Python code generation

## Recommendations

### For Your Use Case (XML Processing)

Provide the LLM with:

1. **`docs/PYTHON_EXAMPLES.md`** - Has complete example for your exact use case
2. **`src/therefore_client.py`** - The API they'll use
3. **`docs/therefore-api-complete-guide.md`** - For field type details

### Example Session

```
User: "Write code to parse XML and check Therefore documents"

AI reads:
1. PYTHON_EXAMPLES.md → Finds "Batch Process XML Documents" section
2. therefore_client.py → Sees execute_single_query() and create_document() methods
3. therefore-api-complete-guide.md → Understands IndexData structure

AI writes:
- Complete working script
- Proper error handling
- Uses correct Therefore patterns
```

## Testing Code Generation

To verify an LLM can write good Therefore code:

1. **Provide these files:**
   ```
   src/therefore_client.py
   docs/PYTHON_EXAMPLES.md
   docs/therefore-api-complete-guide.md
   ```

2. **Ask for specific tasks:**
   - "Write code to check if document 12345 exists"
   - "Parse XML and query Therefore by invoice number"
   - "Create documents with index data from CSV"

3. **Expected output:**
   - Imports ThereforeClient
   - Initializes client correctly
   - Uses proper methods (execute_single_query, get_document, etc.)
   - Handles errors
   - Follows patterns from examples

## Summary

**Your Question:** Is the MCP server enough for LLMs to write Python code?

**Answer:** No, but you now have what's needed:

- ✅ **ThereforeClient** - The Python API (already existed)
- ✅ **PYTHON_EXAMPLES.md** - Code patterns (just created)
- ✅ **API Guide** - Concepts and workflows (already existed)
- ✅ **Knowledge Base** - Structured info (already existed)

**The MCP server is for:**
- LLMs to *execute* Therefore operations via tool calls
- Runtime integration with Therefore

**For writing Python code, LLMs need:**
- The ThereforeClient API reference
- Python code examples (now available)
- API concepts documentation

You're now fully equipped! 🎉
