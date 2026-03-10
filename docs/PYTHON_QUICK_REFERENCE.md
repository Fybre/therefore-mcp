# Therefore Python Quick Reference

Ultra-condensed guide for writing Therefore Python code. Use with `src/therefore_client.py`.

## Setup

```python
from therefore_client import ThereforeClient

client = ThereforeClient(
    base_url="https://demo.thereforeonline.com/theservice/v0001/restun",
    username="user", password="pass"
)
```

## Field Types (TypeNo)

| Type | TypeNo | IndexData Structure | Example |
|------|--------|---------------------|---------|
| String | 0 | `StringIndexData` | `{"Value": {"StringIndexData": {"Value": "text"}}}` |
| Integer | 1 | `IntIndexData` | `{"Value": {"IntIndexData": {"Value": 42}}}` |
| Date | 2 | `DateIndexData` | `{"Value": {"DateIndexData": {"Value": "2024-01-15"}}}` |
| DateTime | 3 | `DateTimeIndexData` | `{"Value": {"DateTimeIndexData": {"Value": "2024-01-15T10:30:00"}}}` |
| Money | 5 | `MoneyIndexData` | `{"Value": {"MoneyIndexData": {"Value": 99.99}}}` |
| Keyword | 6 | `KeywordIndexData` | `{"Value": {"KeywordIndexData": {"KeywordNo": 42}}}` ⚠️ Use ID not name! |
| Table | 9 | `TableData` | `{"Value": {"TableData": {"Rows": [...]}}}` |

## Common Operations

### Check if Document Exists
```python
try:
    doc = client.get_document(doc_no, include_index_data=False)
    exists = True
except: exists = False
```

### Query by Field Value
```python
query = {
    "CategoryNo": 123,
    "WhereClause": "[FieldColName] = 'value'"
}
result = client.execute_single_query(query)
rows = result.get('IndexDataRows', [])
```

### Get Document with Index Data
```python
doc = client.get_document(doc_no, include_index_data=True)
# Map fields to values using IndexDataDef + IndexData
```

### Create Document
```python
index_data = [
    {"Name": "FieldName", "Value": {"StringIndexData": {"Value": "text"}}}
]
streams = [client.make_stream_from_text("file.txt", "content")]

result = client.create_document(
    category_no=123,
    streams=streams,
    index_data_items=index_data
)
doc_no = result.get('DocNo')
```

## Critical Patterns

**Keyword Fields:** Must use KeywordNo (ID), not keyword name string
```python
# WRONG: {"Value": {"KeywordIndexData": {"Value": "Active"}}}
# RIGHT: {"Value": {"KeywordIndexData": {"KeywordNo": 42}}}
```

**Table Data in Queries:** Returns concatenated strings, use GetDocumentIndexData for structured data
```python
# Query returns: "row1col1|row1col2\nrow2col1|row2col2"
# Better: client.get_document_index_data(doc_no)
```

**Index Values Mapping:** `IndexValues[i]` maps to `Columns[i].ColName` (positional)

**Error Handling:**
```python
try:
    doc = client.get_document(doc_no)
except Exception as e:
    if "not found" in str(e).lower(): # Document doesn't exist
    elif "unauthorized" in str(e).lower(): # Access denied
    else: # Other error
```

## XML Processing Pattern

```python
import xml.etree.ElementTree as ET

# Parse XML
tree = ET.parse('file.xml')
for elem in tree.findall('.//Item'):
    item_id = elem.get('id')

    # Check if exists
    query = {"WhereClause": f"[ItemID] = '{item_id}'"}
    result = client.execute_single_query(query)

    if result.get('IndexDataRows'):
        continue  # Already exists

    # Create if missing
    client.create_document(category_no=123, ...)
```

## Query Optimization

**Multiple docs:** Use `IN` clause instead of multiple calls
```python
doc_ids = [100, 101, 102]
query = {"WhereClause": f"[DocNo] IN ({','.join(map(str, doc_ids))})"}
```

## See Also

- `src/therefore_client.py` - Full API
- `docs/PYTHON_EXAMPLES.md` - Complete examples
