# Using Prompt Caching for Therefore Documentation

Prompt caching lets you load documentation once and reuse it across multiple requests at minimal cost.

## What is Prompt Caching?

**Without caching:**
- Every request sends all 22K tokens of documentation
- Pay full price every time

**With caching:**
- First request: Send 22K tokens (full price)
- Subsequent requests: Reuse cached docs (90% discount)
- Cache lasts 5 minutes (Anthropic) to 1 hour

## Cost Comparison

### Anthropic Claude (with prompt caching)

**First request:**
- Input: 22K tokens (documentation) = $0.27
- Output: ~1K tokens (generated code) = $0.015
- Total: **$0.285**

**Subsequent requests (within 5 min):**
- Cached input: 22K tokens = $0.027 (90% off!)
- New input: ~200 tokens (your prompt) = $0.003
- Output: ~1K tokens = $0.015
- Total: **$0.045** (84% cheaper!)

**10 requests in a session:**
- Without caching: $2.85
- With caching: $0.69 (76% savings!)

### How to Use (Anthropic API)

```python
import anthropic

client = anthropic.Anthropic(api_key="...")

# Read documentation files
with open('src/therefore_client.py') as f:
    client_api = f.read()
with open('docs/PYTHON_QUICK_REFERENCE.md') as f:
    quick_ref = f.read()

# Create message with cache_control
response = client.messages.create(
    model="claude-3-5-sonnet-20241022",
    max_tokens=4096,
    system=[
        {
            "type": "text",
            "text": "You are a Therefore API expert. Help write Python code."
        },
        {
            "type": "text",
            "text": f"# Therefore Client API\n\n{client_api}",
            "cache_control": {"type": "ephemeral"}  # Cache this!
        },
        {
            "type": "text",
            "text": f"# Quick Reference\n\n{quick_ref}",
            "cache_control": {"type": "ephemeral"}  # Cache this!
        }
    ],
    messages=[
        {
            "role": "user",
            "content": "Write code to check if document 12345 exists"
        }
    ]
)
```

**Key points:**
- Add `cache_control: {"type": "ephemeral"}` to cache blocks
- Put documentation in system messages (they cache better)
- Cache lasts 5 minutes - perfect for interactive sessions
- 90% cost reduction on cached tokens!

### OpenAI (Prompt Caching)

```python
import openai

client = openai.OpenAI()

response = client.chat.completions.create(
    model="gpt-4-turbo-2024-04-09",
    messages=[
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "You are a Therefore API expert."},
                {
                    "type": "text",
                    "text": documentation_text,
                    "cache_control": {"type": "ephemeral"}
                }
            ]
        },
        {
            "role": "user",
            "content": "Write code to check if document exists"
        }
    ]
)
```

## When to Use Caching

**✅ Perfect for:**
- Interactive coding sessions
- Multiple questions about Therefore
- Iterative development
- AI coding assistants

**❌ Not useful for:**
- One-off questions
- Batch processing (each job starts fresh)
- Long delays between requests (cache expires)

## Cache Duration

| Provider | Duration | Notes |
|----------|----------|-------|
| Anthropic | 5 minutes | Refreshes on each use |
| OpenAI | ~1 hour | May vary |

## Best Practices

1. **Put stable content first** - Documentation that doesn't change
2. **Put variable content last** - User's specific request
3. **Use in interactive sessions** - Where you ask multiple questions
4. **Batch related tasks** - Do all Therefore work in one session

## Example Session (with caching)

```python
# Load docs once
system_with_docs = [
    {"type": "text", "text": "Therefore API expert"},
    {"type": "text", "text": therefore_client_code, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": quick_reference, "cache_control": {"type": "ephemeral"}}
]

# Request 1: Check if doc exists (full cost)
response1 = client.messages.create(
    system=system_with_docs,
    messages=[{"role": "user", "content": "Write code to check if doc 123 exists"}]
)

# Request 2: Query by field (90% off on docs!)
response2 = client.messages.create(
    system=system_with_docs,
    messages=[{"role": "user", "content": "Write code to query by invoice number"}]
)

# Request 3: Create document (still 90% off!)
response3 = client.messages.create(
    system=system_with_docs,
    messages=[{"role": "user", "content": "Write code to create invoice document"}]
)

# Saved: ~$2.16 on 3 requests!
```

## Summary

**Prompt caching is the answer!**

- ✅ Keep all documentation (full context)
- ✅ Pay once, reuse many times
- ✅ 76-90% cost savings in sessions
- ✅ No complexity (just add cache_control)
- ✅ Works perfectly for interactive coding

**Bottom line:** Don't reduce documentation - just cache it!
