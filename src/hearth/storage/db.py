"""Local SQLite persistence with versioned migrations.

Stores conversations, action history, approved folders/shortcuts, and
connector metadata. Never stores OAuth secrets or passwords — those live in
the OS credential store (see keychain.py).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from ..config import app_data_dir

MIGRATIONS: list[str] = [
    # v1 — initial schema
    """
    CREATE TABLE conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL DEFAULT 'New chat',
        created_at REAL NOT NULL
    );
    CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL REFERENCES conversations(id),
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at REAL NOT NULL
    );
    CREATE TABLE actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER REFERENCES conversations(id),
        tool TEXT NOT NULL,
        args_json TEXT NOT NULL,
        risk TEXT NOT NULL,
        status TEXT NOT NULL,
        preview TEXT NOT NULL DEFAULT '',
        result_summary TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        decided_at REAL
    );
    CREATE TABLE approved_folders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        path TEXT NOT NULL UNIQUE,
        added_at REAL NOT NULL
    );
    CREATE TABLE approved_shortcuts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        changes_data INTEGER NOT NULL DEFAULT 1,
        added_at REAL NOT NULL
    );
    CREATE TABLE connectors (
        name TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'disconnected',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        updated_at REAL NOT NULL
    );
    CREATE TABLE settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # v2 — cross-platform reminders (fallback for Windows/Linux; macOS uses EventKit)
    """
    CREATE TABLE reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        due_at REAL,
        completed INTEGER NOT NULL DEFAULT 0,
        completed_at REAL,
        created_at REAL NOT NULL
    );
    """,
]


class _LockedConnection:
    """Serializes statement execution on a connection shared across threads.

    Tool handlers run in worker threads (asyncio.to_thread) while the gate
    writes audit rows from the event-loop thread; one lock keeps every
    statement/commit atomic regardless of the SQLite build's thread mode.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executescript(self, script: str) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executescript(script)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def set_row_factory(self, factory) -> None:
        self._conn.row_factory = factory


class Database:
    def __init__(self, path: Path | str | None = None):
        if path is None:
            app_data_dir().mkdir(parents=True, exist_ok=True)
            path = app_data_dir() / "hearth.db"
        raw = sqlite3.connect(str(path), check_same_thread=False)
        raw.row_factory = sqlite3.Row
        self._conn = _LockedConnection(raw)
        self._conn.execute("PRAGMA journal_mode=WAL")
        # NORMAL is durable enough under WAL and avoids an fsync per commit.
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
        )
        row = self._conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        current = row["v"] or 0
        for version, script in enumerate(MIGRATIONS, start=1):
            if version > current:
                self._conn.executescript(script)
                self._conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
        self._conn.commit()

    @property
    def schema_version(self) -> int:
        row = self._conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
        return row["v"] or 0

    def close(self) -> None:
        self._conn.close()

    # -- conversations / messages -------------------------------------------

    def create_conversation(self, title: str = "New chat") -> int:
        cur = self._conn.execute(
            "INSERT INTO conversations (title, created_at) VALUES (?, ?)",
            (title, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def add_message(self, conversation_id: int, role: str, content: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO messages (conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            (conversation_id, role, content, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def get_messages(self, conversation_id: int) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id",
            (conversation_id,),
        ).fetchall()

    # -- action audit log ----------------------------------------------------

    def record_action(
        self,
        tool: str,
        args: dict[str, Any],
        risk: str,
        status: str,
        preview: str = "",
        conversation_id: int | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO actions (conversation_id, tool, args_json, risk, status,"
            " preview, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, tool, json.dumps(args), risk, status, preview, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_action(self, action_id: int, status: str, result_summary: str = "") -> None:
        self._conn.execute(
            "UPDATE actions SET status = ?, result_summary = ?, decided_at = ? WHERE id = ?",
            (status, result_summary, time.time(), action_id),
        )
        self._conn.commit()

    def list_actions(self, limit: int = 200) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # -- approved folders / shortcuts -----------------------------------------

    def add_approved_folder(self, path: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO approved_folders (path, added_at) VALUES (?, ?)",
            (path, time.time()),
        )
        self._conn.commit()

    def remove_approved_folder(self, path: str) -> None:
        self._conn.execute("DELETE FROM approved_folders WHERE path = ?", (path,))
        self._conn.commit()

    def list_approved_folders(self) -> list[str]:
        rows = self._conn.execute("SELECT path FROM approved_folders ORDER BY path").fetchall()
        return [r["path"] for r in rows]

    def add_approved_shortcut(self, name: str, changes_data: bool = True) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO approved_shortcuts (name, changes_data, added_at)"
            " VALUES (?, ?, ?)",
            (name, int(changes_data), time.time()),
        )
        self._conn.commit()

    def remove_approved_shortcut(self, name: str) -> None:
        self._conn.execute("DELETE FROM approved_shortcuts WHERE name = ?", (name,))
        self._conn.commit()

    def list_approved_shortcuts(self) -> list[tuple[str, bool]]:
        rows = self._conn.execute(
            "SELECT name, changes_data FROM approved_shortcuts ORDER BY name"
        ).fetchall()
        return [(r["name"], bool(r["changes_data"])) for r in rows]

    # -- connectors ------------------------------------------------------------

    def set_connector_status(
        self, name: str, status: str, metadata: dict[str, Any] | None = None
    ) -> None:
        self._conn.execute(
            "INSERT INTO connectors (name, status, metadata_json, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET status = excluded.status,"
            " metadata_json = excluded.metadata_json, updated_at = excluded.updated_at",
            (name, status, json.dumps(metadata or {}), time.time()),
        )
        self._conn.commit()

    def get_connector_status(self, name: str) -> str:
        row = self._conn.execute("SELECT status FROM connectors WHERE name = ?", (name,)).fetchone()
        return row["status"] if row else "disconnected"

    # -- reminders (cross-platform fallback) ------------------------------------

    def add_reminder(self, title: str, notes: str = "", due_at: float | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO reminders (title, notes, due_at, created_at) VALUES (?, ?, ?, ?)",
            (title, notes, due_at, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_reminders(self, include_completed: bool = False) -> list[sqlite3.Row]:
        if include_completed:
            return self._conn.execute(
                "SELECT * FROM reminders ORDER BY due_at ASC, created_at ASC"
            ).fetchall()
        return self._conn.execute(
            "SELECT * FROM reminders WHERE completed = 0 ORDER BY due_at ASC, created_at ASC"
        ).fetchall()

    def complete_reminder(self, reminder_id: int) -> bool:
        """Mark a reminder complete. Returns True if a row was updated."""
        cur = self._conn.execute(
            "UPDATE reminders SET completed = 1, completed_at = ? WHERE id = ?",
            (time.time(), reminder_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def delete_reminder(self, reminder_id: int) -> bool:
        cur = self._conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        self._conn.commit()
        return cur.rowcount > 0
