"""Typed Gmail client behind a protocol so tests can substitute a fake.

The Google API client is synchronous, so every call runs in a worker thread.
Bodies are decoded to plain text and size-capped before they ever reach the
model context (4096 tokens goes fast on an 8 GB machine).
"""

from __future__ import annotations

import asyncio
import base64
from email.message import EmailMessage
from typing import Any, Protocol

from ..google_auth import GoogleAuth

MAX_BODY_CHARS = 6_000


class GmailClient(Protocol):
    async def search_messages(
        self, query: str, max_results: int = 10, page_token: str | None = None
    ) -> dict[str, Any]: ...
    async def get_message(self, message_id: str) -> dict[str, Any]: ...
    async def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]: ...
    async def send_message(self, to: str, subject: str, body: str) -> dict[str, Any]: ...


class GoogleGmailClient:
    def __init__(self, auth: GoogleAuth):
        self._auth = auth

    def _service(self):
        from googleapiclient.discovery import build

        creds = self._auth.get_credentials()
        if creds is None:
            raise RuntimeError("Gmail is not connected")
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    async def search_messages(
        self, query: str, max_results: int = 10, page_token: str | None = None
    ) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            service = self._service()
            kwargs: dict[str, Any] = {
                "userId": "me",
                "q": query,
                "maxResults": min(max_results, 25),
            }
            if page_token:
                kwargs["pageToken"] = page_token
            listing = service.users().messages().list(**kwargs).execute()
            summaries = []
            for ref in listing.get("messages", []):
                msg = (
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=ref["id"],
                        format="metadata",
                        metadataHeaders=["From", "To", "Subject", "Date"],
                    )
                    .execute()
                )
                headers = _header_map(msg)
                summaries.append(
                    {
                        "id": ref["id"],
                        "thread_id": msg.get("threadId", ""),
                        "from": headers.get("from", ""),
                        "subject": headers.get("subject", ""),
                        "date": headers.get("date", ""),
                        "snippet": msg.get("snippet", ""),
                    }
                )
            return {
                "messages": summaries,
                "next_page_token": listing.get("nextPageToken"),
            }

        return await asyncio.to_thread(_run)

    async def get_message(self, message_id: str) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            service = self._service()
            msg = (
                service.users().messages().get(userId="me", id=message_id, format="full").execute()
            )
            headers = _header_map(msg)
            body = _extract_text(msg.get("payload", {}))
            return {
                "id": message_id,
                "from": headers.get("from", ""),
                "to": headers.get("to", ""),
                "subject": headers.get("subject", ""),
                "date": headers.get("date", ""),
                "body": body[:MAX_BODY_CHARS],
                "truncated": len(body) > MAX_BODY_CHARS,
            }

        return await asyncio.to_thread(_run)

    async def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            service = self._service()
            draft = (
                service.users()
                .drafts()
                .create(userId="me", body={"message": {"raw": _encode(to, subject, body)}})
                .execute()
            )
            return {"draft_id": draft.get("id", ""), "status": "draft created"}

        return await asyncio.to_thread(_run)

    async def send_message(self, to: str, subject: str, body: str) -> dict[str, Any]:
        def _run() -> dict[str, Any]:
            service = self._service()
            sent = (
                service.users()
                .messages()
                .send(userId="me", body={"raw": _encode(to, subject, body)})
                .execute()
            )
            return {"message_id": sent.get("id", ""), "status": "sent"}

        return await asyncio.to_thread(_run)


def _header_map(msg: dict[str, Any]) -> dict[str, str]:
    headers = msg.get("payload", {}).get("headers", [])
    return {h["name"].lower(): h["value"] for h in headers if "name" in h and "value" in h}


def _extract_text(payload: dict[str, Any]) -> str:
    """Walk MIME parts and return the first text/plain body (fallback: text/html)."""

    def walk(part: dict[str, Any], want: str) -> str | None:
        if part.get("mimeType") == want and part.get("body", {}).get("data"):
            data = part["body"]["data"]
            return base64.urlsafe_b64decode(data + "===").decode("utf-8", errors="replace")
        for child in part.get("parts", []) or []:
            if found := walk(child, want):
                return found
        return None

    return walk(payload, "text/plain") or walk(payload, "text/html") or ""


def _encode(to: str, subject: str, body: str) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()
