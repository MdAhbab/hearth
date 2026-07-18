"""Reminders / Tasks connector.

macOS: uses native EventKit Reminders (same framework already imported for
Calendar). The default reminder list is used; EventKit handles sync with
iCloud / Exchange reminders automatically.

Windows / Linux: falls back to a local SQLite-backed reminder list stored in
the Hearth database (hearth.db). No cloud sync, but fully functional locally.

Tools:
  reminders_list        READ  — list open (or all) reminders
  reminders_create      WRITE — create a new reminder (confirmation card)
  reminders_complete    WRITE — mark a reminder done by ID (confirmation card)
"""

from __future__ import annotations

import asyncio
import sys
import threading
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from ...agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec
from ...storage.db import Database

# ---------------------------------------------------------------------------
# Pydantic parameter models
# ---------------------------------------------------------------------------


class RemindersListParams(BaseModel):
    include_completed: bool = Field(
        default=False, description="Include already-completed reminders in the list"
    )


class RemindersCreateParams(BaseModel):
    title: str = Field(min_length=1, max_length=500, description="Reminder text / title")
    notes: str = Field(default="", max_length=2000, description="Optional additional notes")
    due_date: str = Field(
        default="",
        description=(
            "Optional due date/time in ISO-8601 format, e.g. '2026-07-20T09:00:00' or '2026-07-20'"
        ),
    )


class RemindersCompleteParams(BaseModel):
    reminder_id: str = Field(
        min_length=1,
        description=(
            "ID of the reminder to mark as complete. On macOS this is the EventKit "
            "calendar-item identifier string; on other platforms it is the integer row ID "
            "returned by reminders_list."
        ),
    )


# ---------------------------------------------------------------------------
# macOS EventKit backend
# ---------------------------------------------------------------------------


def _ek_available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import EventKit  # noqa: F401

        return True
    except ImportError:
        return False


def _parse_due(due_date: str) -> float | None:
    """Return a POSIX timestamp from an ISO date/datetime string, or None."""
    if not due_date:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(due_date, fmt).timestamp()
        except ValueError:
            continue
    return None


def _row_to_dict(row: Any) -> dict:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "notes": row["notes"],
        "due_date": (
            datetime.fromtimestamp(row["due_at"]).strftime("%Y-%m-%dT%H:%M:%S")
            if row["due_at"]
            else None
        ),
        "completed": bool(row["completed"]),
    }


# macOS EventKit backend -------------------------------------------------------


class _EventKitReminders:
    """One shared EKEventStore with a single access request per session.

    OS authorization persists across launches; when already authorized the
    request completes immediately with no prompt. All methods run inside
    worker threads (EKEventStore is documented thread-safe).
    """

    _ACCESS_DENIED = ToolResult(
        ok=False,
        error=(
            "Reminders access was not granted. On macOS, go to System Settings → "
            "Privacy & Security → Reminders and allow Hearth."
        ),
    )

    def __init__(self) -> None:
        self._store = None
        self._granted = False
        self._lock = threading.Lock()

    def _ensure_access(self) -> bool:
        import EventKit

        with self._lock:
            if self._granted:
                return True
            if self._store is None:
                self._store = EventKit.EKEventStore.alloc().init()
            done = threading.Event()
            granted_ref = [False]

            def handler(granted, error):
                granted_ref[0] = bool(granted)
                done.set()

            self._store.requestFullAccessToRemindersWithCompletion_(handler)
            done.wait(timeout=120)
            self._granted = granted_ref[0]
            return self._granted

    def list(self, include_completed: bool) -> ToolResult:
        import EventKit

        if not self._ensure_access():
            return self._ACCESS_DENIED
        store = self._store
        default_list = store.defaultCalendarForNewReminders()
        calendars = (
            [default_list]
            if default_list
            else store.calendarsForEntityType_(EventKit.EKEntityTypeReminder)
        )
        predicate = store.predicateForRemindersInCalendars_(calendars)
        all_reminders = store.remindersMatchingPredicate_(predicate) or []
        items = []
        for r in all_reminders:
            if not include_completed and r.isCompleted():
                continue
            items.append(
                {
                    "id": r.calendarItemIdentifier(),
                    "title": r.title() or "",
                    "notes": r.notes() or "",
                    "due_date": _components_to_iso(r.dueDateComponents()),
                    "completed": bool(r.isCompleted()),
                }
            )
        return ToolResult(ok=True, data={"reminders": items, "count": len(items)})

    def create(self, title: str, notes: str, due_at: float | None) -> ToolResult:
        import EventKit
        import Foundation

        if not self._ensure_access():
            return self._ACCESS_DENIED
        store = self._store
        reminder = EventKit.EKReminder.reminderWithEventStore_(store)
        reminder.setTitle_(title)
        if notes:
            reminder.setNotes_(notes)
        if due_at:
            ns_date = Foundation.NSDate.dateWithTimeIntervalSince1970_(due_at)
            cal = Foundation.NSCalendar.currentCalendar()
            comps = cal.components_(
                Foundation.NSCalendarUnitYear
                | Foundation.NSCalendarUnitMonth
                | Foundation.NSCalendarUnitDay
                | Foundation.NSCalendarUnitHour
                | Foundation.NSCalendarUnitMinute,
                ns_date,
            )
            reminder.setDueDateComponents_(comps)
        reminder.setCalendar_(store.defaultCalendarForNewReminders())
        ok, error = store.saveReminder_commit_error_(reminder, True, None)
        if not ok:
            return ToolResult(ok=False, error=f"EventKit save failed: {error}")
        return ToolResult(ok=True, data={"id": reminder.calendarItemIdentifier(), "title": title})

    def complete(self, reminder_id: str) -> ToolResult:
        import EventKit
        import Foundation

        if not self._ensure_access():
            return self._ACCESS_DENIED
        store = self._store
        item = store.calendarItemWithIdentifier_(reminder_id)
        if item is None or not isinstance(item, EventKit.EKReminder):
            return ToolResult(ok=False, error=f"Reminder not found: {reminder_id}")
        item.setCompleted_(True)
        item.setCompletionDate_(Foundation.NSDate.date())
        ok, error = store.saveReminder_commit_error_(item, True, None)
        if not ok:
            return ToolResult(ok=False, error=f"EventKit save failed: {error}")
        return ToolResult(ok=True, data={"id": reminder_id, "completed": True})


