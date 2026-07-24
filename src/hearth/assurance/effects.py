"""Effect adapters: predict what a tool call will actually do.

An adapter maps a tool name + validated arguments to a :class:`PredictedEffect`
— action class, canonical target, effect kinds (egress, audience expansion,
bulk, recurring, physical, irreversible), reversibility, and quantity. The
policy engine reasons over the *predicted effect*, never the tool's name or the
model's prose, so a mislabeled or renamed tool cannot smuggle authority.

Built-in adapters cover Hearth's real tools. The benchmark registers extra
adapters for its inert TCP/IoT emulators. A tool with no adapter gets a
conservative default derived from its risk level.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .canonical import (
    canonical_app,
    canonical_file,
    canonical_recipient,
    canonical_shortcut,
    canonical_url,
)
from .types import (
    ActionClass,
    CanonicalTarget,
    DataClass,
    EffectKind,
    PredictedEffect,
)

# An adapter reads the validated argument dict and returns a predicted effect.
EffectAdapter = Callable[[dict[str, Any]], PredictedEffect]


class EffectAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, EffectAdapter] = {}

    def register(self, tool_name: str, adapter: EffectAdapter) -> None:
        self._adapters[tool_name] = adapter

    def has(self, tool_name: str) -> bool:
        return tool_name in self._adapters

    def predict(self, tool_name: str, args: dict[str, Any], is_write: bool) -> PredictedEffect:
        adapter = self._adapters.get(tool_name)
        if adapter is not None:
            return adapter(args)
        return _default_effect(tool_name, args, is_write)


def _default_effect(tool_name: str, args: dict[str, Any], is_write: bool) -> PredictedEffect:
    """Conservative fallback for tools without a registered adapter.

    Reads are ALLOW-eligible; writes are treated as local mutations that
    require approval, so an unknown WRITE tool keeps legacy confirmation
    semantics rather than being silently allowed or denied.
    """
    if not is_write:
        return PredictedEffect(action_class=ActionClass.READ, description=f"read via {tool_name}")
    target = _guess_target(args)
    return PredictedEffect(
        action_class=ActionClass.EXECUTE,
        target=target,
        effect_kinds=frozenset(
            {EffectKind.WRITE, EffectKind.EGRESS, EffectKind.IRREVERSIBLE}
        ),
        reversible=False,
        egress=True,
        flags=frozenset({"unknown_effect"}),
        description=f"unverified dynamic effect via {tool_name}",
    )


_TARGET_FIELDS = ("path", "url", "to", "recipient", "destination", "app", "name")


def _guess_target(args: dict[str, Any]) -> CanonicalTarget:
    for field_name in _TARGET_FIELDS:
        if field_name in args and args[field_name]:
            value = str(args[field_name])
            if field_name in ("to", "recipient"):
                return canonical_recipient(value)
            if field_name == "url":
                return canonical_url(value)
            if field_name in ("path", "destination"):
                return canonical_file(value)
            if field_name == "app":
                return canonical_app(value)
            return CanonicalTarget(kind="opaque", canonical_id=value)
    return CanonicalTarget.NONE


# --------------------------------------------------------------------------- #
# Built-in adapters for Hearth's real tools
# --------------------------------------------------------------------------- #
def register_builtin_adapters(reg: EffectAdapterRegistry) -> None:
    # Pure/local reads.
    for name in (
        "time_now",
        "calculate",
        "convert_units",
        "gmail_search",
        "gmail_read_message",
        "calendar_list_calendars",
        "calendar_list_events",
        "calendar_find_free_slots",
        "files_list",
        "files_search",
        "files_read",
        "files_search_content",
        "clipboard_read",
        "chrome_active_tab",
        "system_disk_usage",
        "system_running_processes",
        "reminders_list",
    ):
        reg.register(name, lambda a, tool=name: _read(tool))

    reg.register("gmail_send_message", _gmail_send)
    reg.register("gmail_create_draft", _gmail_draft)
    reg.register("calendar_create_event", _cal_create)
    reg.register("calendar_update_event", _cal_update)
    reg.register("calendar_delete_event", _cal_delete)
    reg.register("files_write", _files_write)
    reg.register("files_move", _files_move)
    reg.register("files_delete", _files_delete)
    reg.register("files_view_image", _files_view_image)
    reg.register("clipboard_write", _clipboard_write)
    reg.register("system_open_url", _open_url)
    reg.register("system_reveal_file", _reveal_file)
    reg.register("system_notify", _notify)
    reg.register("system_open_app", _open_app)
    reg.register("web_fetch", _web_fetch)
    reg.register("weather_current", _weather)
    reg.register("system_run_shortcut", _run_shortcut)
    reg.register("system_screenshot", _screenshot)
    reg.register("reminders_create", _reminder_create)
    reg.register("reminders_complete", _reminder_complete)


def _read(name: str) -> PredictedEffect:
    return PredictedEffect(action_class=ActionClass.READ, description=f"read via {name}")


def _gmail_send(args: dict[str, Any]) -> PredictedEffect:
    to = canonical_recipient(str(args.get("to", "")))
    return PredictedEffect(
        action_class=ActionClass.SEND_EXTERNAL,
        target=to,
        effect_kinds=frozenset({EffectKind.EGRESS, EffectKind.IRREVERSIBLE}),
        audience=(to.canonical_id,),
        reversible=False,
        egress=True,
        description=f"send email to {to.canonical_id} — subject {args.get('subject', '')!r}",
    )


def _gmail_draft(args: dict[str, Any]) -> PredictedEffect:
    to = canonical_recipient(str(args.get("to", "")))
    return PredictedEffect(
        action_class=ActionClass.SEND_EXTERNAL,
        target=to,
        effect_kinds=frozenset({EffectKind.EGRESS}),
        audience=(),  # a draft is not delivered to the recipient
        reversible=True,
        egress=True,
        description=f"create draft to {to.canonical_id} — subject {args.get('subject', '')!r}",
    )


def _cal_create(args: dict[str, Any]) -> PredictedEffect:
    recurring = bool(args.get("recurrence") or args.get("rrule"))
    kinds = {EffectKind.WRITE}
    if recurring:
        kinds |= {EffectKind.RECURRING}
    return PredictedEffect(
        action_class=ActionClass.WRITE_LOCAL,
        target=CanonicalTarget(
            kind="calendar_event",
            canonical_id=str(args.get("calendar_id", "") or "default"),
            attributes={"title": args.get("title", ""), "recurring": recurring},
        ),
        effect_kinds=frozenset(kinds),
        reversible=True,
        description=f"create calendar event {args.get('title', '')!r}",
    )


def _cal_update(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.WRITE_LOCAL,
        target=CanonicalTarget(
            kind="calendar_event",
            canonical_id=str(args.get("event_id", "")),
            attributes={"calendar_id": args.get("calendar_id", "")},
        ),
        effect_kinds=frozenset({EffectKind.WRITE}),
        reversible=True,
        description=f"update calendar event {args.get('event_id', '')}",
    )


def _cal_delete(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.DELETE,
        target=CanonicalTarget(kind="calendar_event", canonical_id=str(args.get("event_id", ""))),
        effect_kinds=frozenset({EffectKind.WRITE, EffectKind.IRREVERSIBLE}),
        reversible=False,
        description=f"delete calendar event {args.get('event_id', '')}",
    )


def _files_write(args: dict[str, Any]) -> PredictedEffect:
    target = canonical_file(str(args.get("path", "")))
    return PredictedEffect(
        action_class=ActionClass.WRITE_LOCAL,
        target=target,
        effect_kinds=frozenset({EffectKind.WRITE}),
        reversible=True,
        # Pin the exact bytes the user is approving so a change between the
        # confirmation card and execution fails the seal (TOCTOU defense).
        pre_state_hash=target.attributes.get("content_hash", ""),
        description=f"write file {target.canonical_id}",
    )


def _files_move(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.WRITE_LOCAL,
        target=canonical_file(str(args.get("destination", ""))),
        effect_kinds=frozenset({EffectKind.WRITE}),
        reversible=True,
        description=f"move {args.get('source', '')} -> {args.get('destination', '')}",
    )


def _files_delete(args: dict[str, Any]) -> PredictedEffect:
    # Hearth deletes to Trash → reversible.
    target = canonical_file(str(args.get("path", "")))
    return PredictedEffect(
        action_class=ActionClass.DELETE,
        target=target,
        effect_kinds=frozenset({EffectKind.WRITE}),
        reversible=True,
        # Bind the file identity being deleted so a swap between preview and
        # execution (a different file now at that path) fails the seal.
        pre_state_hash=target.attributes.get("content_hash", ""),
        description=f"move to Trash {args.get('path', '')}",
    )


def _clipboard_write(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.WRITE_LOCAL,
        target=CanonicalTarget(kind="clipboard", canonical_id="system_clipboard"),
        effect_kinds=frozenset({EffectKind.WRITE}),
        reversible=True,
        description="replace clipboard contents",
    )


def _files_view_image(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.READ,
        target=canonical_file(str(args.get("path", ""))),
        data_out=frozenset({DataClass.PRIVATE_DOC}),
        description="read a local image for model analysis",
    )


def _open_url(args: dict[str, Any]) -> PredictedEffect:
    url = canonical_url(str(args.get("url", "")))
    return PredictedEffect(
        action_class=ActionClass.EGRESS,
        target=url,
        effect_kinds=frozenset({EffectKind.EGRESS}),
        reversible=True,
        egress=True,
        description=f"open URL {url.canonical_id}",
    )


def _reveal_file(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.EXECUTE,
        target=canonical_file(str(args.get("path", ""))),
        effect_kinds=frozenset({EffectKind.WRITE, EffectKind.IRREVERSIBLE}),
        reversible=False,
        description=f"reveal {args.get('path', '')} in the file manager",
    )


def _notify(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.WRITE_LOCAL,
        target=CanonicalTarget(kind="notification", canonical_id="desktop"),
        effect_kinds=frozenset({EffectKind.WRITE, EffectKind.IRREVERSIBLE}),
        reversible=False,
        description=f"show notification {args.get('title', '')!r}",
    )


def _open_app(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.EXECUTE,
        target=canonical_app(str(args.get("app", ""))),
        effect_kinds=frozenset({EffectKind.WRITE, EffectKind.IRREVERSIBLE}),
        reversible=False,
        description=f"open application {args.get('app', '')}",
    )


def _web_fetch(args: dict[str, Any]) -> PredictedEffect:
    url = canonical_url(str(args.get("url", "")))
    return PredictedEffect(
        action_class=ActionClass.EGRESS,
        target=url,
        effect_kinds=frozenset({EffectKind.EGRESS}),
        reversible=True,
        egress=True,
        description=f"fetch web page {url.canonical_id}",
    )


def _weather(args: dict[str, Any]) -> PredictedEffect:
    target = CanonicalTarget(
        kind="url",
        canonical_id="https://weather-provider.invalid/",
        attributes={"host": "weather-provider.invalid", "zone": "public"},
    )
    return PredictedEffect(
        action_class=ActionClass.EGRESS,
        target=target,
        effect_kinds=frozenset({EffectKind.EGRESS}),
        egress=True,
        description=f"fetch weather for {args.get('location', args.get('city', ''))}",
    )


def _run_shortcut(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.EXECUTE,
        target=canonical_shortcut(str(args.get("name", ""))),
        effect_kinds=frozenset({EffectKind.WRITE, EffectKind.IRREVERSIBLE}),
        reversible=False,
        description=f"run shortcut {args.get('name', '')}",
    )


def _screenshot(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.READ,
        target=CanonicalTarget(kind="screen", canonical_id="primary_screen"),
        data_out=frozenset({DataClass.PRIVATE_DOC}),
        description="capture the primary screen for the model",
    )


def _reminder_create(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.WRITE_LOCAL,
        target=CanonicalTarget(kind="reminder", canonical_id=str(args.get("title", ""))),
        effect_kinds=frozenset({EffectKind.WRITE}),
        reversible=True,
        description=f"create reminder {args.get('title', '')!r}",
    )


def _reminder_complete(args: dict[str, Any]) -> PredictedEffect:
    return PredictedEffect(
        action_class=ActionClass.WRITE_LOCAL,
        target=CanonicalTarget(kind="reminder", canonical_id=str(args.get("reminder_id", ""))),
        effect_kinds=frozenset({EffectKind.WRITE}),
        reversible=True,
        description=f"complete reminder {args.get('reminder_id', '')}",
    )
