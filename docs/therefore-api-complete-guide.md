# Therefore™ WebAPI Complete Guide

**Version:** v0001
**Last Updated:** 2026-02-14
**Base URL Pattern:** `https://{tenant}.thereforeonline.com/theservice/v0001/restun`

## Table of Contents

1. [Authentication](#authentication)
2. [Common Workflows](#common-workflows)
3. [Query Operations](#query-operations)
4. [Document Operations](#document-operations)
5. [Working with Tables](#working-with-tables)
6. [Field Types & Mappings](#field-types--mappings)
7. [Error Handling](#error-handling)
8. [API Quirks & Known Issues](#api-quirks--known-issues)

---

## Authentication

### Basic Authentication

All requests require:
- **Content-Type:** `application/json; charset=utf-8`
- **Authorization:** `Basic {base64(username:password)}`
- **TenantName:** `{tenant}` (custom header)

**Example:**
```bash
curl -X POST "https://craigdemo.thereforeonline.com/theservice/v0001/restun/GetConnectedUser" \
  -H "Content-Type: application/json; charset=utf-8" \
  -H "Authorization: Basic Y3JhaWcubWV3ZXR0OlRoZSMxMjM0" \
  -H "TenantName: craigdemo" \
  -d '{}'
```

### Bearer Token Authentication

```bash
curl -X POST "https://{tenant}.thereforeonline.com/theservice/v0001/restun/..." \
  -H "Authorization: Bearer {token}" \
  -H "TenantName: {tenant}"
```

---

## Common Workflows

### Workflow 1: Query Documents with Filter and Iterate Results

**Use Case:** Find all documents in a category where a field matches a specific value, then process each document including table data.

**Steps:**

1. **Execute Async Query**
```http
POST /ExecuteAsyncSingleQuery
```
```json
{
  "Query": {
    "CategoryNo": 270,
    "MaxRows": 10000,
    "RowBlockSize": 1000,
    "Conditions": [
      {
        "FieldNoOrName": "Order_No",
        "Condition": "Order_No = '12345'"
      }
    ]
  }
}
```

**Response:**
```json
{
  "QueryId": 123456,
  "QueryResult": {
    "ResultRows": [
      {
        "DocNo": 789,
        "VersionNo": 1,
        "IndexValues": ["12345", "ACME Corp", "2024-01-15"]
      }
    ],
    "Columns": [
      {"FieldNo": 101, "ColName": "Order_No", "FieldType": 0},
      {"FieldNo": 102, "ColName": "Customer", "FieldType": 0},
      {"FieldNo": 103, "ColName": "Order_Date", "FieldType": 3}
    ]
  },
  "HasRemainingRows": true
}
```

2. **Fetch Additional Batches (if HasRemainingRows = true)**
```http
POST /GetNextSingleQueryRows
```
```json
{
  "QueryID": 123456,
  "RowBlockSize": 1000
}
```

3. **Get Detailed Document Data (including table values)**
```http
POST /GetDocumentIndexData
```
```json
{
  "DocNo": 789,
  "VersionNo": 1,
  "CategoryNo": 270
}
```

**Response:**
```json
{
  "IndexDataItems": [
    {
      "StringIndexData": {
        "FieldNo": 101,
        "DataValue": "12345",
        "FieldName": "Order_No"
      }
    },
    {
      "TableData": {
        "FieldNo": 150,
        "FieldName": "Line_Items",
        "Rows": [
          {
            "RowNo": 1,
            "Values": [
              {
                "FieldNo": 151,
                "StringIndexData": {"DataValue": "Product A"}
              },
              {
                "FieldNo": 152,
                "IntIndexData": {"DataValue": 5}
              }
            ]
          }
        ]
      }
    }
  ]
}
```

4. **Release Query Session**
```http
POST /ReleaseSingleQuery
```
```json
{
  "QueryID": 123456
}
```

### Workflow 2: Create Document (Web-Client Flow)

**Use Case:** Create a new document with index data using the 4-step web-client workflow.

**Steps:**

1. **Get Category Info**
```http
POST /GetCategoryInfo
```
```json
{
  "CategoryNo": 270
}
```

2. **Preprocess Index Data**
```http
POST /PreprocessIndexData
```
```json
{
  "CategoryNo": 270,
  "IndexDataItems": [
    {
      "StringIndexData": {
        "FieldNo": 101,
        "DataValue": "12345"
      }
    }
  ]
}
```

3. **Evaluate Conditional Properties**
```http
POST /EvaluateConditionalProperties
```
```json
{
  "CategoryNo": 270,
  "IndexDataItems": [/* preprocessed data */]
}
```

4. **Create Document**
```http
POST /CreateDocument
```
```json
{
  "CategoryNo": 270,
  "IndexDataItems": [/* evaluated data */],
  "StreamInfo": [
    {
      "FileName": "document.pdf",
      "StreamData": "base64-encoded-content"
    }
  ]
}
```

---

## Query Operations

### Query Result Mapping

The `IndexValues` array in each `ResultRow` maps **positionally** to the `Columns` array:

```python
# IndexValues[i] corresponds to Columns[i].ColName
def map_row_to_dict(row, columns):
    result = {"DocNo": row["DocNo"], "VersionNo": row["VersionNo"]}
    for i, value in enumerate(row.get("IndexValues", [])):
        if i < len(columns):
            result[columns[i]["ColName"]] = value
    return result
```

### Query Condition Syntax

**Exact Match:**
```json
{"FieldNoOrName": "Order_No", "Condition": "Order_No = '12345'"}
```

**Wildcard Search:**
```json
{"FieldNoOrName": "Customer", "Condition": "Customer LIKE 'ACME%'"}
```

**Numeric Comparison:**
```json
{"FieldNoOrName": "Amount", "Condition": "Amount > 1000"}
```

**Date Range:**
```json
{"FieldNoOrName": "Order_Date", "Condition": "Order_Date >= '2024-01-01' AND Order_Date <= '2024-12-31'"}
```

**Multiple Conditions (AND):**
```json
"Conditions": [
  {"FieldNoOrName": "Status", "Condition": "Status = 'Active'"},
  {"FieldNoOrName": "Amount", "Condition": "Amount > 500"}
]
```

**IS NULL / IS NOT NULL:**
```json
{"FieldNoOrName": "Notes", "Condition": "Notes IS NULL"}
{"FieldNoOrName": "Email", "Condition": "Email IS NOT NULL"}
```

### Field Selection

Limit returned fields using `SelectedFieldsNoOrNames`:

```json
{
  "Query": {
    "CategoryNo": 270,
    "SelectedFieldsNoOrNames": ["Order_No", "Customer", "Total"],
    "Conditions": [...]
  }
}
```

### Sorting Results

```json
{
  "Query": {
    "CategoryNo": 270,
    "OrderByFieldsNoOrNames": ["Order_Date", "Order_No"]
  }
}
```

---

## Working with Tables

### Identifying Table Fields

Table fields in the `Columns` array have:
- `BelongsToTable` > 0
- `TypeTableName` set to the table name
- Parent table field has matching `FieldNo`

### Getting Structured Table Data

**Always use `GetDocumentIndexData`** for proper table parsing:

```http
POST /GetDocumentIndexData
```
```json
{
  "DocNo": 789,
  "VersionNo": 1,
  "CategoryNo": 270
}
```

### Creating Documents with Table Data

```json
{
  "CategoryNo": 270,
  "IndexDataItems": [
    {
      "TableData": {
        "FieldNo": 150,
        "Rows": [
          {
            "RowNo": 1,
            "Values": [
              {
                "FieldNo": 151,
                "StringIndexData": {"DataValue": "Item 1"}
              },
              {
                "FieldNo": 152,
                "IntIndexData": {"DataValue": 10}
              }
            ]
          },
          {
            "RowNo": 2,
            "Values": [
              {
                "FieldNo": 151,
                "StringIndexData": {"DataValue": "Item 2"}
              },
              {
                "FieldNo": 152,
                "IntIndexData": {"DataValue": 5}
              }
            ]
          }
        ]
      }
    }
  ]
}
```

---

## Field Types & Mappings

### FieldType Enumeration

| FieldType | Name | Index Data Type | Example Value |
|-----------|------|-----------------|---------------|
| 0 | StringField | StringIndexData | "ABC123" |
| 1 | IntField | IntIndexData | 42 |
| 2 | DateField | DateIndexData | "2024-01-15" |
| 3 | DateTimeField | DateIndexData | "2024-01-15T14:30:00" |
| 4 | LogicalField | LogicalIndexData | true |
| 5 | MoneyField | MoneyIndexData | 1234.56 |
| 6 | KeywordField | SingleKeywordData | "Keyword Value" |
| 7 | MultiKeywordField | MultipleKeywordData | ["KW1", "KW2"] |
| 9 | TableField | TableData | (see table structure) |

### Index Data Structure by Type

**String:**
```json
{
  "StringIndexData": {
    "FieldNo": 101,
    "DataValue": "Text value",
    "FieldName": "Order_No"
  }
}
```

**Integer:**
```json
{
  "IntIndexData": {
    "FieldNo": 102,
    "DataValue": 42,
    "FieldName": "Quantity"
  }
}
```

**Date/DateTime:**
```json
{
  "DateIndexData": {
    "FieldNo": 103,
    "DataValue": "2024-01-15T14:30:00",
    "DataISO8601Value": "2024-01-15T14:30:00Z",
    "FieldName": "Order_Date"
  }
}
```

**Money:**
```json
{
  "MoneyIndexData": {
    "FieldNo": 104,
    "DataValue": 1234.56,
    "DecimalDataValue": 1234.56,
    "FieldName": "Total_Amount"
  }
}
```

**Keyword (Single):**
```json
{
  "SingleKeywordData": {
    "FieldNo": 105,
    "DataValue": "Active",
    "KeywordNo": 42,
    "FieldName": "Status"
  }
}
```

**Keyword (Multiple):**
```json
{
  "MultipleKeywordData": {
    "FieldNo": 106,
    "DataValue": ["Tag1", "Tag2", "Tag3"],
    "KeywordNos": [10, 11, 12],
    "FieldName": "Tags"
  }
}
```

---

## Error Handling

### Common Error Responses

**Invalid Credentials:**
```json
{
  "Error": {
    "ErrorCode": 401,
    "Message": "Unauthorized"
  }
}
```

**Invalid Category:**
```json
{
  "Error": {
    "ErrorCode": -2147467259,
    "Message": "Category not found"
  }
}
```

**Field Not Found:**
```json
{
  "Error": {
    "ErrorCode": -2147024809,
    "Message": "Field 'InvalidField' not found in category"
  }
}
```

### Retry Pattern

```python
import time

def execute_with_retry(func, max_retries=3, timeout=120):
    for attempt in range(max_retries):
        try:
            return func()
        except TimeoutError:
            if attempt < max_retries - 1:
                time.sleep(5 * (attempt + 1))  # Exponential backoff
                continue
            raise
```

---

## API Quirks & Known Issues

### 1. Non-Therefore User Accounts Return UserId: 0

**Issue:** AD/LDAP users always return `UserId: 0` from user resolution APIs.

**Workaround:** Only native Therefore accounts have real UserIds. Use username for AD/LDAP users.

### 2. DeleteDictionaryKeyword Doesn't Actually Delete

**Issue:** `DeleteDictionaryKeyword` returns success but doesn't remove in-use keywords.

**Workaround:** Check keyword usage before attempting deletion.

### 3. Keyword Fields Require KeywordNos, Not Strings

**Issue:** Passing string values to keyword fields causes conversion errors.

**Solution:** Always resolve keyword strings to KeywordNos using `GetDictionaryInfo` first:

```json
// WRONG:
{"SingleKeywordData": {"FieldNo": 105, "DataValue": "Active"}}

// CORRECT:
{"SingleKeywordData": {"FieldNo": 105, "KeywordNo": 42}}
```

### 4. Query Sessions Must Be Released

**Issue:** Unreleased query sessions consume server resources.

**Solution:** Always call `ReleaseSingleQuery` or `ReleaseMultiQuery` in a `finally` block:

```python
query_id = None
try:
    result = execute_async_single_query(query)
    query_id = result["QueryId"]
    # Process results...
finally:
    if query_id:
        release_single_query(query_id)
```

### 5. Table Data in Query Results Is Concatenated

**Issue:** Table field values in `IndexValues` are delimited strings, not structured data.

**Solution:** Use `GetDocumentIndexData` to retrieve structured table data.

### 6. Timezone Handling in Conditions

**Issue:** Date/DateTime conditions may need timezone specification.

**Solution:** Include `TimeZone` field in conditions:

```json
{
  "FieldNoOrName": "Order_Date",
  "Condition": "Order_Date >= '2024-01-01'",
  "TimeZone": 0  // 0 = UTC
}
```

---

## Quick Reference

### Most Common Endpoints

| Operation | Endpoint | Purpose |
|-----------|----------|---------|
| Query documents | ExecuteAsyncSingleQuery | Search/filter documents |
| Get next batch | GetNextSingleQueryRows | Paginate query results |
| Release query | ReleaseSingleQuery | Free query resources |
| Get document data | GetDocumentIndexData | Retrieve full index data |
| Get category info | GetCategoryInfo | Get field definitions |
| Create document | CreateDocument | Create new document |
| Update document | UpdateDocument | Modify existing document |
| Get dictionary | GetDictionaryInfo | Get keyword values |

### Authentication Quick Reference

```python
import base64

# Basic Auth
auth_header = base64.b64encode(b"username:password").decode("ascii")
headers = {
    "Authorization": f"Basic {auth_header}",
    "TenantName": "tenant-name",
    "Content-Type": "application/json; charset=utf-8"
}
```

### Complete Query Example (Python)

```python
import requests
import base64

base_url = "https://craigdemo.thereforeonline.com/theservice/v0001/restun"
auth = base64.b64encode(b"username:password").decode("ascii")
headers = {
    "Authorization": f"Basic {auth}",
    "TenantName": "craigdemo",
    "Content-Type": "application/json; charset=utf-8"
}

# Execute query
response = requests.post(
    f"{base_url}/ExecuteAsyncSingleQuery",
    headers=headers,
    json={
        "Query": {
            "CategoryNo": 270,
            "MaxRows": 10000,
            "RowBlockSize": 1000,
            "Conditions": [{
                "FieldNoOrName": "Order_No",
                "Condition": "Order_No = '12345'"
            }]
        }
    }
)

data = response.json()
query_id = data["QueryId"]
columns = data["QueryResult"]["Columns"]
column_names = [c["ColName"] for c in columns]

# Process results
try:
    for row in data["QueryResult"]["ResultRows"]:
        doc = {"DocNo": row["DocNo"]}
        for i, value in enumerate(row["IndexValues"]):
            doc[column_names[i]] = value

        print(f"Document: {doc}")

    # Fetch more batches if needed
    while data.get("HasRemainingRows"):
        data = requests.post(
            f"{base_url}/GetNextSingleQueryRows",
            headers=headers,
            json={"QueryID": query_id, "RowBlockSize": 1000}
        ).json()

        for row in data["QueryResult"]["ResultRows"]:
            doc = {"DocNo": row["DocNo"]}
            for i, value in enumerate(row["IndexValues"]):
                doc[column_names[i]] = value
            print(f"Document: {doc}")

finally:
    # Always release the query
    requests.post(
        f"{base_url}/ReleaseSingleQuery",
        headers=headers,
        json={"QueryID": query_id}
    )
```

---

## Additional Resources

- **API Endpoint Reference:** See `docs/export/tenant_operations.json`
- **Field Type Constants:** See `docs/specs/therefore_constants.json`
- **Code Examples:** See `src/therefore_client.py` and `src/mcp_server.py`
- **Validation Reports:** See `docs/notes/validation_report.md`
