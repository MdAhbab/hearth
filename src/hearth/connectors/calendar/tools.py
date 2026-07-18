"""Calendar tools. Listing and free-slot finding run automatically once
calendar access is granted; create/update/delete always show a confirmation
card with the exact event details."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, Field, field_validator

from ...agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec
from .base import CalendarStore, EventData


def _parse_dt(value: str) -> datetime:
    """Accept 'YYYY-MM-DD' or ISO 'YYYY-MM-DD HH:MM' (T separator also fine)."""
    try:
        return datetime.fromisoformat(value.replace("T", " ").strip())
    except ValueError as exc:
        raise ValueError(f"Could not parse '{value}'. Use YYYY-MM-DD or YYYY-MM-DD HH:MM.") from exc


class ListCalendarsParams(BaseModel):
    pass


class ListEventsParams(BaseModel):
    start: str = Field(description="Range start, e.g. 2026-07-17 or 2026-07-17 09:00")
    end: str = Field(description="Range end (exclusive)")
    calendar_id: str = Field(default="", description="Optional calendar id filter")

    @field_validator("start", "end")
    @classmethod
    def _valid_dt(cls, v: str) -> str:
        _parse_dt(v)
        return v


class FreeSlotsParams(BaseModel):
    start: str = Field(description="Search from this date/time")
    end: str = Field(description="Search until this date/time")
    duration_minutes: int = Field(ge=5, le=24 * 60, description="Required slot length")
    day_start_hour: int = Field(default=9, ge=0, le=23)
    day_end_hour: int = Field(default=18, ge=1, le=24)

    @field_validator("start", "end")
    @classmethod
    def _valid_dt(cls, v: str) -> str:
        _parse_dt(v)
        return v


class CreateEventParams(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    start: str = Field(description="Event start, e.g. 2026-07-18 14:00")
    end: str = Field(description="Event end")
    calendar_id: str = ""
    location: str = ""
    notes: str = ""
    all_day: bool = False

    @field_validator("start", "end")
    @classmethod
    def _valid_dt(cls, v: str) -> str:
        _parse_dt(v)
        return v


class UpdateEventParams(BaseModel):
    event_id: str = Field(min_length=1)
    calendar_id: str = Field(
        default="", description="Calendar the event lives in (from calendar_list_events)"
    )
    title: str = Field(default="", description="New title (empty = unchanged)")
    start: str = Field(default="", description="New start (empty = unchanged)")
    end: str = Field(default="", description="New end (empty = unchanged)")
    location: str = Field(default="", description="New location (empty = unchanged)")
    notes: str = Field(default="", description="New notes (empty = unchanged)")


class DeleteEventParams(BaseModel):
    event_id: str = Field(min_length=1)
    calendar_id: str = Field(
        default="", description="Calendar the event lives in (from calendar_list_events)"
    )
    title_confirmation: str = Field(
        default="", description="Title of the event being deleted, for the preview"
    )


def find_free_slots(
    busy: list[tuple[datetime, datetime]],
    start: datetime,
    end: datetime,
    duration: timedelta,
    day_start_hour: int = 9,
    day_end_hour: int = 18,
    max_slots: int = 10,
) -> list[tuple[datetime, datetime]]:
    """Pure gap-finder: working-hour windows minus busy intervals."""
    busy_sorted = sorted(busy)
    slots: list[tuple[datetime, datetime]] = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day < end and len(slots) < max_slots:
        window_start = max(day.replace(hour=day_start_hour), start)
        window_end = min(day.replace(hour=0) + timedelta(hours=day_end_hour), end)
        cursor = window_start
        for b_start, b_end in busy_sorted:
            if b_end <= cursor or b_start >= window_end:
                continue
            if b_start - cursor >= duration:
                slots.append((cursor, b_start))
                if len(slots) >= max_slots:
                    break
            cursor = max(cursor, b_end)
        if len(slots) < max_slots and window_end - cursor >= duration:
            slots.append((cursor, window_end))
        day += timedelta(days=1)
    return slots


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%a %Y-%m-%d %H:%M") if dt else "?"


def _event_dict(e: EventData) -> dict:
    return {
        "id": e.id,
        "title": e.title,
        "start": _fmt(e.start),
        "end": _fmt(e.end),
        "all_day": e.all_day,
        "calendar": e.calendar_name,
        "location": e.location,
    }


def register_calendar_tools(registry: ToolRegistry, store: CalendarStore) -> None:
    async def list_calendars(_: ListCalendarsParams) -> ToolResult:
        return ToolResult(ok=True, data=await store.list_calendars())

    async def list_events(p: ListEventsParams) -> ToolResult:
        events = await store.list_events(
            _parse_dt(p.start), _parse_dt(p.end), p.calendar_id or None
        )
        return ToolResult(ok=True, data=[_event_dict(e) for e in events])

    async def free_slots(p: FreeSlotsParams) -> ToolResult:
        if p.day_end_hour <= p.day_start_hour:
            return ToolResult(ok=False, error="day_end_hour must be after day_start_hour")
        start, end = _parse_dt(p.start), _parse_dt(p.end)
        events = await store.list_events(start, end)
        busy = [(e.start, e.end) for e in events if e.start and e.end and not e.all_day]
        slots = find_free_slots(
            busy,
            start,
            end,
            timedelta(minutes=p.duration_minutes),
            p.day_start_hour,
            p.day_end_hour,
        )
        return ToolResult(
            ok=True,
            data=[{"from": _fmt(s), "to": _fmt(e)} for s, e in slots]
            or "No free slots in that window.",
        )

    async def create_event(p: CreateEventParams) -> ToolResult:
        start, end = _parse_dt(p.start), _parse_dt(p.end)
        if end <= start:
            return ToolResult(ok=False, error="Event end must be after its start.")
        created = await store.create_event(
            EventData(
                title=p.title,
                start=start,
                end=end,
                calendar_id=p.calendar_id,
                location=p.location,
                notes=p.notes,
                all_day=p.all_day,
            )
        )
        return ToolResult(ok=True, data=_event_dict(created))

    async def update_event(p: UpdateEventParams) -> ToolResult:
        changes: dict = {}
        if p.title:
            changes["title"] = p.title
        if p.start:
            changes["start"] = _parse_dt(p.start)
        if p.end:
            changes["end"] = _parse_dt(p.end)
        if p.location:
            changes["location"] = p.location
        if p.notes:
            changes["notes"] = p.notes
        if not changes:
            return ToolResult(ok=False, error="No changes given.")
        updated = await store.update_event(p.event_id, changes, p.calendar_id or None)
        return ToolResult(ok=True, data=_event_dict(updated))

    async def delete_event(p: DeleteEventParams) -> ToolResult:
        await store.delete_event(p.event_id, p.calendar_id or None)
        return ToolResult(ok=True, data=f"Deleted event {p.event_id}")

    registry.register(
        ToolSpec(
            name="calendar_list_calendars",
            description="List the user's calendars (id and name).",
            params_model=ListCalendarsParams,
            risk=RiskLevel.READ,
            permission="calendar",
            handler=list_calendars,
        )
    )
    registry.register(
        ToolSpec(
            name="calendar_list_events",
            description="List calendar events between two dates/times.",
            params_model=ListEventsParams,
            risk=RiskLevel.READ,
            permission="calendar",
            handler=list_events,
        )
    )
    registry.register(
        ToolSpec(
            name="calendar_find_free_slots",
            description=(
                "Find free time slots of a given length between two dates, within "
                "working hours, based on the user's existing events."
            ),
            params_model=FreeSlotsParams,
            risk=RiskLevel.READ,
            permission="calendar",
            handler=free_slots,
        )
    )
    registry.register(
        ToolSpec(
            name="calendar_create_event",
            description="Create a calendar event. The user approves the exact details first.",
            params_model=CreateEventParams,
            risk=RiskLevel.WRITE,
            permission="calendar",
            handler=create_event,
            preview=lambda p: (
                f"Create event: {p.title}\n"
                f"When:  {p.start} -> {p.end}{' (all day)' if p.all_day else ''}\n"
                f"Where: {p.location or '—'}\n"
                f"Notes: {p.notes or '—'}"
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="calendar_update_event",
            description="Change fields of an existing event (only non-empty fields change).",
            params_model=UpdateEventParams,
            risk=RiskLevel.WRITE,
            permission="calendar",
            handler=update_event,
            preview=lambda p: (
                "Update event "
                + p.event_id
                + "".join(
                    f"\n  {name}: {value}"
                    for name, value in (
                        ("title", p.title),
                        ("start", p.start),
                        ("end", p.end),
                        ("location", p.location),
                        ("notes", p.notes),
                    )
                    if value
                )
            ),
        )
    )
    registry.register(
        ToolSpec(
            name="calendar_delete_event",
            description="Delete a calendar event by id. Requires explicit approval.",
            params_model=DeleteEventParams,
            risk=RiskLevel.WRITE,
            permission="calendar",
            handler=delete_event,
            preview=lambda p: (
                f"DELETE event: {p.title_confirmation or p.event_id}\n"
                "This removes it from the calendar (and synced devices)."
            ),
        )
    )
