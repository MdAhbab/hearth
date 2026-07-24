"""Transaction layer: stage reversible effects, show a deterministic diff,
commit only under a verified seal, and expose compensating undo metadata.

Reversible effects (file writes/moves/deletes, calendar/reminder mutations)
are staged so the user sees the exact before/after and can discard without any
world change. Irreversible effects (an email send, a physical actuation)
cannot claim rollback — they instead require stronger confirmation and an
idempotency key, and are represented here as ``reversible=False`` so callers
never offer a false undo.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .types import stable_hash


@dataclass
class SemanticDiff:
    """A deterministic, human-readable statement of a staged change."""

    summary: str
    before: str
    after: str
    reversible: bool = True

    def render(self) -> str:
        arrow = "  (reversible)" if self.reversible else "  (IRREVERSIBLE — no undo)"
        return f"{self.summary}{arrow}\n- before: {self.before}\n+ after:  {self.after}"


@dataclass
class UndoRecord:
    """Compensating metadata: how to reverse a committed change, if at all."""

    kind: str  # "restore_file" | "trash" | "delete_created" | "none"
    detail: dict = field(default_factory=dict)
    reversible: bool = True


class StagedFileWrite:
    """Stage a file create/overwrite; commit atomically or discard cleanly.

    On commit an existing file is first snapshotted so ``undo`` can restore the
    exact prior bytes; a newly created file records enough to delete it back
    out. Discarding removes only the staging file — the real target is never
    touched until ``commit``.
    """

    def __init__(self, target: Path, content: str, staging_dir: Path) -> None:
        self.target = Path(target)
        self.content = content
        self.staging_dir = Path(staging_dir)
        self._staged: Path | None = None
        self._snapshot: Path | None = None
        self.committed = False

    def stage(self) -> StagedFileWrite:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        name = f"stage_{stable_hash(str(self.target))[:16]}_{int(time.time() * 1000)}"
        self._staged = self.staging_dir / name
        self._staged.write_text(self.content, encoding="utf-8")
        return self

    def diff(self) -> SemanticDiff:
        existed = self.target.is_file()
        before = self.target.read_text(errors="replace") if existed else "(new file)"
        return SemanticDiff(
            summary=f"{'Overwrite' if existed else 'Create'} {self.target}",
            before=(before[:400] + "…") if len(before) > 400 else before,
            after=(self.content[:400] + "…") if len(self.content) > 400 else self.content,
            reversible=True,
        )

    def commit(self) -> UndoRecord:
        if self._staged is None:
            self.stage()
        existed = self.target.is_file()
        if existed:
            self.staging_dir.mkdir(parents=True, exist_ok=True)
            self._snapshot = self.staging_dir / f"snap_{stable_hash(str(self.target))[:16]}"
            shutil.copy2(self.target, self._snapshot)
        self.target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(self._staged), str(self.target))
        self._staged = None
        self.committed = True
        if existed:
            return UndoRecord(
                kind="restore_file",
                detail={"target": str(self.target), "snapshot": str(self._snapshot)},
            )
        return UndoRecord(kind="delete_created", detail={"target": str(self.target)})

    def discard(self) -> None:
        if self._staged is not None and self._staged.exists():
            self._staged.unlink()
        self._staged = None

    def undo(self, record: UndoRecord) -> None:
        if record.kind == "restore_file":
            shutil.copy2(record.detail["snapshot"], record.detail["target"])
        elif record.kind == "delete_created":
            target = Path(record.detail["target"])
            if target.exists():
                target.unlink()


@dataclass
class Transaction:
    """Groups staged operations so a turn commits or rolls back as a unit."""

    staged: list[StagedFileWrite] = field(default_factory=list)
    undo_log: list[UndoRecord] = field(default_factory=list)
    committed: bool = False

    def add(self, op: StagedFileWrite) -> StagedFileWrite:
        op.stage()
        self.staged.append(op)
        return op

    def diffs(self) -> list[SemanticDiff]:
        return [op.diff() for op in self.staged]

    def commit(self) -> list[UndoRecord]:
        for op in self.staged:
            self.undo_log.append(op.commit())
        self.committed = True
        return self.undo_log

    def rollback(self) -> None:
        """Discard everything staged. No target is touched, by construction."""
        for op in self.staged:
            op.discard()
        self.staged.clear()

    def undo_committed(self) -> None:
        """Reverse an already-committed transaction using its undo log."""
        for op, record in zip(reversed(self.staged), reversed(self.undo_log), strict=False):
            op.undo(record)
