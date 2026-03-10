# Therefore Knowledge Tools - Quick Reference

## 🚀 8 Knowledge Tools for Natural Language Queries

### ⭐ ask_therefore_expert (RECOMMENDED START HERE)
**One-stop shop for all Therefore questions**
```json
{"question": "how do I get the customer ID?"}
```
Auto-routes to the right knowledge/tools. Handles:
- Customer ID, logs, queries, creation, structure, troubleshooting

### 🔍 search_therefore_knowledge
**Search the knowledge base**
```json
{"query": "how to filter by date", "limit": 5}
```

### 📋 get_therefore_workflow
**Get step-by-step workflows**
```json
{"workflow_name": "query_documents_with_filter"}
```
Available: `query_documents_with_filter`, `create_document_web_client_flow`

### 📊 get_therefore_field_type_info
**Get field type structures**
```json
{"field_type": "TableField"}
```
Types: `StringField` (0), `IntField` (1), `TableField` (9), etc.

### 💡 get_therefore_common_pattern
**Get coding patterns**
```json
{"pattern_name": "map_index_values_to_columns"}
```
Patterns: `map_index_values_to_columns`, `query_condition_syntax`, `batch_query_results`

### ⚠️ get_therefore_api_quirks
**Get known issues**
```json
{"search": "keyword"}
```

### 📚 list_therefore_knowledge
**List all resources**
```json
{}
```

### 🌐 get_therefore_api_help
**Fetch live API help docs**
```json
{"operation": "ExecuteAsyncSingleQuery", "format": "text"}
```
Formats: `html`, `text`, `json` | Omit operation for index

---

## 💬 Natural Language Examples

**"How do I query documents with a filter?"**
→ Returns complete workflow with all 4 steps and code examples

**"What's the structure for table fields?"**
→ Returns TableData structure with complete example

**"How do I map IndexValues to column names?"**
→ Returns pattern with Python and JavaScript examples

**"My keyword field isn't working"**
→ Returns quirks about keyword fields requiring KeywordNos

---

## 🧪 Test It

```bash
python3 test_knowledge_tools.py
```

---

## 📖 Full Documentation

- **Usage Guide:** `docs/KNOWLEDGE_TOOLS_USAGE.md`
- **API Guide:** `docs/therefore-api-complete-guide.md`
- **Knowledge Base:** `docs/knowledge-base.json`
- **System Guide:** `docs/AI_KNOWLEDGE_SYSTEM.md`

---

**Total MCP Tools:** 96 (88 Therefore API + 8 Knowledge)
**MCP Prompts:** 5 (`/therefore-help`, `/query-documents`, `/create-document`, `/troubleshoot`, `/create-category`)
