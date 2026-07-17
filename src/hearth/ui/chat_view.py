"""Chat view: message bubbles, streaming, stop/cancel, suggestion chips, and
inline confirmation cards."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..agent.gate import ApprovalRequest
from .confirmation_card import ConfirmationCard

SUGGESTIONS = [
    "Summarize my unread email",
    "What's on my calendar tomorrow?",
    "Find a free 1-hour slot this week",
    "Find a file for me",
    "Draft a reply to my last email",
    "What time is it?",
]


class _InputBox(QPlainTextEdit):
    """Enter sends, Shift+Enter inserts a newline."""

    def __init__(self, on_submit: Callable[[], None]):
        super().__init__()
        self._on_submit = on_submit
        self.setObjectName("chatInput")
        self.setPlaceholderText("Ask Hearth anything…  (Enter to send, Shift+Enter for newline)")
        self.setFixedHeight(64)

    def keyPressEvent(self, event):  # noqa: N802 — Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self._on_submit()
            return
        super().keyPressEvent(event)


class ChatView(QWidget):
    def __init__(self, on_send: Callable[[str], None], on_stop: Callable[[], None]):
        super().__init__()
        self._on_send = on_send
        self._on_stop = on_stop
        self._streaming_label: QLabel | None = None
        self._streaming_text = ""
        self._open_cards: list[ConfirmationCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 14)
        layout.setSpacing(10)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        canvas = QWidget()
        canvas.setObjectName("chatCanvas")
        self._messages = QVBoxLayout(canvas)
        self._messages.setContentsMargins(4, 8, 4, 8)
        self._messages.setSpacing(10)
        self._messages.addStretch(1)
        self._scroll.setWidget(canvas)
        layout.addWidget(self._scroll, stretch=1)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        for text in SUGGESTIONS:
            chip = QPushButton(text)
            chip.setProperty("chip", True)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _=False, t=text: self._submit_text(t))
            chips.addWidget(chip)
        chips.addStretch(1)
        layout.addLayout(chips)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._input = _InputBox(self._submit)
        input_row.addWidget(self._input, stretch=1)

        buttons = QVBoxLayout()
        self._send = QPushButton("Send")
        self._send.setObjectName("sendBtn")
        self._send.clicked.connect(self._submit)
        self._stop = QPushButton("Stop")
        self._stop.setObjectName("stopBtn")
        self._stop.clicked.connect(self._on_stop)
        self._stop.hide()
        buttons.addWidget(self._send)
        buttons.addWidget(self._stop)
        input_row.addLayout(buttons)
        layout.addLayout(input_row)

    # -- sending ----------------------------------------------------------

    def _submit(self) -> None:
        self._submit_text(self._input.toPlainText())

    def _submit_text(self, text: str) -> None:
        text = text.strip()
        if not text or not self._send.isEnabled():
            return
        self._input.clear()
        self._on_send(text)

    def set_busy(self, busy: bool) -> None:
        self._send.setEnabled(not busy)
        self._stop.setVisible(busy)

    # -- rendering ---------------------------------------------------------

    def _add_widget(self, widget: QWidget) -> None:
        self._messages.insertWidget(self._messages.count() - 1, widget)
        QTimer.singleShot(30, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _bubble(self, kind: str, text: str) -> QLabel:
        frame = QFrame()
        frame.setProperty("bubble", kind)
        box = QVBoxLayout(frame)
        box.setContentsMargins(12, 8, 12, 8)
        label = QLabel(text)
        label.setProperty("bubbleText", True)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(label)

        row = QHBoxLayout()
        if kind == "user":
            row.addStretch(1)
            row.addWidget(frame, stretch=4)
        else:
            row.addWidget(frame, stretch=4)
            row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        self._add_widget(holder)
        return label

    def add_user_message(self, text: str) -> None:
        self._bubble("user", text)

    def add_assistant_message(self, text: str) -> None:
        self._bubble("assistant", text)

    def add_tool_note(self, text: str) -> None:
        frame = QFrame()
        frame.setProperty("bubble", "tool")
        box = QHBoxLayout(frame)
        box.setContentsMargins(10, 5, 10, 5)
        label = QLabel(text)
        label.setProperty("bubbleMeta", True)
        box.addWidget(label)
        self._add_widget(frame)

    # -- streaming ----------------------------------------------------------

    def begin_stream(self) -> None:
        self._streaming_text = ""
        self._streaming_label = self._bubble("assistant", "…")

    def stream_chunk(self, text: str) -> None:
        if self._streaming_label is None:
            self.begin_stream()
        self._streaming_text += text
        self._streaming_label.setText(self._streaming_text)

    def end_stream(self, final_text: str) -> None:
        if self._streaming_label is not None:
            if final_text:
                self._streaming_label.setText(final_text)
            elif not self._streaming_text:
                self._streaming_label.parentWidget().hide()
        self._streaming_label = None
        self._streaming_text = ""

    # -- confirmation cards ---------------------------------------------------

    def show_confirmation(self, request: ApprovalRequest) -> asyncio.Future:
        future = asyncio.get_event_loop().create_future()
        card = ConfirmationCard(request, future)
        self._open_cards.append(card)
        future.add_done_callback(lambda _f: self._open_cards.remove(card))
        self._add_widget(card)
        return future

    def cancel_open_cards(self) -> None:
        for card in list(self._open_cards):
            card.cancel()
