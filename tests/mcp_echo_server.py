"""Minimal MCP server used by the client tests. Speaks newline-delimited
JSON-RPC on stdio: initialize, tools/list, and tools/call for one echo tool.

Run directly: python tests/mcp_echo_server.py
"""

import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the given text back",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "repeat": {"type": "integer", "default": 1},
            },
            "required": ["text"],
        },
    }
]


def reply(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        message["error"] = error
    else:
        message["result"] = result
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method", "")
        request_id = request.get("id")
        if request_id is None:
            continue  # notification — nothing to answer
        if method == "initialize":
            reply(
                request_id,
                {
                    "protocolVersion": request["params"]["protocolVersion"],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "echo-server", "version": "1.0"},
                },
            )
        elif method == "tools/list":
            reply(request_id, {"tools": TOOLS})
        elif method == "tools/call":
            params = request.get("params", {})
            if params.get("name") != "echo":
                reply(request_id, error={"code": -32602, "message": "unknown tool"})
                continue
            args = params.get("arguments", {})
            text = args.get("text", "")
            repeat = int(args.get("repeat") or 1)
            reply(
                request_id,
                {
                    "content": [{"type": "text", "text": ("echo:" + text) * repeat}],
                    "isError": False,
                },
            )
        else:
            reply(request_id, error={"code": -32601, "message": f"no method {method}"})


if __name__ == "__main__":
    main()