def _components_to_iso(due) -> str | None:
    """Convert NSDateComponents to an ISO string, or None."""
    if due is None:
        return None
    try:
        import Foundation

        cal = Foundation.NSCalendar.currentCalendar()
        ns_date = cal.dateFromComponents_(due)
        if ns_date is None:
            return None
        return datetime.fromtimestamp(ns_date.timeIntervalSince1970()).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:  # noqa: BLE001 — malformed components shouldn't break listing
        return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_reminders_tools(registry: ToolRegistry, db: Database) -> None:
    """Register reminder tools. Uses EventKit on macOS, SQLite fallback elsewhere."""
    use_ek = _ek_available()
    ek_backend = _EventKitReminders() if use_ek else None

    async def reminders_list(p: RemindersListParams) -> ToolResult:
        if use_ek:
            return await asyncio.to_thread(ek_backend.list, p.include_completed)

        # SQLite fallback
        def _list() -> ToolResult:
            rows = db.list_reminders(include_completed=p.include_completed)
            return ToolResult(
                ok=True,
                data={
                    "reminders": [_row_to_dict(r) for r in rows],
                    "count": len(rows),
                },
            )

        return await asyncio.to_thread(_list)

    async def reminders_create(p: RemindersCreateParams) -> ToolResult:
        due_at = _parse_due(p.due_date)
        if use_ek:
            return await asyncio.to_thread(ek_backend.create, p.title, p.notes, due_at)

        def _create() -> ToolResult:
            row_id = db.add_reminder(p.title, p.notes, due_at)
            return ToolResult(
                ok=True,
                data={"id": str(row_id), "title": p.title, "due_date": p.due_date or None},
            )

        return await asyncio.to_thread(_create)

    async def reminders_complete(p: RemindersCompleteParams) -> ToolResult:
        if use_ek:
            return await asyncio.to_thread(ek_backend.complete, p.reminder_id)

        def _complete() -> ToolResult:
            try:
                rid = int(p.reminder_id)
            except ValueError:
                return ToolResult(ok=False, error=f"Invalid reminder ID: {p.reminder_id!r}")
            updated = db.complete_reminder(rid)
            if not updated:
                return ToolResult(ok=False, error=f"No reminder found with ID {rid}")
            return ToolResult(ok=True, data={"id": str(rid), "completed": True})

        return await asyncio.to_thread(_complete)

    registry.register(
        ToolSpec(
            name="reminders_list",
            description=(
                "List the user's open reminders (or all reminders if include_completed=true). "
                "Each reminder has an id, title, optional notes, and optional due_date."
            ),
            params_model=RemindersListParams,
            risk=RiskLevel.READ,
            permission="reminders",
            handler=reminders_list,
            timeout_s=15,
        )
    )
    registry.register(
        ToolSpec(
            name="reminders_create",
            description=(
                "Create a new reminder with a title, optional notes, and optional due date. "
                "Always requires user confirmation before saving."
            ),
            params_model=RemindersCreateParams,
            risk=RiskLevel.WRITE,
            permission="reminders",
            handler=reminders_create,
            timeout_s=15,
            preview=lambda p: (
                f"Create reminder: {p.title}"
                + (f"\nDue: {p.due_date}" if p.due_date else "")
                + (f"\nNotes: {p.notes}" if p.notes else "")
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="reminders_complete",
            description=(
                "Mark a reminder as completed by its ID. The ID comes from reminders_list. "
                "Requires user confirmation."
            ),
            params_model=RemindersCompleteParams,
            risk=RiskLevel.WRITE,
            permission="reminders",
            handler=reminders_complete,
            timeout_s=15,
            preview=lambda p: f"Mark reminder {p.reminder_id} as completed",
        )
    )
