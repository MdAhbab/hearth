"""The gate is the security boundary: these tests pin its guarantees."""

import asyncio

from pydantic import BaseModel

from hearth.agent.tools import RiskLevel, ToolResult, ToolSpec


async def test_read_runs_without_approval(harness):
    result = await harness.gate.execute("echo_read", {"text": "hi"})
    assert result.ok and result.data == "echo:hi"
    assert harness.requests == []  # no card shown


async def test_write_requires_approval(harness):
    harness.approve_next = True
    result = await harness.gate.execute("echo_write", {"text": "hi"})
    assert result.ok
    assert len(harness.requests) == 1
    assert harness.requests[0].preview == "will echo hi"


async def test_rejected_write_never_executes(harness, db):
    harness.approve_next = False
    result = await harness.gate.execute("echo_write", {"text": "danger"})
    assert not result.ok
    assert harness.executed == []  # handler never ran
    (action,) = [a for a in db.list_actions() if a["tool"] == "echo_write"]
    assert action["status"] == "rejected"


async def test_edited_args_are_revalidated_and_used(harness):
    harness.approve_next = True
    harness.edited_args = {"text": "edited"}
    result = await harness.gate.execute("echo_write", {"text": "original"})
    assert result.ok
    assert harness.executed == ["edited"]


async def test_invalid_edit_rejected(harness):
    harness.approve_next = True
    harness.edited_args = {"text": ""}  # violates min_length
    try:
        await harness.gate.execute("echo_write", {"text": "original"})
        raised = False
    except Exception:
        raised = True
    assert raised
    assert harness.executed == []


async def test_permission_denied_blocks_even_reads(harness):
    harness.granted = set()
    result = await harness.gate.execute("echo_read", {"text": "hi"})
    assert not result.ok and "permission" in result.error.lower()
    assert harness.executed == []


async def test_invalid_model_args_never_execute(harness):
    from hearth.agent.tools import ToolValidationError

    try:
        await harness.gate.execute("echo_write", {"wrong": 1})
        raised = False
    except ToolValidationError:
        raised = True
    assert raised
    assert harness.executed == []
    assert harness.requests == []  # rejected before any card


async def test_timeout_reported_and_recorded(harness, registry, db):
    class P(BaseModel):
        pass

    async def slow(_: P) -> ToolResult:
        await asyncio.sleep(5)
        return ToolResult(ok=True)

    registry.register(
        ToolSpec(
            name="slow_tool",
            description="",
            params_model=P,
            risk=RiskLevel.READ,
            permission="test",
            handler=slow,
            timeout_s=0.05,
        )
    )
    result = await harness.gate.execute("slow_tool", {})
    assert not result.ok and "timed out" in result.error
    (action,) = [a for a in db.list_actions() if a["tool"] == "slow_tool"]
    assert action["status"] == "failed"


async def test_handler_exception_becomes_tool_error(harness, registry):
    class P(BaseModel):
        pass

    async def boom(_: P) -> ToolResult:
        raise RuntimeError("kaput")

    registry.register(
        ToolSpec(
            name="boom_tool",
            description="",
            params_model=P,
            risk=RiskLevel.READ,
            permission="test",
            handler=boom,
        )
    )
    result = await harness.gate.execute("boom_tool", {})
    assert not result.ok and "kaput" in result.error


async def test_audit_trail_completeness(harness, db):
    await harness.gate.execute("echo_read", {"text": "a"})
    harness.approve_next = True
    await harness.gate.execute("echo_write", {"text": "b"})
    harness.approve_next = False
    await harness.gate.execute("echo_write", {"text": "c"})
    statuses = sorted(a["status"] for a in db.list_actions())
    assert statuses == ["completed", "completed", "rejected"]
