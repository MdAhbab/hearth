"""Central permission state.

One place answers "may tool X run?" for the ActionGate and drives the
Permission Center UI. Grants persist in SQLite (except secrets — connection
tokens live in the OS credential store and 'connected' is derived from them).

Keys:
- core:       always granted (time, calculator — pure local functions)
- gmail:      derived from the Google OAuth token being present
- calendar:   granted after calendar access succeeds (EventKit/Google)
- files:      granted while at least one approved folder exists
- system:     open URL/app, reveal, notify, clipboard (user toggle)
- shortcuts:  run approved macOS Shortcuts (user toggle + per-name list)
- automation: read Chrome active tab (user toggle, macOS Automation prompt)
- web:        fetch web pages (user toggle, default off)
"""

from __future__ import annotations

from collections.abc import Callable

from .storage.db import Database

TOGGLE_KEYS = (
    "system",
    "shortcuts",
    "automation",
    "web",
    "calendar",
    "reminders",
    "weather",
    "mcp",
)

PERMISSION_LABELS: dict[str, tuple[str, str]] = {
    "gmail": ("Gmail", "Search, read, draft, and send email (send/draft always confirmed)"),
    "calendar": ("Calendar", "Read events; create/update/delete only with confirmation"),
    "files": ("Files", "Work with files inside folders you approve"),
    "system": ("System", "Open URLs/apps, reveal files, notifications, clipboard"),
    "shortcuts": ("Shortcuts", "Run macOS Shortcuts you approve by exact name"),
    "automation": ("Browser", "Read the active Chrome tab title/URL"),
    "web": ("Web access", "Fetch web pages as text (sends requests off-device)"),
    "reminders": (
        "Reminders",
        "List, add, and complete reminders (native on macOS, local on others)",
    ),
    "mcp": (
        "MCP servers",
        "Let external MCP tools run (each call is confirmed; servers set in config.toml)",
    ),
    "weather": (
        "Weather",
        "Look up current weather for a city (sends request to Open-Meteo — free, no key)",
    ),
}


class Permissions:
    def __init__(self, db: Database, gmail_connected: Callable[[], bool]):
        self._db = db
        self._gmail_connected = gmail_connected

    def check(self, key: str) -> bool:
        if key == "core":
            return True
        if key == "gmail":
            return self._gmail_connected()
        if key == "files":
            return bool(self._db.list_approved_folders())
        return self._db.get_connector_status(key) == "granted"

    def grant(self, key: str) -> None:
        self._db.set_connector_status(key, "granted")

    def revoke(self, key: str) -> None:
        self._db.set_connector_status(key, "revoked")

    def snapshot(self) -> dict[str, bool]:
        keys = ["gmail", "calendar", "files", *TOGGLE_KEYS]
        return {k: self.check(k) for k in dict.fromkeys(keys)}
