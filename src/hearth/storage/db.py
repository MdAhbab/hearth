"""Local SQLite persistence with versioned migrations.

Stores conversations, action history, approved folders/shortcuts, and
connector metadata. Never stores OAuth secrets or passwords — those live in
the OS credential store (see keychain.py).
"""

from __future__ import annotations

import hashlib
import json
import re
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
    # v3 — indexes for the queries that grow with months of use
    """
    CREATE INDEX idx_messages_conversation ON messages(conversation_id);
    CREATE INDEX idx_actions_conversation ON actions(conversation_id);
    CREATE INDEX idx_reminders_open ON reminders(completed, due_at);
    """,
    # v4 — IntentSeal: spent one-use seal nonces (cross-session replay defense)
    # and a decision column on the action audit trail.
    """
    CREATE TABLE seal_nonces (
        nonce TEXT PRIMARY KEY,
        used_at REAL NOT NULL
    );
    ALTER TABLE actions ADD COLUMN decision TEXT NOT NULL DEFAULT '';
    """,
    # v5 — durable redacted hash-chain and effect idempotency reservations.
    """
    CREATE TABLE intentseal_audit (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        at REAL NOT NULL,
        payload_json TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        record_hash TEXT NOT NULL UNIQUE
    );
    CREATE TABLE intentseal_idempotency (
        effect_key TEXT PRIMARY KEY,
        action_id INTEGER,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    """,
]

_AUDIT_CANARY_RE = re.compile(r"(?i)\b[A-Z0-9_-]*CANARY[A-Z0-9_-]*\b")
_AUDIT_SECRET_RE = re.compile(
    r"(?i)(?:\bsk-[a-z0-9_-]{8,}\b|\b(?:ghp|github_pat|xox[baprs])_[a-z0-9_-]{8,}\b|"
    r"\bAKIA[0-9A-Z]{12,}\b|\bSYNTHETIC[_-]SECRET[A-Z0-9_-]*\b)"
)


def _redact_audit_value(value):
    if isinstance(value, dict):
        return {str(key): _redact_audit_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_audit_value(child) for child in value]
    if isinstance(value, str):
        value = _AUDIT_CANARY_RE.sub("«REDACTED-CANARY»", value)
        return _AUDIT_SECRET_RE.sub("«REDACTED-SECRET»", value)
    return value


def _chain_hash(payload_json: str, prev_hash: str) -> str:
    return hashlib.sha256(f"{prev_hash}\n{payload_json}".encode()).hexdigest()


class _LockedConnection:
    """Serializes statement execution on a connection shared across threads.

    Tool handlers run in worker threads (asyncio.to_thread) while the gate
    writes audit rows from the event-loop thread; one lock keeps every
    statement/commit atomic regardless of the SQLite build's thread mode.
    """

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.RLock()
        self._closed = False

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            if self._closed:
                raise sqlite3.ProgrammingError("database is closed")
            return self._conn.execute(sql, params)

    def executescript(self, script: str) -> sqlite3.Cursor:
        with self._lock:
            if self._closed:
                raise sqlite3.ProgrammingError("database is closed")
            return self._conn.executescript(script)

    def commit(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()


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
        safe_args = _redact_audit_value(args)
        safe_preview = _redact_audit_value(preview)
        cur = self._conn.execute(
            "INSERT INTO actions (conversation_id, tool, args_json, risk, status,"
            " preview, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                tool,
                json.dumps(safe_args),
                risk,
                status,
                safe_preview,
                time.time(),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    def update_action(self, action_id: int, status: str, result_summary: str = "") -> None:
        try:
            self._conn.execute(
                "UPDATE actions SET status = ?, result_summary = ?, decided_at = ? WHERE id = ?",
                (status, result_summary, time.time(), action_id),
            )
            self._conn.commit()
        except sqlite3.ProgrammingError:
            # App quitting: a cancelled task may finalize its audit row after
            # close(). Losing that last status update is fine; crashing isn't.
            pass

    def list_actions(self, limit: int = 200) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def set_action_decision(self, action_id: int, decision: str) -> None:
        """Record the IntentSeal policy decision on an audit row."""
        try:
            self._conn.execute(
                "UPDATE actions SET decision = ? WHERE id = ?", (decision, action_id)
            )
            self._conn.commit()
        except sqlite3.ProgrammingError:
            pass  # app quitting — losing the decision label is fine, crashing isn't

    # -- IntentSeal one-use seal nonces --------------------------------------

    def mark_seal_nonce(self, nonce: str) -> bool:
        """Record a seal nonce as spent. Returns True if newly used, False if
        it was already consumed — i.e. a replay attempt. Atomic under the
        connection lock, so two concurrent verifications cannot both win."""
        try:
            self._conn.execute(
                "INSERT INTO seal_nonces (nonce, used_at) VALUES (?, ?)", (nonce, time.time())
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def is_seal_nonce_used(self, nonce: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seal_nonces WHERE nonce = ?", (nonce,)
        ).fetchone()
        return row is not None

    # -- IntentSeal durable audit + idempotency ------------------------------

    def append_intentseal_audit(self, payload: dict[str, Any]) -> int:
        safe = _redact_audit_value(payload)
        payload_json = json.dumps(
            safe, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
        )
        row = self._conn.execute(
            "SELECT record_hash FROM intentseal_audit ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev_hash = row["record_hash"] if row else "0" * 64
        record_hash = _chain_hash(payload_json, prev_hash)
        cur = self._conn.execute(
            "INSERT INTO intentseal_audit "
            "(at, payload_json, prev_hash, record_hash) VALUES (?, ?, ?, ?)",
            (time.time(), payload_json, prev_hash, record_hash),
        )
        self._conn.commit()
        return cur.lastrowid

    def list_intentseal_audit(self, limit: int = 500) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM intentseal_audit ORDER BY seq DESC LIMIT ?", (limit,)
        ).fetchall()

    def verify_intentseal_audit(self) -> bool:
        rows = self._conn.execute(
            "SELECT * FROM intentseal_audit ORDER BY seq"
        ).fetchall()
        previous = "0" * 64
        for row in rows:
            if row["prev_hash"] != previous:
                return False
            if row["record_hash"] != _chain_hash(row["payload_json"], previous):
                return False
            previous = row["record_hash"]
        return True

    def reserve_idempotency(self, effect_key: str, action_id: int | None) -> bool:
        try:
            now = time.time()
            self._conn.execute(
                "INSERT INTO intentseal_idempotency "
                "(effect_key, action_id, status, created_at, updated_at) "
                "VALUES (?, ?, 'reserved', ?, ?)",
                (effect_key, action_id, now, now),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def finish_idempotency(self, effect_key: str, status: str) -> None:
        self._conn.execute(
            "UPDATE intentseal_idempotency SET status = ?, updated_at = ? "
            "WHERE effect_key = ?",
            (status, time.time(), effect_key),
        )
        self._conn.commit()

    def release_idempotency(self, effect_key: str) -> None:
        self._conn.execute(
            "DELETE FROM intentseal_idempotency "
            "WHERE effect_key = ? AND status = 'reserved'",
            (effect_key,),
        )
        self._conn.commit()

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

    def get_connector_metadata(self, name: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT metadata_json FROM connectors WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(row["metadata_json"])
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

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
