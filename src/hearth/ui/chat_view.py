"""Chat view: message bubbles, markdown rendering, streaming, stop/cancel,
a personalized welcome state, and inline confirmation cards."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
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
from .markdown import markdown_to_html

# Starters live in the welcome state and disappear once the conversation
# begins — a quiet desk, not a wall of chips.
SUGGESTIONS = [
    "/today",
    "Summarize my unread email",
    "What's on my calendar tomorrow?",
    "Find a free 1-hour slot this week",
    "Find a file for me",
    "What time is it?",
]


def _greeting(name: str) -> str:
    hour = datetime.now().hour
    part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
    return f"Good {part}, {name}" if name else f"Good {part}"


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
    def __init__(
        self,
        on_send: Callable[[str, list[str]], None],
        on_stop: Callable[[], None],
        on_voice: Callable[[], None] | None = None,
        greeting_name: str = "",
    ):
        super().__init__()
        self._on_send = on_send
        self._on_stop = on_stop
        self._on_voice = on_voice
        self._busy = False
        self._streaming_label: QLabel | None = None
        self._streaming_text = ""
        self._pending_text = ""
        self._thinking_frame = 0
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(80)
        self._flush_timer.timeout.connect(self._flush_stream)
        self._open_cards: list[ConfirmationCard] = []
        self._attachments: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 14)
        layout.setSpacing(10)

        # The conversation lives in a centered, width-capped column — an
        # editorial page rather than text stretched across a wide window.
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        canvas = QWidget()
        canvas.setObjectName("chatCanvas")
        canvas_row = QHBoxLayout(canvas)
        canvas_row.setContentsMargins(0, 0, 0, 0)
        column = QWidget()
        column.setMaximumWidth(860)
        self._messages = QVBoxLayout(column)
        self._messages.setContentsMargins(4, 10, 4, 10)
        self._messages.setSpacing(12)
        self._messages.addStretch(1)
        canvas_row.addStretch(1)
        canvas_row.addWidget(column, stretch=1000)
        canvas_row.addStretch(1)
        self._scroll.setWidget(canvas)
        layout.addWidget(self._scroll, stretch=1)

        self._welcome: QWidget | None = self._build_welcome(greeting_name)
        self._messages.insertWidget(0, self._welcome)

        self._attach_row = QHBoxLayout()
        self._attach_row.setSpacing(6)
        self._attach_label = QLabel("")
        self._attach_label.setProperty("muted", True)
        clear_attach = QPushButton("Clear attachments")
        clear_attach.setProperty("chip", True)
        clear_attach.clicked.connect(self._clear_attachments)
        self._attach_row.addWidget(self._attach_label, stretch=1)
        self._attach_row.addWidget(clear_attach)
        attach_holder = QWidget()
        attach_holder.setLayout(self._attach_row)
        attach_holder.hide()
        self._attach_holder = attach_holder
        layout.addWidget(attach_holder)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        attach = QPushButton("＋")
        attach.setProperty("secondary", True)
        attach.setFixedSize(40, 40)
        attach.setToolTip("Attach an image or document (PDF, DOCX, text)")
        attach.setAccessibleName("Attach a file")
        attach.clicked.connect(self._pick_attachment)
        input_row.addWidget(attach)
        self._mic = QPushButton("🎤")
        self._mic.setProperty("secondary", True)
        self._mic.setFixedSize(40, 40)
        self._mic.setToolTip("Voice input — click to record, click again to transcribe")
        self._mic.setAccessibleName("Voice input")
        if on_voice is not None:
            self._mic.clicked.connect(on_voice)
        else:
            self._mic.hide()
        input_row.addWidget(self._mic)
        self._input = _InputBox(self._submit)
        input_row.addWidget(self._input, stretch=1)

        # One button that morphs: Send while idle, Stop while a turn runs.
        self._action = QPushButton("Send")
        self._action.setObjectName("sendBtn")
        self._action.setAccessibleName("Send message")
        self._action.clicked.connect(self._on_action)
        input_row.addWidget(self._action, alignment=Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(input_row)

    # -- welcome state ------------------------------------------------------

    def _build_welcome(self, greeting_name: str) -> QWidget:
        from .theme import flame_pixmap

        welcome = QWidget()
        box = QVBoxLayout(welcome)
        box.setContentsMargins(8, 42, 8, 8)
        box.setSpacing(8)
        hearth_mark = QLabel()
        hearth_mark.setPixmap(flame_pixmap(46))
        hearth_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(hearth_mark)
        greeting = QLabel(_greeting(greeting_name))
        greeting.setProperty("hero", True)
        greeting.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel(
            "Everything runs on this machine, and anything that changes data\n"
            "asks you first. Type /help to list commands."
        )
        sub.setProperty("muted", True)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(greeting)
        box.addWidget(sub)
        box.addSpacing(12)
        for chunk_start in range(0, len(SUGGESTIONS), 3):
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addStretch(1)
            for text in SUGGESTIONS[chunk_start : chunk_start + 3]:
                chip = QPushButton(text)
                chip.setProperty("chip", True)
                chip.setCursor(Qt.CursorShape.PointingHandCursor)
                chip.clicked.connect(lambda _=False, t=text: self._submit_text(t))
                row.addWidget(chip)
            row.addStretch(1)
            box.addLayout(row)
        return welcome

    def _dismiss_welcome(self) -> None:
        if self._welcome is not None:
            self._messages.removeWidget(self._welcome)
            self._welcome.deleteLater()
            self._welcome = None

    # -- sending ----------------------------------------------------------

    def _on_action(self) -> None:
        if self._busy:
            self._on_stop()
        else:
            self._submit()

    def _submit(self) -> None:
        self._submit_text(self._input.toPlainText())

    def _submit_text(self, text: str) -> None:
        text = text.strip()
        if not text or self._busy:
            return
        self._input.clear()
        attachments = self._attachments
        self._clear_attachments()
        self._on_send(text, attachments)

    # -- attachments (images and documents) ---------------------------------

    MAX_ATTACHMENTS = 3

    _FILE_FILTER = (
        "Images and documents (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tiff *.heic "
        "*.pdf *.docx *.txt *.md *.csv *.tsv *.json *.log *.yaml *.yml *.toml *.xml "
        "*.html *.htm *.ini *.cfg *.py *.js *.ts *.sh *.rst *.markdown);;All files (*)"
    )

    def _pick_attachment(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Attach a file", "", self._FILE_FILTER)
        if not path:
            return
        if len(self._attachments) >= self.MAX_ATTACHMENTS:
            self._attachments = self._attachments[1:]
        self._attachments.append(path)
        names = ", ".join(Path(p).name for p in self._attachments)
        self._attach_label.setText(f"Attached: {names}")
        self._attach_holder.show()

    def _clear_attachments(self) -> None:
        self._attachments = []
        self._attach_holder.hide()

    # -- voice input ---------------------------------------------------------

    def set_voice_state(self, state: str) -> None:
        """states: idle | recording | busy"""
        recording = state == "recording"
        self._mic.setText("■" if recording else "…" if state == "busy" else "🎤")
        self._mic.setEnabled(state != "busy")
        self._mic.setProperty("voiceRecording", recording)
        style = self._mic.style()
        style.unpolish(self._mic)
        style.polish(self._mic)
        if recording:
            self._attach_label.setText("Recording — click ■ to stop and transcribe")
            self._attach_holder.show()
        elif not self._attachments:
            self._attach_holder.hide()

    def insert_transcript(self, text: str) -> None:
        existing = self._input.toPlainText()
        self._input.setPlainText((existing + " " + text).strip() if existing else text)
        cursor = self._input.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._input.setTextCursor(cursor)
        self._input.setFocus()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._action.setText("Stop" if busy else "Send")
        self._action.setAccessibleName("Stop generating" if busy else "Send message")
        self._action.setProperty("stopMode", busy)
        style = self._action.style()
        style.unpolish(self._action)
        style.polish(self._action)

    # -- rendering ---------------------------------------------------------

    MAX_RENDERED_MESSAGES = 200  # keeps day-long sessions from growing without bound
    _SCROLL_ANCHOR_PX = 80  # how close to the bottom still counts as "following"

    def _near_bottom(self) -> bool:
        bar = self._scroll.verticalScrollBar()
        return bar.maximum() - bar.value() <= self._SCROLL_ANCHOR_PX

    def _add_widget(self, widget: QWidget, force_scroll: bool = False) -> None:
        follow = force_scroll or self._near_bottom()
        self._messages.insertWidget(self._messages.count() - 1, widget)
        # +1 accounts for the trailing stretch item.
        while self._messages.count() > self.MAX_RENDERED_MESSAGES + 1:
            item = self._messages.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        if follow:
            QTimer.singleShot(30, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self._scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _bubble(self, kind: str, text: str) -> QLabel:
        frame = QFrame()
        frame.setProperty("bubble", kind)
        box = QVBoxLayout(frame)
        label = QLabel()
        label.setProperty("bubbleText", True)
        label.setWordWrap(True)
        if kind == "assistant":
            # Model output renders as markdown; the converter escapes all HTML
            # in the input, so rich text here cannot inject markup.
            box.setContentsMargins(2, 2, 2, 2)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setText(markdown_to_html(text))
            label.setOpenExternalLinks(True)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByMouse
            )
        else:
            box.setContentsMargins(13, 9, 13, 9)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setText(text)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            # A wrap-enabled label reports a tiny minimum width and gets
            # squeezed; hold it at its natural text width up to the cap so
            # short messages hug and long ones wrap. Polish first so the
            # metrics use the stylesheet font, not the default one.
            from PySide6.QtGui import QFontMetrics

            label.ensurePolished()
            natural = QFontMetrics(label.font()).size(0, text).width()
            label.setMinimumWidth(min(natural + 8, 590))
        box.addWidget(label)

        row = QHBoxLayout()
        if kind == "user":
            # Right-aligned bubble that hugs short messages and wraps long
            # ones; the assistant answers as open text spanning the column,
            # like a page rather than a chat template.
            frame.setMaximumWidth(620)
            row.addStretch(1)
            row.addWidget(frame)
        else:
            row.addWidget(frame)
        holder = QWidget()
        holder.setLayout(row)
        self._add_widget(holder, force_scroll=(kind == "user"))
        return label

    def add_user_message(self, text: str) -> None:
        self._dismiss_welcome()
        self._bubble("user", text)

    def add_assistant_message(self, text: str) -> None:
        self._bubble("assistant", text)

    def add_tool_note(self, text: str) -> None:
        frame = QFrame()
        frame.setProperty("bubble", "tool")
        box = QHBoxLayout(frame)
        box.setContentsMargins(10, 2, 10, 2)
        label = QLabel(text)
        label.setProperty("bubbleMeta", True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(label)
        self._add_widget(frame)

    # -- streaming ----------------------------------------------------------
    #
    # Chunks arrive per token; setText per token forces a relayout each time.
    # Buffer them and flush on a timer instead — one relayout per ~80 ms.
    # Until the first token lands, the same timer animates a thinking pulse.

    _THINKING_FRAMES = ("·", "· ·", "· · ·")

    def begin_stream(self) -> None:
        self._streaming_text = ""
        self._pending_text = ""
        self._thinking_frame = 0
        self._streaming_label = self._bubble("assistant", self._THINKING_FRAMES[0])
        self._flush_timer.start()

    def stream_chunk(self, text: str) -> None:
        if self._streaming_label is None:
            self.begin_stream()
        self._pending_text += text

    def _flush_stream(self) -> None:
        if self._streaming_label is None:
            return
        if self._pending_text:
            self._streaming_text += self._pending_text
            self._pending_text = ""
            self._streaming_label.setText(markdown_to_html(self._streaming_text))
            if self._near_bottom():
                self._scroll_to_bottom()
        elif not self._streaming_text:
            self._thinking_frame = (self._thinking_frame + 1) % len(self._THINKING_FRAMES)
            self._streaming_label.setText(
                markdown_to_html(self._THINKING_FRAMES[self._thinking_frame])
            )

    def end_stream(self, final_text: str) -> None:
        self._flush_timer.stop()
        if self._streaming_label is not None:
            if final_text:
                self._streaming_label.setText(markdown_to_html(final_text))
            elif self._streaming_text:
                self._streaming_label.setText(markdown_to_html(self._streaming_text))
            else:
                # Nothing was streamed: remove the empty bubble row entirely so
                # it doesn't leave a blank gap in the transcript.
                frame = self._streaming_label.parentWidget()
                holder = frame.parentWidget() if frame is not None else None
                (holder or frame).hide()
        self._streaming_label = None
        self._streaming_text = ""
        self._pending_text = ""

    # -- confirmation cards ---------------------------------------------------

    def show_confirmation(self, request: ApprovalRequest) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        card = ConfirmationCard(request, future)
        self._open_cards.append(card)
        future.add_done_callback(lambda _f: self._open_cards.remove(card))
        # A card demands a decision — always bring it into view.
        self._add_widget(card, force_scroll=True)
        return future

    def cancel_open_cards(self) -> None:
        for card in list(self._open_cards):
            card.cancel()
