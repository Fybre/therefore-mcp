#!/usr/bin/env python3
"""
Regression tests for the ask_therefore_expert router and the tool/operation surface
it routes to.

Unlike the old version of this file, these tests import and exercise the REAL router
code (MCPServer._ask_therefore_expert) instead of a hand-copied, drifted duplicate of
its keyword table - a duplicate can never catch a regression in the real logic, which
is how the "search" vs "search users" / "workflow" vs "workflow tasks" misrouting and
the CreateCase param-name bug shipped unnoticed. Run directly: `python3 test_expert_router.py`.
Exits non-zero on any failure.
"""
import json
import os
import re
import sys

sys.path.insert(0, 'src')

from mcp_server import MCPServer, OPERATION_REGISTRY
from therefore_client import ThereforeClient, ThereforeConfig

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def make_server():
    # A stub client is enough - none of these tests need a live network call, they only
    # exercise routing/registry logic. Use a syntactically valid but unreachable config.
    cfg = ThereforeConfig(
        base_url="https://example.invalid/theservice/v0001/restun",
        username="test",
        password="test",
        tenant_name="test",
        auth_method="Basic",
    )
    client = ThereforeClient(cfg)
    return MCPServer(clients={"test": client}, default_tenant="test", tenant_labels={"test": "Test"})


def ask(server, question):
    resp = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "ask_therefore_expert", "arguments": {"question": question}},
    })
    return json.loads(resp["result"]["content"][0]["text"])


def test_router_picks_most_specific_keyword():
    """Regression test: shorter keywords must not shadow more specific ones that
    contain them, regardless of dict insertion order (found 2026-07-16)."""
    server = make_server()
    cases = [
        ("search users by name", "therefore_users", "search"),
        ("I want to do a full text search", "therefore_query", "search_fulltext"),
        ("show me workflow tasks", "therefore_workflow", "get_my_tasks"),
        ("list workflow instances", "therefore_workflow", "get_all_instances"),
        ("search documents for invoices", "therefore_query", "search"),
        ("what workflow do I have", "therefore_workflow", "query_all"),
    ]
    for question, exp_tool, exp_op in cases:
        result = ask(server, question)
        got = (result.get("suggested_tool"), result.get("suggested_operation"))
        check(
            f"routes {question!r} to {exp_tool}/{exp_op}",
            got == (exp_tool, exp_op),
            f"got {got}",
        )


def test_router_uses_word_boundaries():
    """Regression test: a keyword like "search" must not match inside an unrelated
    word like "research" (found 2026-07-16)."""
    server = make_server()
    result = ask(server, "I'm doing academic research on document types")
    check(
        "\"search\" keyword does not match inside \"research\"",
        result.get("suggested_tool") != "therefore_query"
        or result.get("suggested_operation") != "search",
    )


def test_shadowed_keywords_still_resolve_correctly():
    """Keyword pairs where one contains the other (e.g. "search" / "search users")
    are expected to coexist in tool_suggestions - the router's longest-match rule
    is what's supposed to disambiguate them. This walks every such pair found in the
    table and confirms the SHORTER keyword's own question still routes to the
    shorter keyword's target when asked in isolation, i.e. the overlap doesn't
    accidentally break the short keyword's own matching."""
    src = open("src/mcp_server.py").read()
    m = re.search(r'tool_suggestions = \{(.*?)\n        \}\n', src, re.S)
    entries = re.findall(r'"([^"]+)":\s*\{"tool":\s*"([^"]+)",\s*"operation":\s*"([^"]+)"\}', m.group(1))

    server = make_server()
    for kw, tool, op in entries:
        result = ask(server, kw)
        got = (result.get("suggested_tool"), result.get("suggested_operation"))
        check(f'keyword {kw!r} alone still routes to {tool}/{op}', got == (tool, op), f"got {got}")


