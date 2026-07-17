from .base import CalendarStore, EventData
from .tools import find_free_slots, register_calendar_tools

__all__ = ["CalendarStore", "EventData", "register_calendar_tools", "find_free_slots"]


def make_calendar_store(backend: str, google_auth=None):
    """Pick the calendar backend: EventKit on macOS, Google Calendar elsewhere."""
    import sys

    if backend == "auto":
        backend = "eventkit" if sys.platform == "darwin" else "google"
    if backend == "eventkit":
        from .eventkit import EventKitCalendarStore

        return EventKitCalendarStore()
    if backend == "google":
        from .google_cal import GoogleCalendarStore

        if google_auth is None:
            raise ValueError("Google Calendar backend requires a GoogleAuth instance")
        return GoogleCalendarStore(google_auth)
    raise ValueError(f"Unknown calendar backend: {backend}")
