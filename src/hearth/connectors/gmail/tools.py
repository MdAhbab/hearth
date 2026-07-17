"""Gmail tools. Search/read run automatically once connected; creating a
draft or sending always goes through the confirmation card, which shows the
complete outgoing message."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from ...agent.tools import RiskLevel, ToolRegistry, ToolResult, ToolSpec
from .client import GmailClient


class SearchParams(BaseModel):
    query: str = Field(
        min_length=1,
        description="Gmail search query, e.g. 'is:unread', 'from:alice@x.com newer_than:7d'",
    )
    max_results: int = Field(default=10, ge=1, le=25)
    page_token: str = Field(default="", description="Token from a previous page, if any")


class ReadParams(BaseModel):
    message_id: str = Field(min_length=1, description="Gmail message id from gmail_search")


class DraftParams(BaseModel):
    to: EmailStr = Field(description="Recipient email address")
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, description="Plain-text body of the email")


class SendParams(DraftParams):
    pass


def _mail_preview(kind: str, p: DraftParams) -> str:
    return f"{kind}\nTo:      {p.to}\nSubject: {p.subject}\n--- body ---\n{p.body}"


def register_gmail_tools(registry: ToolRegistry, client: GmailClient) -> None:
    async def search(p: SearchParams) -> ToolResult:
        data = await client.search_messages(p.query, p.max_results, p.page_token or None)
        return ToolResult(ok=True, data=data)

    async def read_message(p: ReadParams) -> ToolResult:
        return ToolResult(ok=True, data=await client.get_message(p.message_id))

    async def create_draft(p: DraftParams) -> ToolResult:
        return ToolResult(ok=True, data=await client.create_draft(p.to, p.subject, p.body))

    async def send_message(p: SendParams) -> ToolResult:
        return ToolResult(ok=True, data=await client.send_message(p.to, p.subject, p.body))

    registry.register(
        ToolSpec(
            name="gmail_search",
            description=(
                "Search Gmail with standard Gmail query syntax and get message summaries "
                "(sender, subject, date, snippet). Use gmail_read_message for full bodies."
            ),
            params_model=SearchParams,
            risk=RiskLevel.READ,
            permission="gmail",
            handler=search,
            timeout_s=45,
        )
    )
    registry.register(
        ToolSpec(
            name="gmail_read_message",
            description="Read the full plain-text body of one Gmail message by id.",
            params_model=ReadParams,
            risk=RiskLevel.READ,
            permission="gmail",
            handler=read_message,
            timeout_s=30,
        )
    )
    registry.register(
        ToolSpec(
            name="gmail_create_draft",
            description="Create a Gmail draft (does not send). The user approves it first.",
            params_model=DraftParams,
            risk=RiskLevel.WRITE,
            permission="gmail",
            handler=create_draft,
            timeout_s=30,
            preview=lambda p: _mail_preview("Create DRAFT (will not send)", p),
        )
    )
    registry.register(
        ToolSpec(
            name="gmail_send_message",
            description="Send an email from the user's Gmail. Requires explicit approval.",
            params_model=SendParams,
            risk=RiskLevel.WRITE,
            permission="gmail",
            handler=send_message,
            timeout_s=30,
            preview=lambda p: _mail_preview("SEND EMAIL (goes out immediately on approval)", p),
        )
    )
