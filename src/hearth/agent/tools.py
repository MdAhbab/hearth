"""Tool contract and registry.

Every tool declares: a name, a description the model sees, a Pydantic model
for its arguments (validated before anything runs), a risk level, the
permission it requires, a timeout, and a preview function that renders the
exact effect for the confirmation card. Only registered tools can ever be
executed, and only by the deterministic executor — the model just proposes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

_AUTHORIZED_LOCAL_RESOURCES: ContextVar[frozenset[str]] = ContextVar(
    "hearth_authorized_local_resources", default=frozenset()
)


def authorized_local_resources() -> frozenset[str]:
    return _AUTHORIZED_LOCAL_RESOURCES.get()


def set_authorized_local_resources(resources: frozenset[str]) -> Token:
    return _AUTHORIZED_LOCAL_RESOURCES.set(resources)


def reset_authorized_local_resources(token: Token) -> None:
    _AUTHORIZED_LOCAL_RESOURCES.reset(token)


class RiskLevel(StrEnum):
    READ = "read"  # runs automatically once the permission is granted
    WRITE = "write"  # always pauses for explicit user approval


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str = ""
    # base64 image for the model to *see* (vision models); empty otherwise.
    image_b64: str = ""

    def for_model(self) -> str:
        if not self.ok:
            return f"ERROR: {self.error}"
        if isinstance(self.data, str):
            return self.data
        import json

        return json.dumps(self.data, ensure_ascii=False, default=str)


@dataclass
class StagedAction:
    """Prepared reversible effect committed only after seal verification."""

    semantic_diff: str
    commit: Callable[[], Awaitable[ToolResult]]
    discard: Callable[[], Awaitable[None]]
    undo_metadata: Callable[[], dict[str, Any]] = field(default=lambda: {})
    undo: Callable[[], Awaitable[None]] | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    params_model: type[BaseModel]
    risk: RiskLevel
    permission: str  # e.g. "gmail", "calendar", "files", "mac", "shortcuts"
    handler: Callable[[BaseModel], Awaitable[ToolResult]]
    timeout_s: float = 30.0
    # Renders a human-readable preview of exactly what will happen.
    preview: Callable[[BaseModel], str] = field(default=None)  # type: ignore[assignment]
    # IntentSeal effect metadata. ``effect_adapter`` maps the validated argument
    # dict to a PredictedEffect; when unset the gate falls back to its
    # EffectAdapterRegistry (keyed by tool name) and then to a risk-based
    # default. ``irreversible`` and ``data_classes`` are advisory hints a tool
    # can declare without writing a full adapter. Typed as ``Any`` so tools.py
    # stays free of an import cycle with the assurance package.
    effect_adapter: Callable[[dict[str, Any]], Any] | None = None
    irreversible: bool = False
    data_classes: tuple[str, ...] = ()
    manifest_hash: str = ""
    identity_namespace: str = "hearth.builtin"
    publisher: str = "hearth"
    server_identity: str = ""
    rollback_supported: bool = False
    postcondition_supported: bool = False
    state_probe: Callable[[BaseModel], Awaitable[str] | str] | None = None
    expected_post_state: Callable[[BaseModel], str] | None = None
    stager: Callable[[BaseModel], Awaitable[StagedAction | ToolResult]] | None = None
    idempotency: bool = True

    def __post_init__(self) -> None:
        if self.manifest_hash:
            return
        import hashlib
        import json

        declaration = {
            "name": self.name,
            "schema": self.params_model.model_json_schema(),
            "risk": self.risk.value,
            "permission": self.permission,
            "namespace": self.identity_namespace,
            "publisher": self.publisher,
            "server": self.server_identity,
            "irreversible": self.irreversible,
            "data_classes": list(self.data_classes),
            "rollback_supported": self.rollback_supported,
            "postcondition_supported": self.postcondition_supported,
            "expected_post_state": bool(self.expected_post_state),
        }
        raw = json.dumps(declaration, sort_keys=True, separators=(",", ":"), default=str)
        self.manifest_hash = hashlib.sha256(raw.encode()).hexdigest()

    def render_preview(self, params: BaseModel) -> str:
        if self.preview:
            return self.preview(params)
        return f"{self.name}({params.model_dump_json()})"

    def to_ollama(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.params_model.model_json_schema(),
            },
        }


class ToolValidationError(Exception):
    pass


class UnknownToolError(Exception):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"Tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(f"Unknown tool: {name}") from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def ollama_tools(self) -> list[dict[str, Any]]:
        return [spec.to_ollama() for spec in self._tools.values()]

    def validate_args(self, name: str, raw_args: dict[str, Any]) -> BaseModel:
        """Validate model-produced arguments. Raises before anything executes."""
        spec = self.get(name)
        try:
            return spec.params_model.model_validate(raw_args)
        except ValidationError as exc:
            raise ToolValidationError(
                f"Invalid arguments for {name}: {exc.errors(include_url=False)}"
            ) from exc
