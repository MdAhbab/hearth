"""Shared fixtures. Tests never touch real mail, calendars, user files,
the network, or the OS credential store."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from hearth.agent.gate import ActionGate, ApprovalRequest, ApprovalResponse
from hearth.agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec
from hearth.storage.db import Database


class EchoParams(BaseModel):
    text: str = Field(min_length=1)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def registry():
    return ToolRegistry()


class GateHarness:
    """ActionGate wired to programmable approval + permission behavior."""

    def __init__(self, db: Database, registry: ToolRegistry):
        self.approve_next = True
        self.edited_args: dict | None = None
        self.granted: set[str] = {"core", "test"}
        self.requests: list[ApprovalRequest] = []
        self.executed: list[str] = []
        self.gate = ActionGate(db, registry, self._check, self._approve)

    def _check(self, permission: str) -> bool:
        return permission in self.granted

    async def _approve(self, request: ApprovalRequest) -> ApprovalResponse:
        self.requests.append(request)
        return ApprovalResponse(approved=self.approve_next, edited_args=self.edited_args)


@pytest.fixture
def harness(db, registry):
    h = GateHarness(db, registry)

    async def echo(p: EchoParams) -> ToolResult:
        h.executed.append(p.text)
        return ToolResult(ok=True, data=f"echo:{p.text}")

    registry.register(
        ToolSpec(
            name="echo_read",
            description="",
            params_model=EchoParams,
            risk=RiskLevel.READ,
            permission="test",
            handler=echo,
        )
    )
    registry.register(
        ToolSpec(
            name="echo_write",
            description="",
            params_model=EchoParams,
            risk=RiskLevel.WRITE,
            permission="test",
            handler=echo,
            preview=lambda p: f"will echo {p.text}",
        )
    )
    return h
