"""Gmail tools with a fake client (no network, nothing sent), plus the pure
MIME/encoding helpers."""

import base64

import pytest

from hearth.connectors.gmail.client import _encode, _extract_text, _header_map
from hearth.connectors.gmail.tools import register_gmail_tools


class FakeGmailClient:
    def __init__(self):
        self.sent = []
        self.drafts = []
        self.pages = {
            None: {"messages": [{"id": "m1", "subject": "One"}], "next_page_token": "p2"},
            "p2": {"messages": [{"id": "m2", "subject": "Two"}], "next_page_token": None},
        }

    async def search_messages(self, query, max_results=10, page_token=None):
        return self.pages[page_token]

    async def get_message(self, message_id):
        return {"id": message_id, "subject": "One", "body": "hello body"}

    async def create_draft(self, to, subject, body):
        self.drafts.append((to, subject, body))
        return {"draft_id": "d1", "status": "draft created"}

    async def send_message(self, to, subject, body):
        self.sent.append((to, subject, body))
        return {"message_id": "s1", "status": "sent"}


@pytest.fixture
def gmail_env(harness, registry):
    client = FakeGmailClient()
    register_gmail_tools(registry, client)
    harness.granted.add("gmail")
    return harness, client


async def test_search_and_pagination(gmail_env):
    h, _ = gmail_env
    first = await h.gate.execute("gmail_search", {"query": "is:unread"})
    assert first.data["next_page_token"] == "p2"
    second = await h.gate.execute("gmail_search", {"query": "is:unread", "page_token": "p2"})
    assert second.data["messages"][0]["id"] == "m2"


async def test_max_results_capped(gmail_env, registry):
    from hearth.agent.tools import ToolValidationError

    with pytest.raises(ToolValidationError):
        registry.validate_args("gmail_search", {"query": "x", "max_results": 100})


async def test_invalid_recipient_rejected(gmail_env, registry):
    from hearth.agent.tools import ToolValidationError

    with pytest.raises(ToolValidationError):
        registry.validate_args(
            "gmail_send_message",
            {"to": "not-an-email", "subject": "s", "body": "b"},
        )


async def test_send_requires_approval_and_rejection_sends_nothing(gmail_env):
    h, client = gmail_env
    h.approve_next = False
    result = await h.gate.execute(
        "gmail_send_message", {"to": "a@b.com", "subject": "s", "body": "b"}
    )
    assert not result.ok and client.sent == []

    h.approve_next = True
    result = await h.gate.execute(
        "gmail_send_message", {"to": "a@b.com", "subject": "s", "body": "b"}
    )
    assert result.ok and client.sent == [("a@b.com", "s", "b")]


async def test_send_preview_shows_full_message(gmail_env):
    h, _ = gmail_env
    h.approve_next = True
    await h.gate.execute(
        "gmail_send_message",
        {"to": "a@b.com", "subject": "Quarterly", "body": "The numbers."},
    )
    preview = h.requests[-1].preview
    assert "a@b.com" in preview and "Quarterly" in preview and "The numbers." in preview


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def test_extract_text_prefers_plain():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<b>html</b>")}},
            {"mimeType": "text/plain", "body": {"data": _b64("plain text")}},
        ],
    }
    assert _extract_text(payload) == "plain text"


def test_extract_text_falls_back_to_html():
    payload = {"mimeType": "text/html", "body": {"data": _b64("<b>only html</b>")}}
    assert "only html" in _extract_text(payload)


def test_encode_roundtrip():
    raw = _encode("to@x.com", "Subject", "Body text")
    decoded = base64.urlsafe_b64decode(raw).decode()
    assert "To: to@x.com" in decoded and "Body text" in decoded


def test_header_map_lowercases():
    msg = {"payload": {"headers": [{"name": "From", "value": "a@b.c"}]}}
    assert _header_map(msg) == {"from": "a@b.c"}
