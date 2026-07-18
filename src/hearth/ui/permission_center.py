"""Permission Center: connect accounts, grant/revoke capabilities, and manage
approved folders and Shortcuts. Nothing is on by default."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable

from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..permissions import PERMISSION_LABELS, Permissions
from ..storage.db import Database

log = logging.getLogger(__name__)


def _card(title: str, subtitle: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setProperty("card", True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 12, 14, 12)
    heading = QLabel(title)
    heading.setProperty("h2", True)
    sub = QLabel(subtitle)
    sub.setProperty("muted", True)
    sub.setWordWrap(True)
    layout.addWidget(heading)
    layout.addWidget(sub)
    return frame, layout


class PermissionCenter(QWidget):
    def __init__(
        self,
        permissions: Permissions,
        db: Database,
        connect_gmail: Callable[[], asyncio.Future],
        disconnect_gmail: Callable[[], None],
        grant_calendar: Callable[[], asyncio.Future],
    ) -> None:
        super().__init__()
        self._permissions = permissions
        self._db = db
        self._connect_gmail = connect_gmail
        self._disconnect_gmail = disconnect_gmail
        self._grant_calendar = grant_calendar

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        title = QLabel("Permission Center")
        title.setProperty("h1", True)
        intro = QLabel(
            "Hearth can only touch what you enable here. Reads run automatically once "
            "granted; anything that changes data always shows a confirmation card first."
        )
        intro.setProperty("muted", True)
        intro.setWordWrap(True)
        outer.addWidget(title)
        outer.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        self._list = QVBoxLayout(body)
        self._list.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll, stretch=1)

        self._build_gmail_card()
        self._build_calendar_card()
        self._build_folders_card()
        mac_only = ("shortcuts", "automation") if sys.platform == "darwin" else ()
        for key in ("system", "web", "reminders", "weather", *mac_only):
            self._build_toggle_card(key)
        if sys.platform == "darwin":
            self._build_shortcuts_card()
        self._list.addStretch(1)
        self.refresh()


    # -- Gmail ---------------------------------------------------------------

    def _build_gmail_card(self) -> None:
        name, desc = PERMISSION_LABELS["gmail"]
        frame, layout = _card(name, desc)
        row = QHBoxLayout()
        self._gmail_status = QLabel("")
        self._gmail_status.setProperty("muted", True)
        self._gmail_btn = QPushButton("Connect Gmail")
        self._gmail_btn.setProperty("secondary", True)
        self._gmail_btn.clicked.connect(self._on_gmail_clicked)
        row.addWidget(self._gmail_status, stretch=1)
        row.addWidget(self._gmail_btn)
        layout.addLayout(row)
        hint = QLabel(
            "Needs your own Google OAuth desktop credentials file — set its path in "
            "Settings first. See docs/google-oauth.md for the walkthrough."
        )
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._list.addWidget(frame)

    def _on_gmail_clicked(self) -> None:
        if self._permissions.check("gmail"):
            self._disconnect_gmail()
            self.refresh()
        else:
            self._gmail_btn.setEnabled(False)
            self._gmail_status.setText("A browser window opened — finish signing in there…")
            task = self._connect_gmail()
            task.add_done_callback(self._gmail_done)

    def _gmail_done(self, task) -> None:
        self._gmail_btn.setEnabled(True)
        exc = task.exception()
        if exc:
            log.warning("Gmail connect failed: %s", exc)
            self._gmail_status.setText(f"Connection failed: {exc}")
        self.refresh(keep_status=bool(exc))

    # -- Calendar ---------------------------------------------------------------

    def _build_calendar_card(self) -> None:
        name, desc = PERMISSION_LABELS["calendar"]
        frame, layout = _card(name, desc)
        row = QHBoxLayout()
        self._cal_status = QLabel("")
        self._cal_status.setProperty("muted", True)
        self._cal_btn = QPushButton("Grant access")
        self._cal_btn.setProperty("secondary", True)
        self._cal_btn.clicked.connect(self._on_calendar_clicked)
        row.addWidget(self._cal_status, stretch=1)
        row.addWidget(self._cal_btn)
        layout.addLayout(row)
        if sys.platform == "darwin":
            note = QLabel(
                "Uses the native macOS Calendar (EventKit). Google calendars already "
                "added to Apple Calendar sync through macOS automatically."
            )
        else:
            note = QLabel("Uses Google Calendar via your connected Google account.")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        layout.addWidget(note)
        self._list.addWidget(frame)

    def _on_calendar_clicked(self) -> None:
        if self._permissions.check("calendar"):
            self._permissions.revoke("calendar")
            self.refresh()
            return
        self._cal_btn.setEnabled(False)
        self._cal_status.setText("Requesting access…")
        task = self._grant_calendar()
        task.add_done_callback(self._calendar_done)

    def _calendar_done(self, task) -> None:
        self._cal_btn.setEnabled(True)
        exc = task.exception()
        granted = (not exc) and task.result()
        if granted:
            self._permissions.grant("calendar")
        else:
            self._cal_status.setText(
                "Access was not granted. On macOS: System Settings > Privacy & "
                "Security > Calendars. On Windows/Linux: connect Google first."
            )
        self.refresh(keep_status=not granted)

    # -- folders -------------------------------------------------------------

    def _build_folders_card(self) -> None:
        name, desc = PERMISSION_LABELS["files"]
        frame, layout = _card(name, desc)
        self._folder_list = QListWidget()
        self._folder_list.setMaximumHeight(110)
        layout.addWidget(self._folder_list)
        row = QHBoxLayout()
        add = QPushButton("Add folder…")
        add.setProperty("secondary", True)
        add.clicked.connect(self._add_folder)
        remove = QPushButton("Remove selected")
        remove.setProperty("secondary", True)
        remove.clicked.connect(self._remove_folder)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        self._list.addWidget(frame)

    def _add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder Hearth may use")
        if path:
            self._db.add_approved_folder(path)
            self.refresh()

    def _remove_folder(self) -> None:
        item = self._folder_list.currentItem()
        if item:
            self._db.remove_approved_folder(item.text())
            self.refresh()

    # -- simple toggles ---------------------------------------------------------

    def _build_toggle_card(self, key: str) -> None:
        name, desc = PERMISSION_LABELS[key]
        frame, layout = _card(name, desc)
        row = QHBoxLayout()
        status = QLabel("")
        status.setProperty("muted", True)
        button = QPushButton("")
        button.setProperty("secondary", True)
        row.addWidget(status, stretch=1)
        row.addWidget(button)
        layout.addLayout(row)
        self._list.addWidget(frame)

        def sync() -> None:
            granted = self._permissions.check(key)
            status.setText("Enabled" if granted else "Disabled")
            button.setText("Disable" if granted else "Enable")

        def flip() -> None:
            if self._permissions.check(key):
                self._permissions.revoke(key)
            else:
                self._permissions.grant(key)
            sync()

        button.clicked.connect(flip)
        setattr(self, f"_sync_{key}", sync)

    # -- shortcuts list -----------------------------------------------------------

    def _build_shortcuts_card(self) -> None:
        frame, layout = _card(
            "Approved Shortcuts",
            "Only Shortcuts named here can run, and each run is confirmed first.",
        )
        self._shortcut_list = QListWidget()
        self._shortcut_list.setMaximumHeight(90)
        layout.addWidget(self._shortcut_list)
        row = QHBoxLayout()
        self._shortcut_input = QLineEdit()
        self._shortcut_input.setPlaceholderText("Exact Shortcut name")
        add = QPushButton("Add")
        add.setProperty("secondary", True)
        add.clicked.connect(self._add_shortcut)
        remove = QPushButton("Remove selected")
        remove.setProperty("secondary", True)
        remove.clicked.connect(self._remove_shortcut)
        row.addWidget(self._shortcut_input, stretch=1)
        row.addWidget(add)
        row.addWidget(remove)
        layout.addLayout(row)
        self._list.addWidget(frame)

    def _add_shortcut(self) -> None:
        name = self._shortcut_input.text().strip()
        if name:
            self._db.add_approved_shortcut(name)
            self._shortcut_input.clear()
            self.refresh()

    def _remove_shortcut(self) -> None:
        item = self._shortcut_list.currentItem()
        if item:
            self._db.remove_approved_shortcut(item.text())
            self.refresh()

    # -- refresh ---------------------------------------------------------------

    def refresh(self, keep_status: bool = False) -> None:
        gmail_ok = self._permissions.check("gmail")
        if not keep_status:
            self._gmail_status.setText("Connected" if gmail_ok else "Not connected")
        self._gmail_btn.setText("Disconnect" if gmail_ok else "Connect Gmail")

        cal_ok = self._permissions.check("calendar")
        if not keep_status:
            self._cal_status.setText("Granted" if cal_ok else "Not granted")
        self._cal_btn.setText("Revoke" if cal_ok else "Grant access")

        self._folder_list.clear()
        self._folder_list.addItems(self._db.list_approved_folders())

        if hasattr(self, "_shortcut_list"):
            self._shortcut_list.clear()
            self._shortcut_list.addItems([n for n, _ in self._db.list_approved_shortcuts()])

        for key in ("system", "web", "shortcuts", "automation", "reminders", "weather"):
            if sync := getattr(self, f"_sync_{key}", None):
                sync()

