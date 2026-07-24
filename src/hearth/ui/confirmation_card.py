"""Inline confirmation card: Approve / Edit / Reject for every side effect.

The ActionGate awaits the asyncio.Future this card resolves. Editing shows
the arguments as JSON; edited values are re-validated by the gate before
anything runs.
"""

from __future__ import annotations

import asyncio
import json

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..agent.gate import ApprovalRequest, ApprovalResponse


def _intentseal_summary(request: ApprovalRequest) -> str:
    """A compact, human-readable summary of why IntentSeal is asking.

    Shows the trusted context a habituated 'Approve' click would otherwise
    skip: the escalation reason, the canonical target, who the effect reaches,
    what data would leave the machine, and whether it can be undone.
    """
    lines: list[str] = []
    if request.intent_confirmation:
        lines.append(f"Confirm turn intent: {request.intent_goal}")
        identity = request.principal
        if request.account:
            identity += f" · {request.account}"
        if identity:
            lines.append(f"Acting as: {identity}")
    reason = request.escalation or (request.reasons[0] if request.reasons else "")
    if reason:
        lines.append(f"IntentSeal · {request.decision} — {reason}")
    if request.canonical_target:
        lines.append(f"Target: {request.canonical_target}")
    if request.audience:
        lines.append(f"Recipients: {', '.join(request.audience)}")
    if request.data_out:
        lines.append(f"Data leaving the machine: {', '.join(request.data_out)}")
    if request.redact_fields:
        lines.append(f"Will strip protected fields: {', '.join(request.redact_fields)}")
    if request.provenance:
        lines.append(f"Sources: {', '.join(request.provenance[:4])}")
    lines.append("Reversible: yes" if request.reversible else "Reversible: NO — cannot be undone")
    return "\n".join(lines)


class ConfirmationCard(QFrame):
    def __init__(self, request: ApprovalRequest, future: asyncio.Future, parent=None):
        super().__init__(parent)
        self.setObjectName("confirmCard")
        self._request = request
        self._future = future

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        prefix = "Confirm intent" if request.intent_confirmation else "Approval needed"
        title = QLabel(f"{prefix} — {request.tool}")
        title.setObjectName("confirmTitle")
        layout.addWidget(title)

        details = _intentseal_summary(request)
        if details:
            summary = QLabel(details)
            summary.setObjectName("confirmProvenance")
            summary.setWordWrap(True)
            summary.setProperty("muted", True)
            layout.addWidget(summary)

        preview = request.preview
        if request.semantic_diff:
            preview = f"{preview}\n\nStaged semantic diff:\n{request.semantic_diff}"
        self._preview = QPlainTextEdit(preview)
        self._preview.setObjectName("confirmPreview")
        self._preview.setReadOnly(True)
        self._preview.setMinimumHeight(90)
        self._preview.setMaximumHeight(240)
        layout.addWidget(self._preview)

        self._editor = QPlainTextEdit(json.dumps(request.args, indent=2, ensure_ascii=False))
        self._editor.setObjectName("confirmEditor")
        self._editor.setMinimumHeight(110)
        self._editor.hide()
        layout.addWidget(self._editor)

        self._error = QLabel("")
        self._error.setProperty("error", True)  # palette-correct in both themes
        self._error.hide()
        layout.addWidget(self._error)

        buttons = QHBoxLayout()
        self._approve = QPushButton("Approve")
        self._approve.setObjectName("approveBtn")
        self._reject = QPushButton("Reject")
        self._reject.setObjectName("rejectBtn")
        self._edit = QPushButton("Edit")
        self._edit.setObjectName("editBtn")
        if not request.editable:
            self._edit.hide()
        buttons.addWidget(self._approve)
        buttons.addWidget(self._edit)
        buttons.addWidget(self._reject)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self._approve.clicked.connect(self._on_approve)
        self._reject.clicked.connect(self._on_reject)
        self._edit.clicked.connect(self._on_toggle_edit)

    def _on_toggle_edit(self) -> None:
        editing = self._editor.isHidden()
        self._editor.setVisible(editing)
        self._edit.setText("Hide edit" if editing else "Edit")
        if editing:
            self._approve.setText("Approve edited")

    def _on_approve(self) -> None:
        edited = None
        if not self._editor.isHidden():
            try:
                edited = json.loads(self._editor.toPlainText())
            except json.JSONDecodeError as exc:
                self._error.setText(f"Edited arguments are not valid JSON: {exc}")
                self._error.show()
                return
        self._resolve(ApprovalResponse(approved=True, edited_args=edited), "Approved")

    def _on_reject(self) -> None:
        self._resolve(ApprovalResponse(approved=False), "Rejected — nothing was changed")

    def _resolve(self, response: ApprovalResponse, verdict: str) -> None:
        if not self._future.done():
            self._future.set_result(response)
        for w in (self._approve, self._reject, self._edit, self._editor, self._error):
            w.hide()
        verdict_label = QLabel(verdict)
        verdict_label.setProperty("muted", True)
        self.layout().addWidget(verdict_label)

    def cancel(self) -> None:
        """Called if the app closes or the run is cancelled with the card open."""
        if not self._future.done():
            self._future.set_result(ApprovalResponse(approved=False))
