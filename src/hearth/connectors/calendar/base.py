"""Calendar backend contract. Implementations: EventKit (macOS native) and
Google Calendar (cross-platform). Tests use an in-memory fake."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class EventData(BaseModel):
    id: str = ""
    calendar_id: str = ""
    calendar_name: str = ""
    title: str = ""
    start: datetime | None = None
    end: datetime | None = None
    all_day: bool = False
    location: str = ""
    notes: str = ""


class CalendarStore(Protocol):
    async def request_access(self) -> bool: ...
    async def list_calendars(self) -> list[dict]: ...
    async def list_events(
        self, start: datetime, end: datetime, calendar_id: str | None = None
    ) -> list[EventData]: ...
    async def create_event(self, event: EventData) -> EventData: ...
    async def update_event(self, event_id: str, changes: dict) -> EventData: ...
    async def delete_event(self, event_id: str) -> None: ...
