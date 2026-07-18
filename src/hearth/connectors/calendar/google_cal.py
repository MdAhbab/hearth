"""Google Calendar backend — used on Windows/Linux (or by config override),
reusing the same Google OAuth connection as Gmail."""

from __future__ import annotations

import asyncio
from datetime import datetime

from ..google_auth import GoogleAuth
from .base import EventData


class GoogleCalendarStore:
    def __init__(self, auth: GoogleAuth):
        self._auth = auth

    def _service(self):
        from googleapiclient.discovery import build

        creds = self._auth.get_credentials()
        if creds is None:
            raise RuntimeError("Google account is not connected")
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    async def request_access(self) -> bool:
        return self._auth.is_connected()

    async def list_calendars(self) -> list[dict]:
        def _run() -> list[dict]:
            items = self._service().calendarList().list().execute().get("items", [])
            return [{"id": c["id"], "name": c.get("summary", c["id"])} for c in items]

        return await asyncio.to_thread(_run)

    async def list_events(
        self, start: datetime, end: datetime, calendar_id: str | None = None
    ) -> list[EventData]:
        def _run() -> list[EventData]:
            resp = (
                self._service()
                .events()
                .list(
                    calendarId=calendar_id or "primary",
                    timeMin=start.astimezone().isoformat(),
                    timeMax=end.astimezone().isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=100,
                )
                .execute()
            )
            return [_to_event_data(e, calendar_id or "primary") for e in resp.get("items", [])]

        return await asyncio.to_thread(_run)

    async def create_event(self, event: EventData) -> EventData:
        def _run() -> EventData:
            body = _to_google_body(event)
            created = (
                self._service()
                .events()
                .insert(calendarId=event.calendar_id or "primary", body=body)
                .execute()
            )
            return _to_event_data(created, event.calendar_id or "primary")

        return await asyncio.to_thread(_run)

    async def update_event(
        self, event_id: str, changes: dict, calendar_id: str | None = None
    ) -> EventData:
        def _run() -> EventData:
            body: dict = {}
            if "title" in changes:
                body["summary"] = changes["title"]
            if "location" in changes:
                body["location"] = changes["location"]
            if "notes" in changes:
                body["description"] = changes["notes"]
            if "start" in changes:
                body["start"] = {"dateTime": changes["start"].astimezone().isoformat()}
            if "end" in changes:
                body["end"] = {"dateTime": changes["end"].astimezone().isoformat()}
            cal = calendar_id or "primary"
            updated = (
                self._service()
                .events()
                .patch(calendarId=cal, eventId=event_id, body=body)
                .execute()
            )
            return _to_event_data(updated, cal)

        return await asyncio.to_thread(_run)

    async def delete_event(self, event_id: str, calendar_id: str | None = None) -> None:
        def _run() -> None:
            self._service().events().delete(
                calendarId=calendar_id or "primary", eventId=event_id
            ).execute()

        return await asyncio.to_thread(_run)


def _to_google_body(event: EventData) -> dict:
    body: dict = {"summary": event.title}
    if event.location:
        body["location"] = event.location
    if event.notes:
        body["description"] = event.notes
    if event.all_day and event.start and event.end:
        body["start"] = {"date": event.start.date().isoformat()}
        body["end"] = {"date": event.end.date().isoformat()}
    else:
        body["start"] = {"dateTime": event.start.astimezone().isoformat()}
        body["end"] = {"dateTime": event.end.astimezone().isoformat()}
    return body


def _parse_when(value: dict) -> tuple[datetime | None, bool]:
    if "dateTime" in value:
        return datetime.fromisoformat(value["dateTime"]), False
    if "date" in value:
        return datetime.fromisoformat(value["date"]), True
    return None, False


def _to_event_data(item: dict, calendar_id: str) -> EventData:
    start, all_day = _parse_when(item.get("start", {}))
    end, _ = _parse_when(item.get("end", {}))
    return EventData(
        id=item.get("id", ""),
        calendar_id=calendar_id,
        calendar_name=calendar_id,
        title=item.get("summary", ""),
        start=start,
        end=end,
        all_day=all_day,
        location=item.get("location", ""),
        notes=item.get("description", ""),
    )
