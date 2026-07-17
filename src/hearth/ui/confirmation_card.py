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


class ConfirmationCard(QFrame):
    def __init__(self, request: ApprovalRequest, future: asyncio.Future, parent=None):
        super().__init__(parent)
        self.setObjectName("confirmCard")
        self._request = request
        self._future = future

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        title = QLabel(f"Approval needed — {request.tool}")
        title.setObjectName("confirmTitle")
        layout.addWidget(title)

        self._preview = QPlainTextEdit(request.preview)
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
        self._error.setProperty("muted", True)
        self._error.setStyleSheet("color: #e08d84;")
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
