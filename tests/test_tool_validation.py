import pytest
from pydantic import BaseModel, Field

from hearth.agent.tools import (
    RiskLevel,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    ToolValidationError,
    UnknownToolError,
)


class Params(BaseModel):
    count: int = Field(ge=1, le=10)
    name: str = Field(min_length=1)


async def _noop(p: Params) -> ToolResult:
    return ToolResult(ok=True)


def _spec() -> ToolSpec:
    return ToolSpec(
        name="demo",
        description="",
        params_model=Params,
        risk=RiskLevel.READ,
        permission="test",
        handler=_noop,
    )


def test_valid_args_pass():
    registry = ToolRegistry()
    registry.register(_spec())
    params = registry.validate_args("demo", {"count": 3, "name": "x"})
    assert params.count == 3


def test_out_of_range_rejected():
    registry = ToolRegistry()
    registry.register(_spec())
    with pytest.raises(ToolValidationError):
        registry.validate_args("demo", {"count": 999, "name": "x"})


def test_missing_field_rejected():
    registry = ToolRegistry()
    registry.register(_spec())
    with pytest.raises(ToolValidationError):
        registry.validate_args("demo", {"count": 3})


def test_wrong_type_rejected():
    registry = ToolRegistry()
    registry.register(_spec())
    with pytest.raises(ToolValidationError):
        registry.validate_args("demo", {"count": "lots", "name": "x"})


def test_unknown_tool():
    registry = ToolRegistry()
    with pytest.raises(UnknownToolError):
        registry.get("nope")


def test_duplicate_registration_rejected():
    registry = ToolRegistry()
    registry.register(_spec())
    with pytest.raises(ValueError):
        registry.register(_spec())


def test_ollama_schema_export():
    registry = ToolRegistry()
    registry.register(_spec())
    (tool,) = registry.ollama_tools()
    assert tool["type"] == "function"
    assert tool["function"]["name"] == "demo"
    assert "count" in tool["function"]["parameters"]["properties"]
