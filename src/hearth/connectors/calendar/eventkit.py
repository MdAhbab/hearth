"""macOS EventKit calendar backend (PyObjC).

Events already synced into Apple Calendar (including Google calendars added
to macOS) show up here with no extra cloud API. All EventKit calls run in a
worker thread; EKEventStore itself is documented as thread-safe.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime

from .base import EventData

log = logging.getLogger(__name__)


class CalendarAccessDenied(Exception):
    pass


class EventKitCalendarStore:
    def __init__(self) -> None:
        self._store = None
        self._access_granted = False

    def _ek(self):
        import EventKit  # noqa: PLC0415 — heavyweight, import on demand

        return EventKit

    def _get_store(self):
        if self._store is None:
            self._store = self._ek().EKEventStore.alloc().init()
        return self._store

    async def request_access(self) -> bool:
        return await asyncio.to_thread(self._request_access_sync)

    def _request_access_sync(self) -> bool:
        ek = self._ek()
        store = self._get_store()
        done = threading.Event()
        result: dict = {"granted": False}

        def completion(granted, error):
            result["granted"] = bool(granted)
            if error:
                log.warning("EventKit access error: %s", error)
            done.set()

        # macOS 14+; falls back to the older API on earlier systems.
        if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
            store.requestFullAccessToEventsWithCompletion_(completion)
        else:
            store.requestAccessToEntityType_completion_(
                ek.EKEntityTypeEvent, lambda granted, error: completion(granted, error)
            )
        done.wait(timeout=120)
        self._access_granted = result["granted"]
        return self._access_granted

    def _require_access(self) -> None:
        if not self._access_granted:
            raise CalendarAccessDenied(
                "Calendar access not granted. Enable it in the Permission Center "
                "(macOS System Settings > Privacy & Security > Calendars)."
            )

    async def list_calendars(self) -> list[dict]:
        def _run() -> list[dict]:
            self._require_access()
            ek = self._ek()
            calendars = self._get_store().calendarsForEntityType_(ek.EKEntityTypeEvent)
            return [{"id": str(c.calendarIdentifier()), "name": str(c.title())} for c in calendars]

        return await asyncio.to_thread(_run)

    async def list_events(
        self, start: datetime, end: datetime, calendar_id: str | None = None
    ) -> list[EventData]:
        def _run() -> list[EventData]:
            self._require_access()
            store = self._get_store()
            calendars = None
            if calendar_id:
                cal = store.calendarWithIdentifier_(calendar_id)
                calendars = [cal] if cal else None
            predicate = store.predicateForEventsWithStartDate_endDate_calendars_(
                _nsdate(start), _nsdate(end), calendars
            )
            events = store.eventsMatchingPredicate_(predicate) or []
            return [_to_event_data(e) for e in events]

        return await asyncio.to_thread(_run)

    async def create_event(self, event: EventData) -> EventData:
        def _run() -> EventData:
            self._require_access()
            ek = self._ek()
            store = self._get_store()
            ek_event = ek.EKEvent.eventWithEventStore_(store)
            ek_event.setTitle_(event.title)
            ek_event.setStartDate_(_nsdate(event.start))
            ek_event.setEndDate_(_nsdate(event.end))
            if event.all_day:
                ek_event.setAllDay_(True)
            if event.location:
                ek_event.setLocation_(event.location)
            if event.notes:
                ek_event.setNotes_(event.notes)
            calendar = None
            if event.calendar_id:
                calendar = store.calendarWithIdentifier_(event.calendar_id)
            ek_event.setCalendar_(calendar or store.defaultCalendarForNewEvents())
            ok, error = store.saveEvent_span_error_(ek_event, ek.EKSpanThisEvent, None)
            if not ok:
                raise RuntimeError(f"EventKit refused to save the event: {error}")
            return _to_event_data(ek_event)

        return await asyncio.to_thread(_run)

    async def update_event(self, event_id: str, changes: dict) -> EventData:
        def _run() -> EventData:
            self._require_access()
            ek = self._ek()
            store = self._get_store()
            ek_event = store.eventWithIdentifier_(event_id)
            if ek_event is None:
                raise RuntimeError(f"No event with id {event_id}")
            if "title" in changes:
                ek_event.setTitle_(changes["title"])
            if "start" in changes:
                ek_event.setStartDate_(_nsdate(changes["start"]))
            if "end" in changes:
                ek_event.setEndDate_(_nsdate(changes["end"]))
            if "location" in changes:
                ek_event.setLocation_(changes["location"])
            if "notes" in changes:
                ek_event.setNotes_(changes["notes"])
            ok, error = store.saveEvent_span_error_(ek_event, ek.EKSpanThisEvent, None)
            if not ok:
                raise RuntimeError(f"EventKit refused the update: {error}")
            return _to_event_data(ek_event)

        return await asyncio.to_thread(_run)

    async def delete_event(self, event_id: str) -> None:
        def _run() -> None:
            self._require_access()
            ek = self._ek()
            store = self._get_store()
            ek_event = store.eventWithIdentifier_(event_id)
            if ek_event is None:
                raise RuntimeError(f"No event with id {event_id}")
            ok, error = store.removeEvent_span_error_(ek_event, ek.EKSpanThisEvent, None)
            if not ok:
                raise RuntimeError(f"EventKit refused the delete: {error}")

        return await asyncio.to_thread(_run)


def _nsdate(dt: datetime | None):
    from Foundation import NSDate

    if dt is None:
        raise ValueError("datetime required")
    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def _from_nsdate(nsdate) -> datetime | None:
    if nsdate is None:
        return None
    return datetime.fromtimestamp(nsdate.timeIntervalSince1970())


def _to_event_data(ek_event) -> EventData:
    calendar = ek_event.calendar()
    return EventData(
        id=str(ek_event.eventIdentifier() or ""),
        calendar_id=str(calendar.calendarIdentifier()) if calendar else "",
        calendar_name=str(calendar.title()) if calendar else "",
        title=str(ek_event.title() or ""),
        start=_from_nsdate(ek_event.startDate()),
        end=_from_nsdate(ek_event.endDate()),
        all_day=bool(ek_event.isAllDay()),
        location=str(ek_event.location() or ""),
        notes=str(ek_event.notes() or ""),
    )
