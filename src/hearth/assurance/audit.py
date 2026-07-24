"""Postconditions and tamper-evident audit.

After an effect (or a block), IntentSeal compares expected versus observed
state and records a structured, redacted, hash-chained entry. Dependent steps
stop on a postcondition mismatch. Records are structured (never free text that
could forge log lines) and each links to the previous by hash, so any later
edit or deletion breaks the chain and is detectable.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .types import Decision, stable_hash, stable_json

# Redact obvious secret/canary shapes before anything reaches a log or the UI.
_CANARY_RE = re.compile(r"\b[A-Z0-9_]*CANARY[A-Z0-9_]*\b")
_SECRETISH_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{8,})\b"
)


def redact_text(text: str, extra_markers: tuple[str, ...] = ()) -> str:
    """Mask canary tokens, common secret shapes, and any explicit markers.

    Structured logs store this, never raw content, so a synthetic secret that
    entered model context is not re-leaked through the audit trail.
    """
    if not text:
        return text
    out = _CANARY_RE.sub("«REDACTED-CANARY»", text)
    out = _SECRETISH_RE.sub("«REDACTED-SECRET»", out)
    for marker in extra_markers:
        if marker:
            out = out.replace(marker, "«REDACTED»")
    return out


@dataclass
class PostconditionResult:
    ok: bool
    reason: str = ""
    expected: str = ""
    observed: str = ""


def check_postcondition(
    *,
    expected_change: bool,
    pre_state_hash: str,
    post_state_hash: str,
) -> PostconditionResult:
    """Verify observed state matches the decision.

    ``expected_change=True`` (an ALLOW that ran) requires the state hash to have
    moved; ``expected_change=False`` (a DENY/QUARANTINE, or a rolled-back stage)
    requires it to be unchanged. A mismatch halts dependent steps.
    """
    changed = pre_state_hash != post_state_hash
    if expected_change and not changed:
        return PostconditionResult(False, "expected a state change but none was observed",
                                   pre_state_hash, post_state_hash)
    if not expected_change and changed:
        return PostconditionResult(False, "state changed when none should have",
                                   pre_state_hash, post_state_hash)
    return PostconditionResult(True, "postcondition satisfied", pre_state_hash, post_state_hash)


@dataclass
class AuditRecord:
    seq: int
    at: float
    tool: str
    action: str
    decision: str
    reasons: tuple[str, ...]
    seal_nonce: str
    outcome: str  # "executed" | "blocked" | "rolled_back" | "postcondition_failed"
    prev_hash: str
    record_hash: str = ""

    def content(self) -> dict:
        return {
            "seq": self.seq,
            "at": round(self.at, 3),
            "tool": self.tool,
            "action": self.action,
            "decision": self.decision,
            "reasons": list(self.reasons),
            "seal_nonce": self.seal_nonce,
            "outcome": self.outcome,
            "prev_hash": self.prev_hash,
        }


class HashChainAudit:
    """An append-only, hash-linked audit log. In-memory; the production gate
    also mirrors decisions into SQLite via the existing action history."""

    GENESIS = "0" * 64

    def __init__(self) -> None:
        self._records: list[AuditRecord] = []

    @property
    def head(self) -> str:
        return self._records[-1].record_hash if self._records else self.GENESIS

    def append(
        self,
        *,
        tool: str,
        action: str,
        decision: Decision | str,
        reasons: tuple[str, ...],
        seal_nonce: str,
        outcome: str,
    ) -> AuditRecord:
        record = AuditRecord(
            seq=len(self._records),
            at=time.time(),
            tool=tool,
            action=action,
            decision=str(decision),
            reasons=tuple(redact_text(r) for r in reasons),
            seal_nonce=seal_nonce,
            outcome=outcome,
            prev_hash=self.head,
        )
        record.record_hash = stable_hash(stable_json(record.content()))
        self._records.append(record)
        return record

    def records(self) -> list[AuditRecord]:
        return list(self._records)

    def verify_chain(self) -> bool:
        """True iff every record hashes correctly and links to its predecessor."""
        prev = self.GENESIS
        for record in self._records:
            if record.prev_hash != prev:
                return False
            if record.record_hash != stable_hash(stable_json(record.content())):
                return False
            prev = record.record_hash
        return True
