"""ActionGate — the single chokepoint between a proposed tool call and any
effect on the world.

Rules it enforces:
- Arguments are validated against the tool's schema before anything else.
- READ tools run only if their permission (connector connected, folder
  approved) is granted.
- WRITE tools always pause and require explicit user approval via a card
  showing the tool, target, and exact change. Rejection executes nothing.
- If the user edits arguments on the card, the edited values are re-validated
  before execution.
- Every proposal and its outcome is written to the local action history.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..storage.db import Database
from .tools import RiskLevel, ToolRegistry, ToolResult

log = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    action_id: int
    tool: str
    args: dict[str, Any]
    preview: str
    editable: bool


@dataclass
class ApprovalResponse:
    approved: bool
    edited_args: dict[str, Any] | None = None


class PermissionDenied(Exception):
    pass


# UI supplies this: show a confirmation card, resolve when the user decides.
ApprovalCallback = Callable[[ApprovalRequest], Awaitable[ApprovalResponse]]
# Returns True when the named permission (e.g. "gmail", "calendar") is granted.
PermissionChecker = Callable[[str], bool]


class ActionGate:
    def __init__(
        self,
        db: Database,
        registry: ToolRegistry,
        permission_checker: PermissionChecker,
        request_approval: ApprovalCallback,
    ) -> None:
        self._db = db
        self._registry = registry
        self._has_permission = permission_checker
        self._request_approval = request_approval

    async def execute(
        self,
        tool_name: str,
        raw_args: dict[str, Any],
        conversation_id: int | None = None,
    ) -> ToolResult:
        """Validate, authorize, then execute a tool call. The only entry point."""
        spec = self._registry.get(tool_name)
        params = self._registry.validate_args(tool_name, raw_args)

        if not self._has_permission(spec.permission):
            self._db.record_action(
                tool_name,
                raw_args,
                spec.risk.value,
                "denied_no_permission",
                conversation_id=conversation_id,
            )
            return ToolResult(
                ok=False,
                error=(
                    f"Permission '{spec.permission}' is not granted. "
                    "Ask the user to enable it in the Permission Center."
                ),
            )

        preview = spec.render_preview(params)

        if spec.risk is RiskLevel.WRITE:
            action_id = self._db.record_action(
                tool_name,
                params.model_dump(mode="json"),
                spec.risk.value,
                "pending",
                preview,
                conversation_id,
            )
            response = await self._request_approval(
                ApprovalRequest(
                    action_id=action_id,
                    tool=tool_name,
                    args=params.model_dump(mode="json"),
                    preview=preview,
                    editable=True,
                )
            )
            if not response.approved:
                self._db.update_action(action_id, "rejected")
                log.info("Action %s (%s) rejected by user", action_id, tool_name)
                return ToolResult(ok=False, error="The user rejected this action. Do not retry it.")
            if response.edited_args is not None:
                # Re-validate anything the user changed before it can run.
                params = self._registry.validate_args(tool_name, response.edited_args)
        else:
            action_id = self._db.record_action(
                tool_name,
                params.model_dump(mode="json"),
                spec.risk.value,
                "auto_approved",
                preview,
                conversation_id,
            )

        try:
            result = await asyncio.wait_for(spec.handler(params), timeout=spec.timeout_s)
        except TimeoutError:
            self._db.update_action(action_id, "failed", f"timeout after {spec.timeout_s}s")
            return ToolResult(ok=False, error=f"{tool_name} timed out after {spec.timeout_s}s")
        except asyncio.CancelledError:
            self._db.update_action(action_id, "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 — surface tool bugs as tool errors
            log.exception("Tool %s raised", tool_name)
            self._db.update_action(action_id, "failed", str(exc))
            return ToolResult(ok=False, error=f"{tool_name} failed: {exc}")

        status = "completed" if result.ok else "failed"
        summary = result.error if not result.ok else _summarize(result)
        self._db.update_action(action_id, status, summary)
        return result


def _summarize(result: ToolResult, limit: int = 200) -> str:
    text = result.for_model()
    return text[:limit] + ("…" if len(text) > limit else "")
