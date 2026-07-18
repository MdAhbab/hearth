"""Calendar tools with an in-memory fake store + the pure free-slot finder."""

from datetime import datetime, timedelta

import pytest

from hearth.connectors.calendar.base import EventData
from hearth.connectors.calendar.tools import find_free_slots, register_calendar_tools


class FakeCalendarStore:
    def __init__(self):
        self.events: dict[str, EventData] = {}
        self._next = 1

    async def request_access(self) -> bool:
        return True

    async def list_calendars(self):
        return [{"id": "cal1", "name": "Personal"}]

    async def list_events(self, start, end, calendar_id=None):
        return [
            e for e in self.events.values() if e.start and e.start < end and e.end and e.end > start
        ]

    async def create_event(self, event: EventData) -> EventData:
        event.id = f"e{self._next}"
        self._next += 1
        self.events[event.id] = event
        return event

    async def update_event(self, event_id, changes, calendar_id=None):
        event = self.events[event_id]
        for key, value in changes.items():
            setattr(event, key, value)
        return event

    async def delete_event(self, event_id, calendar_id=None):
        del self.events[event_id]


@pytest.fixture
def cal_env(harness, registry):
    store = FakeCalendarStore()
    register_calendar_tools(registry, store)
    harness.granted.add("calendar")
    return harness, store


async def test_create_requires_approval(cal_env):
    h, store = cal_env
    h.approve_next = False
    result = await h.gate.execute(
        "calendar_create_event",
        {
            "title": "Standup",
            "start": "2026-07-20 09:00",
            "end": "2026-07-20 09:30",
        },
    )
    assert not result.ok and store.events == {}

    h.approve_next = True
    result = await h.gate.execute(
        "calendar_create_event",
        {
            "title": "Standup",
            "start": "2026-07-20 09:00",
            "end": "2026-07-20 09:30",
        },
    )
    assert result.ok and len(store.events) == 1


async def test_end_before_start_rejected(cal_env):
    h, store = cal_env
    h.approve_next = True
    result = await h.gate.execute(
        "calendar_create_event",
        {
            "title": "Backwards",
            "start": "2026-07-20 10:00",
            "end": "2026-07-20 09:00",
        },
    )
    assert not result.ok and store.events == {}


async def test_bad_date_rejected_before_approval(cal_env, registry):
    from hearth.agent.tools import ToolValidationError

    with pytest.raises(ToolValidationError):
        registry.validate_args(
            "calendar_create_event",
            {
                "title": "x",
                "start": "someday",
                "end": "2026-07-20 09:00",
            },
        )


async def test_update_and_delete_flow(cal_env):
    h, store = cal_env
    h.approve_next = True
    created = await h.gate.execute(
        "calendar_create_event",
        {
            "title": "Old",
            "start": "2026-07-20 09:00",
            "end": "2026-07-20 10:00",
        },
    )
    event_id = created.data["id"]

    result = await h.gate.execute("calendar_update_event", {"event_id": event_id, "title": "New"})
    assert result.ok and store.events[event_id].title == "New"

    result = await h.gate.execute("calendar_delete_event", {"event_id": event_id})
    assert result.ok and store.events == {}


async def test_update_with_no_changes_fails(cal_env):
    h, _ = cal_env
    h.approve_next = True
    result = await h.gate.execute("calendar_update_event", {"event_id": "e9"})
    assert not result.ok


async def test_free_slots_tool(cal_env):
    h, store = cal_env
    h.approve_next = True
    await h.gate.execute(
        "calendar_create_event",
        {
            "title": "Busy",
            "start": "2026-07-20 09:00",
            "end": "2026-07-20 12:00",
        },
    )
    result = await h.gate.execute(
        "calendar_find_free_slots",
        {
            "start": "2026-07-20",
            "end": "2026-07-21",
            "duration_minutes": 60,
            "day_start_hour": 9,
            "day_end_hour": 17,
        },
    )
    assert result.ok
    assert result.data[0]["from"].endswith("12:00")


def test_find_free_slots_pure():
    day = datetime(2026, 7, 20)
    busy = [
        (day.replace(hour=9), day.replace(hour=10)),
        (day.replace(hour=13), day.replace(hour=14)),
    ]
    slots = find_free_slots(busy, day, day + timedelta(days=1), timedelta(hours=2), 9, 18)
    assert slots[0] == (day.replace(hour=10), day.replace(hour=13))
    assert slots[1] == (day.replace(hour=14), day.replace(hour=18))


def test_find_free_slots_respects_working_hours():
    day = datetime(2026, 7, 20)
    slots = find_free_slots([], day, day + timedelta(days=1), timedelta(hours=1), 9, 10)
    assert slots == [(day.replace(hour=9), day.replace(hour=10))]


def test_find_free_slots_none_when_packed():
    day = datetime(2026, 7, 20)
    busy = [(day.replace(hour=9), day.replace(hour=18))]
    slots = find_free_slots(busy, day, day + timedelta(days=1), timedelta(hours=1), 9, 18)
    assert slots == []
