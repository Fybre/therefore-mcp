#!/bin/bash
# Test Therefore MCP Server

echo "Testing Therefore MCP Server..."
echo ""

# Test initialize
python3 src/mcp_server.py <<'EOF'
Content-Length: 129

{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}

Content-Length: 49

{"jsonrpc":"2.0","method":"initialized","params":{}}

Content-Length: 44

{"jsonrpc":"2.0","id":2,"method":"tools/list"}

EOF

echo ""
echo "If you see tool definitions above, the MCP server is working!"
