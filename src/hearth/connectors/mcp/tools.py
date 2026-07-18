"""Bridge MCP server tools into Hearth's gated tool registry.

Safety posture: every MCP tool is WRITE-risk — Hearth cannot know what an
external server's tool does, so each call shows a confirmation card with the
server name, tool, and exact arguments. The 'mcp' permission (off by
default) gates the whole feature.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ConfigDict, create_model

from ...agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec
from ...config import MCPConfig
from .client import MCPError, MCPServerConnection

log = logging.getLogger(__name__)

_JSON_TO_PY = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def params_model_from_schema(model_name: str, schema: dict[str, Any] | None):
    """Build a Pydantic model from an MCP inputSchema (top-level properties).

    Unknown or missing schemas fall back to a pass-through model — the server
    still validates on its side, and the user still sees the exact arguments
    on the confirmation card.
    """
    properties = (schema or {}).get("properties") or {}
    required = set((schema or {}).get("required") or [])
    fields: dict[str, tuple] = {}
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        py_type = _JSON_TO_PY.get(prop.get("type"), Any)
        default = ... if key in required else prop.get("default", None)
        fields[key] = (py_type, default)
    if fields:
        return create_model(model_name, **fields)
    return create_model(model_name, __config__=ConfigDict(extra="allow"))


def _result_text(result: dict[str, Any]) -> str:
    """Flatten an MCP tools/call result into text for the model."""
    parts = []
    for item in result.get("content") or []:
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        else:
            parts.append(json.dumps(item, ensure_ascii=False, default=str))
    return "\n".join(parts) or json.dumps(result, ensure_ascii=False, default=str)


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", text).strip("_").lower()


class MCPManager:
    """Owns server connections and their registered tools."""

    def __init__(self, config: MCPConfig, registry: ToolRegistry):
        self._config = config
        self._registry = registry
        self._connections: list[MCPServerConnection] = []
        self.started = False

    async def start(self) -> None:
        """Connect every configured server and register its tools. Idempotent."""
        if self.started:
            return
        self.started = True
        for server in self._config.servers:
            connection = MCPServerConnection(server.name, server.command, list(server.args))
            try:
                await connection.start()
            except (OSError, MCPError) as exc:
                log.error("MCP server '%s' failed to start: %s", server.name, exc)
                connection.kill()
                continue
            self._connections.append(connection)
            self._register_tools(connection)

    def _register_tools(self, connection: MCPServerConnection) -> None:
        for tool in connection.tools:
            tool_name = tool.get("name", "")
            if not tool_name:
                continue
            registered_name = f"mcp_{_slug(connection.name)}_{_slug(tool_name)}"
            params_model = params_model_from_schema(
                f"MCP_{_slug(connection.name)}_{_slug(tool_name)}", tool.get("inputSchema")
            )

            def make_handler(conn: MCPServerConnection, name: str):
                async def handler(params) -> ToolResult:
                    try:
                        result = await conn.call_tool(name, params.model_dump(exclude_none=True))
                    except MCPError as exc:
                        return ToolResult(ok=False, error=str(exc))
                    if result.get("isError"):
                        return ToolResult(ok=False, error=_result_text(result))
                    return ToolResult(ok=True, data=_result_text(result))

                return handler

            def make_preview(server_name: str, name: str):
                def preview(params) -> str:
                    args = json.dumps(
                        params.model_dump(exclude_none=True), indent=2, ensure_ascii=False
                    )
                    return (
                        f"External MCP tool: {name}\n"
                        f"Server: {server_name}\n"
                        f"Arguments:\n{args}\n"
                        "Hearth cannot verify what this external tool does."
                    )

                return preview

            description = tool.get("description") or f"Tool '{tool_name}' from MCP server"
            try:
                self._registry.register(
                    ToolSpec(
                        name=registered_name,
                        description=f"[{connection.name}] {description[:400]}",
                        params_model=params_model,
                        risk=RiskLevel.WRITE,  # unknown side effects: always confirm
                        permission="mcp",
                        handler=make_handler(connection, tool_name),
                        timeout_s=60,
                        preview=make_preview(connection.name, tool_name),
                    )
                )
            except ValueError:
                log.warning("Skipping duplicate MCP tool name: %s", registered_name)

    async def stop(self) -> None:
        for connection in self._connections:
            await connection.stop()
        self._connections.clear()

    def kill_all(self) -> None:
        """Synchronous teardown for app shutdown."""
        for connection in self._connections:
            connection.kill()
        self._connections.clear()