def test_router_suggestions_are_all_valid():
    """Every (tool, operation) the router can suggest - via the keyword table or the
    fuzzy-match fallback - must exist in OPERATION_REGISTRY AND be handled by that
    tool's dispatch function. Otherwise the router can recommend a call that 500s
    with 'Unknown operation'."""
    src = open("src/mcp_server.py").read()

    m = re.search(r'tool_suggestions = \{(.*?)\n        \}\n', src, re.S)
    keyword_pairs = set(re.findall(r'\{"tool":\s*"([^"]+)",\s*"operation":\s*"([^"]+)"\}', m.group(1)))

    dispatch_fns = re.findall(
        r'def (_dispatch_\w+)\(self, args, tenant, client\):(.*?)(?=\n    def |\Z)', src, re.S
    )
    handled = {}
    for name, body in dispatch_fns:
        tool = "therefore_" + name.replace("_dispatch_", "")
        handled[tool] = set(re.findall(r'op == "(\w+)"', body))

    for tool, op in sorted(keyword_pairs):
        check(
            f"router target ({tool}, {op}) is registered and dispatched",
            (tool, op) in OPERATION_REGISTRY and op in handled.get(tool, set()),
        )


def test_operation_registry_fully_dispatched():
    """Every entry in OPERATION_REGISTRY - reachable via the fuzzy-match fallback,
    not just the keyword table - must be handled by its tool's dispatch function."""
    src = open("src/mcp_server.py").read()
    dispatch_fns = re.findall(
        r'def (_dispatch_\w+)\(self, args, tenant, client\):(.*?)(?=\n    def |\Z)', src, re.S
    )
    handled = {}
    for name, body in dispatch_fns:
        tool = "therefore_" + name.replace("_dispatch_", "")
        handled[tool] = set(re.findall(r'op == "(\w+)"', body))

    for (tool, op) in sorted(OPERATION_REGISTRY.keys()):
        check(
            f"registry entry ({tool}, {op}) is handled by dispatch",
            op in handled.get(tool, set()),
        )


def test_no_dispatch_calls_nonexistent_endpoint():
    """Every string literal passed as the endpoint name to ThereforeClient._post(...)
    must be a real WSDL operation. This is exactly the class of bug that shipped
    silently in add_comment/get_comments (wrong endpoint name entirely) and
    get_document_versions (endpoint doesn't exist) - both sat broken until this test
    was written because nothing exercised those code paths. Requires network access
    to fetch the WSDL; skips gracefully if unreachable (e.g. offline CI)."""
    import urllib.request

    env_path = os.environ.get("THEREFORE_ENV_PATH", ".env.local")
    tenant, user, pwd, base = None, None, None, None
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("THEREFORE_CRAIGDEMO_BASE_URL="):
                    base = line.split("=", 1)[1]
                if line.startswith("THEREFORE_CRAIGDEMO_USERNAME="):
                    user = line.split("=", 1)[1]
                if line.startswith("THEREFORE_CRAIGDEMO_PASSWORD="):
                    pwd = line.split("=", 1)[1]
    except FileNotFoundError:
        pass

    if not (base and user and pwd):
        print("[SKIP] no-dead-endpoints check -- no craigdemo credentials in .env.local")
        return

    wsdl_url = base.rsplit("/restun", 1)[0] + "?wsdl"
    try:
        import base64
        req = urllib.request.Request(wsdl_url)
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode())
        with urllib.request.urlopen(req, timeout=15) as resp:
            wsdl_text = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"[SKIP] no-dead-endpoints check -- could not fetch WSDL ({e})")
        return

    real_ops = set(re.findall(r'wsdl:operation name="([^"]+)"', wsdl_text))

    client_src = open("src/therefore_client.py").read()
    called_ops = set(re.findall(r"_post\(\s*'([A-Za-z0-9]+)'", client_src))
    called_ops |= set(re.findall(r"_post\(\s*f'([A-Za-z0-9]+)", client_src))

    dead = sorted(called_ops - real_ops)
    check(
        "every ThereforeClient._post(...) endpoint literal exists in the live WSDL",
        not dead,
        f"dead endpoint names: {dead}",
    )


if __name__ == "__main__":
    print("Expert Router & Tool Surface Regression Tests")
    print("=" * 80)

    test_router_picks_most_specific_keyword()
    test_router_uses_word_boundaries()
    test_shadowed_keywords_still_resolve_correctly()
    test_router_suggestions_are_all_valid()
    test_operation_registry_fully_dispatched()
    test_no_dispatch_calls_nonexistent_endpoint()

    print("=" * 80)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")
