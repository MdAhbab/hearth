"""Tests for reminders connector â€” SQLite fallback path (Capability 3).

The macOS EventKit path requires a real Mac + Reminders permission grant, so
we test only the SQLite fallback which is the cross-platform path. EventKit
is mocked out entirely here.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hearth.agent.tools import ToolRegistry
from hearth.storage.db import Database


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture()
def registry(db):
    reg = ToolRegistry()
    # Force SQLite fallback regardless of platform
    with patch("hearth.connectors.reminders.tools._ek_available", return_value=False):
        from hearth.connectors.reminders.tools import register_reminders_tools

        register_reminders_tools(reg, db)
    return reg, db


@pytest.mark.asyncio
async def test_create_and_list(registry):
    reg, db = registry
    create_params = reg.validate_args(
        "reminders_create",
        {"title": "Buy milk", "notes": "2% fat", "due_date": "2026-07-20"},
    )
    result = await reg.get("reminders_create").handler(create_params)
    assert result.ok
    assert result.data["title"] == "Buy milk"
    reminder_id = result.data["id"]

    list_result = await reg.get("reminders_list").handler(reg.validate_args("reminders_list", {}))
    assert list_result.ok
    assert list_result.data["count"] == 1
    r = list_result.data["reminders"][0]
    assert r["title"] == "Buy milk"
    assert r["id"] == reminder_id
    assert not r["completed"]


@pytest.mark.asyncio
async def test_complete_reminder(registry):
    reg, db = registry
    row_id = db.add_reminder("Call dentist")

    complete_result = await reg.get("reminders_complete").handler(
        reg.validate_args("reminders_complete", {"reminder_id": str(row_id)})
    )
    assert complete_result.ok
    assert complete_result.data["completed"]

    # Should not appear in open list
    list_result = await reg.get("reminders_list").handler(
        reg.validate_args("reminders_list", {"include_completed": False})
    )
    assert list_result.data["count"] == 0

    # Should appear when including completed
    list_all = await reg.get("reminders_list").handler(
        reg.validate_args("reminders_list", {"include_completed": True})
    )
    assert list_all.data["count"] == 1


@pytest.mark.asyncio
async def test_complete_nonexistent(registry):
    reg, _ = registry
    result = await reg.get("reminders_complete").handler(
        reg.validate_args("reminders_complete", {"reminder_id": "9999"})
    )
    assert not result.ok
    assert "9999" in result.error


@pytest.mark.asyncio
async def test_invalid_id_type(registry):
    reg, _ = registry
    result = await reg.get("reminders_complete").handler(
        reg.validate_args("reminders_complete", {"reminder_id": "not-an-int"})
    )
    assert not result.ok


@pytest.mark.asyncio
async def test_reminder_with_due_date(registry):
    reg, db = registry
    result = await reg.get("reminders_create").handler(
        reg.validate_args(
            "reminders_create",
            {"title": "Submit report", "due_date": "2026-08-01T09:00:00"},
        )
    )
    assert result.ok
    list_result = await reg.get("reminders_list").handler(reg.validate_args("reminders_list", {}))
    r = list_result.data["reminders"][0]
    assert r["due_date"] is not None
    assert "2026-08" in r["due_date"]


def test_db_migration_creates_reminders_table(tmp_path):
    db = Database(tmp_path / "mig.db")
    # Should be version 2 after migration
    assert db.schema_version >= 2
    # Should be able to add a reminder without error
    row_id = db.add_reminder("Test", "notes", None)
    assert row_id > 0
