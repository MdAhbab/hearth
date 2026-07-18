"""MCP client against a real subprocess echo server, schema conversion, and
gate integration (every MCP tool is WRITE-risk → confirmation required)."""

import sys
from pathlib import Path

import pytest

from hearth.agent.tools import RiskLevel
from hearth.config import MCPConfig, MCPServerConfig
from hearth.connectors.mcp import MCPManager, MCPServerConnection, params_model_from_schema

ECHO_SERVER = str(Path(__file__).parent / "mcp_echo_server.py")


def echo_config() -> MCPConfig:
    return MCPConfig(
        servers=[MCPServerConfig(name="Echo", command=sys.executable, args=[ECHO_SERVER])]
    )


async def test_connection_handshake_and_tool_call():
    connection = MCPServerConnection("echo", sys.executable, [ECHO_SERVER])
    try:
        await connection.start()
        assert [t["name"] for t in connection.tools] == ["echo"]
        result = await connection.call_tool("echo", {"text": "hi"})
        assert result["content"][0]["text"] == "echo:hi"
    finally:
        await connection.stop()


async def test_server_error_surfaces():
    from hearth.connectors.mcp import MCPError

    connection = MCPServerConnection("echo", sys.executable, [ECHO_SERVER])
    try:
        await connection.start()
        with pytest.raises(MCPError):
            await connection.call_tool("not_a_tool", {})
    finally:
        await connection.stop()


async def test_manager_registers_gated_tools(harness, registry):
    manager = MCPManager(echo_config(), registry)
    try:
        await manager.start()
        assert "mcp_echo_echo" in registry.names()
        spec = registry.get("mcp_echo_echo")
        assert spec.risk is RiskLevel.WRITE  # external tools always confirm
        assert spec.permission == "mcp"

        # Denied without the mcp permission
        result = await harness.gate.execute("mcp_echo_echo", {"text": "hi"})
        assert not result.ok and "permission" in result.error.lower()

        # Approved through the confirmation card
        harness.granted.add("mcp")
        harness.approve_next = True
        result = await harness.gate.execute("mcp_echo_echo", {"text": "hi"})
        assert result.ok and result.data == "echo:hi"
        assert "Echo" in harness.requests[-1].preview  # card names the server

        # Rejection runs nothing new
        harness.approve_next = False
        result = await harness.gate.execute("mcp_echo_echo", {"text": "no"})
        assert not result.ok
    finally:
        await manager.stop()


async def test_manager_survives_bad_server(registry):
    config = MCPConfig(
        servers=[MCPServerConfig(name="broken", command="/nonexistent/binary", args=[])]
    )
    manager = MCPManager(config, registry)
    await manager.start()  # must not raise
    assert registry.names() == []
    await manager.stop()


def test_params_model_from_schema_typed():
    model = params_model_from_schema(
        "M",
        {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "count": {"type": "integer", "default": 2},
            },
            "required": ["text"],
        },
    )
    instance = model(text="x")
    assert instance.text == "x" and instance.count == 2
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        model(count=1)  # missing required field


def test_params_model_from_schema_fallback():
    model = params_model_from_schema("M2", None)
    instance = model(anything="goes")
    assert instance.model_dump()["anything"] == "goes"
