# Therefore MCP - AI Usage Guide

**For:** Perplexity, Claude, ChatGPT, and other AI systems using this MCP server

---

## ⭐ START HERE: The One Tool You Need

### `ask_therefore_expert` - Use This First!

**For ANY Therefore question**, call this tool:

```json
{
  "tool": "ask_therefore_expert",
  "args": {"question": "YOUR QUESTION HERE"}
}
```

**It handles:**
- ✅ "What is the customer/client/system/tenant ID?" → Returns actual ID
- ✅ "How do I query documents?" → Returns workflow
- ✅ "Summarize logs for X days" → Returns summary
- ✅ "How to create documents?" → Returns steps
- ✅ "What's the structure for [field]?" → Returns structure
- ✅ "Why isn't X working?" → Returns troubleshooting

**Returns:** Direct answer with actual data (not just docs)

---

## Common Questions - Quick Reference

### Question: "What is the client ID for craigdemo?"

**❌ WRONG:**
```json
{"tool": "GetOAuthClients"}  // This is for OAuth configuration, not customer ID!
```

**✅ CORRECT - Option 1 (Recommended):**
```json
{
  "tool": "ask_therefore_expert",
  "args": {"question": "what is the client id"}
}
```

**Response:**
```json
{
  "answer": "The system customer ID is: XN4BRR3OHD",
  "customer_id": "XN4BRR3OHD",
  "how_to_get_it": {
    "tool": "get_system_customer_id",
    "example": "result = client.get_system_customer_id()..."
  }
}
```

**✅ CORRECT - Option 2 (Direct):**
```json
{
  "tool": "get_system_customer_id"
}
```

**Response:**
```json
{
  "CustomerId": "XN4BRR3OHD"
}
```

**Key Point:** "Client ID" = "Customer ID" = "System ID" = "Tenant ID" in Therefore terminology. They all mean the same thing!

---

### Question: "How do I query documents with a filter?"

**✅ CORRECT:**
```json
{
  "tool": "ask_therefore_expert",
  "args": {"question": "how do I query documents with a filter"}
}
```

**Response:**
```json
{
  "answer": "To query documents in Therefore:\n\n1. ExecuteAsyncSingleQuery - Initiate async query...\n2. GetNextSingleQueryRows - Fetch additional batches...\n3. GetDocumentIndexData - Get detailed data...\n4. ReleaseSingleQuery - Release resources",
  "workflow_name": "Query Documents with Field Filter",
  "total_steps": 4,
  "full_workflow": {...}
}
```

---

### Question: "Summarize logs for the last 7 days"

**✅ CORRECT:**
```json
{
  "tool": "ask_therefore_expert",
  "args": {"question": "summarize logs for the last 7 days"}
}
```

**Response:**
```json
{
  "answer": "Log summary for the last 7 days:\nTotal entries: 150\nBreakdown by severity:\n  - Error: 12\n  - Warning: 38\n  - Info: 100",
  "days": 7,
  "total_entries": 150,
  "date_range": "2026-02-07 to 2026-02-14"
}
```

---

## Terminology Reference

Therefore uses different terms for the same concepts:

| User Might Say | Therefore API Calls It | Tool to Use |
|----------------|------------------------|-------------|
| Client ID | CustomerId | get_system_customer_id |
| Customer ID | CustomerId | get_system_customer_id |
| System ID | CustomerId | get_system_customer_id |
| Tenant ID | CustomerId | get_system_customer_id |
| OAuth Client | OAuth application | (Different - use GetOAuthClients) |

**Important:** "Client ID" in Therefore context usually means the **CustomerId**, NOT an OAuth client!

---

## Decision Flow for AI Systems

```
User asks a question about Therefore
    ↓
Does it match a common pattern?
    YES → Use ask_therefore_expert
        ↓
        Returns direct answer
        ↓
        Present answer to user ✅

    NO → Is it a very specific API call?
        YES → Use specific tool (e.g., get_system_customer_id)
        NO → Use ask_therefore_expert anyway (it will search)
```

---

## Why Perplexity's Response Was Wrong

### What Happened
```
User: "what is the client id for the craigdemo therefore client"

Perplexity called: GetOAuthClients
Result: Error/not found

Perplexity said: "I can't retrieve the OAuth client id..."
```

### Why It Failed
1. **Wrong tool** - GetOAuthClients is for OAuth configuration, not system identity
2. **Didn't use** `ask_therefore_expert` which would have auto-routed
3. **Confused terminology** - "client id" in Therefore = "customer id", not OAuth

### What Should Have Happened
```
User: "what is the client id for the craigdemo therefore client"

Perplexity calls: ask_therefore_expert
    with: {"question": "what is the client id"}

Result: {
    "answer": "The system customer ID is: XN4BRR3OHD",
    "customer_id": "XN4BRR3OHD"
}

Perplexity says: "The Therefore customer ID (also called client ID) for craigdemo is: **XN4BRR3OHD**"
```

---

## Common Mistakes to Avoid

### ❌ Don't Do This:
```json
// Guessing which API call to use
{"tool": "GetOAuthClients"}         // Wrong for customer ID
{"tool": "GetDomainInfo"}           // Wrong for customer ID
{"tool": "GetConnectedUser"}        // Wrong for customer ID
```

### ✅ Do This Instead:
```json
// Use the smart helper
{"tool": "ask_therefore_expert", "args": {"question": "what is the customer id"}}

// OR use the specific tool if you're sure
{"tool": "get_system_customer_id"}
```

---

## Tool Priority Order

When responding to user questions:

1. **First:** Try `ask_therefore_expert` - handles 80% of questions
2. **If need details:** Use specific tool mentioned in response
3. **For official docs:** Use `get_therefore_api_help`
4. **Never:** Guess which API endpoint to call

---

## Examples of Good AI Responses

### Example 1: Customer ID
```
User: "What is the client ID?"

AI → ask_therefore_expert({"question": "what is the client id"})
AI ← {
    "answer": "The system customer ID is: XN4BRR3OHD",
    "customer_id": "XN4BRR3OHD"
}

AI Response to User:
"The Therefore customer ID (also called client ID or system ID) for this tenant is: **XN4BRR3OHD**

This is retrieved using the `get_system_customer_id` API endpoint."
```

### Example 2: Query Documents
```
User: "How do I find documents with a specific order number?"

AI → ask_therefore_expert({"question": "how do I query documents with a filter"})
AI ← {
    "answer": "To query documents...(4 steps)...",
    "workflow_name": "Query Documents with Field Filter",
    "total_steps": 4
}

AI Response to User:
"To find documents with a specific order number in Therefore, follow this 4-step workflow:

1. **ExecuteAsyncSingleQuery** - Start the query with your filter
2. **GetNextSingleQueryRows** - Paginate through results
3. **GetDocumentIndexData** - Get detailed document data
4. **ReleaseSingleQuery** - Clean up (always required!)

Example query:
{
  "Query": {
    "CategoryNo": 270,
    "Conditions": [{
      "FieldNoOrName": "Order_No",
      "Condition": "Order_No = '12345'"
    }]
  }
}

The IndexValues in results map positionally to the Columns array returned."
```

---

## Summary

**For AI Systems:**
1. ✅ **Always start with** `ask_therefore_expert`
2. ✅ **Understand terminology** - "client ID" = "customer ID" in Therefore
3. ✅ **Return actual data** - not just "here's how to get it"
4. ❌ **Don't guess** which API endpoint to call

**The user asked for the client ID. The answer is:** `XN4BRR3OHD`

**Not:** "Here's how you can find it..." ❌
**But:** "The client ID is: XN4BRR3OHD" ✅
