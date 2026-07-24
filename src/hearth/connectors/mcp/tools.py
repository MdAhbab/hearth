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
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, create_model

from ...agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec
from ...assurance import (
    ActionClass,
    CanonicalTarget,
    EffectKind,
    PredictedEffect,
    stable_hash,
)
from ...config import MCPConfig
from .client import MCPError, MCPServerConnection

log = logging.getLogger(__name__)

def params_model_from_schema(model_name: str, schema: dict[str, Any] | None):
    """Recursively convert an MCP JSON Schema into a strict Pydantic model."""
    schema = schema if isinstance(schema, dict) else {"type": "object", "properties": {}}
    if schema.get("type", "object") != "object":
        raise ValueError("MCP inputSchema root must be an object")
    return _object_model(model_name, schema)


def _object_model(model_name: str, schema: dict[str, Any]):
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    required = set(schema.get("required") or [])
    fields: dict[str, tuple] = {}
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            raise ValueError(f"MCP schema property {key!r} is not an object")
        annotation = _schema_annotation(f"{model_name}_{_slug(key)}", prop)
        default = ... if key in required else prop.get("default", None)
        constraints: dict[str, Any] = {}
        for source, target in (
            ("minimum", "ge"),
            ("maximum", "le"),
            ("exclusiveMinimum", "gt"),
            ("exclusiveMaximum", "lt"),
            ("multipleOf", "multiple_of"),
            ("minLength", "min_length"),
            ("maxLength", "max_length"),
            ("pattern", "pattern"),
            ("minItems", "min_length"),
            ("maxItems", "max_length"),
        ):
            if source in prop:
                constraints[target] = prop[source]
        fields[key] = (
            annotation,
            Field(default, description=str(prop.get("description", "")), **constraints),
        )
    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def _schema_annotation(model_name: str, schema: dict[str, Any]):
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return Literal.__getitem__(tuple(enum))
    schema_type = schema.get("type")
    if schema_type == "string":
        constraints = {
            target: schema[source]
            for source, target in (
                ("minLength", "min_length"),
                ("maxLength", "max_length"),
                ("pattern", "pattern"),
            )
            if source in schema
        }
        return Annotated[str, Field(**constraints)] if constraints else str
    if schema_type == "number":
        constraints = {
            target: schema[source]
            for source, target in (
                ("minimum", "ge"),
                ("maximum", "le"),
                ("exclusiveMinimum", "gt"),
                ("exclusiveMaximum", "lt"),
                ("multipleOf", "multiple_of"),
            )
            if source in schema
        }
        return Annotated[float, Field(**constraints)] if constraints else float
    if schema_type == "integer":
        constraints = {
            target: schema[source]
            for source, target in (
                ("minimum", "ge"),
                ("maximum", "le"),
                ("exclusiveMinimum", "gt"),
                ("exclusiveMaximum", "lt"),
                ("multipleOf", "multiple_of"),
            )
            if source in schema
        }
        return Annotated[int, Field(**constraints)] if constraints else int
    if schema_type == "boolean":
        return bool
    if schema_type == "object":
        return _object_model(model_name, schema)
    if schema_type == "array":
        items = schema.get("items")
        if not isinstance(items, dict):
            raise ValueError("MCP array schemas must declare an item schema")
        return list[_schema_annotation(f"{model_name}_item", items)]
    if schema_type == "null":
        return type(None)
    raise ValueError(f"Unsupported MCP JSON Schema type: {schema_type!r}")


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


def _manifest(connection: MCPServerConnection, tool: dict[str, Any]) -> str:
    return stable_hash(
        {
            "server": connection.server_identity,
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "inputSchema": tool.get("inputSchema"),
            "effect": "restrictive-external-execute-v1",
        }
    )


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
            try:
                params_model = params_model_from_schema(
                    f"MCP_{_slug(connection.name)}_{_slug(tool_name)}",
                    tool.get("inputSchema"),
                )
            except ValueError as exc:
                log.error(
                    "Skipping MCP tool %s/%s with unsupported schema: %s",
                    connection.name,
                    tool_name,
                    exc,
                )
                continue
            manifest_hash = _manifest(connection, tool)

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

            def make_effect(
                server_identity: str,
                name: str,
                pinned_manifest: str,
            ):
                def effect(_args: dict[str, Any]) -> PredictedEffect:
                    return PredictedEffect(
                        action_class=ActionClass.EXECUTE,
                        target=CanonicalTarget(
                            kind="mcp",
                            canonical_id=f"{server_identity}:{name}",
                            attributes={"manifest_hash": pinned_manifest},
                        ),
                        effect_kinds=frozenset(
                            {EffectKind.WRITE, EffectKind.EGRESS, EffectKind.IRREVERSIBLE}
                        ),
                        reversible=False,
                        egress=True,
                        pre_state_hash=pinned_manifest,
                        flags=frozenset({"unknown_effect"}),
                        description=f"external MCP execution {name}",
                    )

                return effect

            def make_manifest_probe(
                conn: MCPServerConnection,
                name: str,
            ):
                async def probe(_params) -> str:
                    tools = await conn.list_tools()
                    current = next(
                        (candidate for candidate in tools if candidate.get("name") == name),
                        None,
                    )
                    return _manifest(conn, current or {"name": name, "missing": True})

                return probe

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
                        effect_adapter=make_effect(
                            connection.server_identity, tool_name, manifest_hash
                        ),
                        irreversible=True,
                        manifest_hash=manifest_hash,
                        identity_namespace=f"mcp.{_slug(connection.name)}",
                        publisher=connection.name,
                        server_identity=connection.server_identity,
                        state_probe=make_manifest_probe(connection, tool_name),
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
