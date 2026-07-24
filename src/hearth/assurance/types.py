"""Core vocabulary for IntentSeal — the provenance-bound capability monitor.

Everything here is data: enums for the fixed decision/effect/trust space and
frozen dataclasses for the objects that flow through ``IntentSeal.authorize``.
Nothing in this module reaches out to the world; it only *describes* proposed
effects so the deterministic policy engine can reason about them.

The design follows the research canvas' one-shot mandate:

    principal + intent_hash + action + canonical_resource + args_hash
    + audience + data_labels + pre_state_hash + approval_id? + expiry + nonce

An LLM may *propose* the intent and the effect, but only this code path may
issue or verify a seal.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


# --------------------------------------------------------------------------- #
# Fixed decision / classification spaces
# --------------------------------------------------------------------------- #
class Decision(StrEnum):
    """The only outcomes the deterministic policy engine may return."""

    ALLOW = "ALLOW"  # required by intent, satisfies every constraint
    DENY = "DENY"  # outside mandate / forbidden sink / uncanonicalizable
    ASK = "ASK"  # material ambiguity or irreversible/cross-boundary effect
    REDACT = "REDACT"  # allowed, but protected fields must not reach the sink
    QUARANTINE = "QUARANTINE"  # inspect as data only; no control-flow authority


# Decisions that permit the effect to run (possibly after user approval).
PERMISSIVE = frozenset({Decision.ALLOW, Decision.ASK, Decision.REDACT})
# Decisions that block the effect outright.
BLOCKING = frozenset({Decision.DENY, Decision.QUARANTINE})


class Origin(StrEnum):
    """Where a value entered the turn from. Trust is derived from this."""

    USER = "user"  # typed by the authenticated human — trusted literal
    SYSTEM = "system"  # Hearth's own trusted configuration
    EMAIL = "email"
    ICS = "ics"
    WEB = "web"
    FILE = "file"
    TOOL_OUTPUT = "tool_output"
    MCP = "mcp"
    MEMORY = "memory"
    HISTORY = "history"  # prior conversation turns leaving the machine
    DEVICE = "device"  # IoT / TCP / local-service telemetry
    CLIPBOARD = "clipboard"
    SCREENSHOT = "screenshot"


# Origins whose content carries user authority. Everything else is untrusted
# data that may be *read* but can never become a control instruction.
TRUSTED_ORIGINS = frozenset({Origin.USER, Origin.SYSTEM})


class DataClass(StrEnum):
    """Protected data categories used for provenance-to-sink and redaction."""

    PUBLIC = "public"
    PII = "pii"  # personal identifiers
    PRIVATE_DOC = "private_doc"  # user documents / notes
    SECRET = "secret"  # credentials, tokens, API keys, passwords
    CANARY = "canary"  # synthetic protected token tracked by the benchmark
    SPATIAL = "spatial"  # maps / floorplans
    HEALTH = "health"
    CLIPBOARD = "clipboard"


# Data classes that must never leave the machine or cross to an external sink
# without an explicit, data-class-aware decision.
SENSITIVE_DATA = frozenset(
    {DataClass.SECRET, DataClass.CANARY, DataClass.PII, DataClass.PRIVATE_DOC,
     DataClass.SPATIAL, DataClass.HEALTH, DataClass.CLIPBOARD}
)


class ActionClass(StrEnum):
    """Semantic category of a tool's effect (independent of its name)."""

    READ = "read"
    WRITE_LOCAL = "write_local"  # local file/calendar/reminder mutation
    SEND_EXTERNAL = "send_external"  # email / message leaving the machine
    EGRESS = "egress"  # network fetch / open URL
    DELETE = "delete"
    PURCHASE = "purchase"
    CREDENTIAL = "credential"  # touches secrets/tokens
    PHYSICAL = "physical"  # IoT actuation (lock, thermostat, garage…)
    EXECUTE = "execute"  # run a shortcut / external tool of unknown effect


class EffectKind(StrEnum):
    """Predicted properties of an effect; a single effect may carry several."""

    READ = "read"
    WRITE = "write"
    EGRESS = "egress"
    AUDIENCE_EXPANSION = "audience_expansion"
    BULK = "bulk"  # affects many items at once
    RECURRING = "recurring"  # repeats over time
    PHYSICAL = "physical"
    IRREVERSIBLE = "irreversible"


# --------------------------------------------------------------------------- #
# Hashing helpers — one stable canonical serialization for every hash
# --------------------------------------------------------------------------- #
def stable_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace jitter, str fallback."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def stable_hash(obj: Any) -> str:
    """SHA-256 of the stable serialization; the identity of anything hashed."""
    return hashlib.sha256(stable_json(obj).encode("utf-8")).hexdigest()


def new_nonce() -> str:
    return uuid.uuid4().hex


# --------------------------------------------------------------------------- #
# Principals and evidence
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Principal:
    """The authenticated human and the account an action runs under."""

    user_id: str
    account: str = ""  # e.g. "gmail:user-a@test.invalid"
    display_name: str = ""

    def key(self) -> str:
        return f"{self.user_id}|{self.account}"


@dataclass(frozen=True)
class EvidenceRef:
    """An immutable reference to a value extracted from some origin.

    Untrusted content cannot upgrade its origin: an ``EvidenceRef`` minted from
    an email stays ``EMAIL`` forever, even if a later summary, tool echo, or
    memory write copies its text. The ``ref_id`` is content-addressed so the
    same value from the same origin is stable across a turn.
    """

    ref_id: str
    origin: Origin
    content_hash: str
    data_classes: frozenset[DataClass] = frozenset({DataClass.PUBLIC})
    preview: str = ""  # short, redaction-safe description (never raw secrets)
    source: str = ""  # stable connector/resource identity, never raw content
    field_path: str = ""  # typed field within the source, e.g. messages.0.body
    lineage: tuple[str, ...] = ()  # parent EvidenceRef ids through transformations
    expires_at: float = 0.0

    @property
    def trusted(self) -> bool:
        return self.origin in TRUSTED_ORIGINS

    @property
    def sensitive(self) -> bool:
        return bool(self.data_classes & SENSITIVE_DATA)

    def expired(self, now: float | None = None) -> bool:
        return bool(self.expires_at and (now or time.time()) > self.expires_at)


@dataclass(frozen=True)
class BoundArg:
    """A tool argument bound to its provenance.

    ``source_ref`` is the id of the ``EvidenceRef`` the value came from, or the
    literal sentinel ``"literal"`` when the authenticated user typed it. A
    sensitive argument that resolves to neither fails closed in the policy.
    """

    name: str
    value: Any
    source_ref: str  # EvidenceRef.ref_id or "literal"
    data_classes: frozenset[DataClass] = frozenset({DataClass.PUBLIC})

    LITERAL = "literal"

    @property
    def from_literal(self) -> bool:
        return self.source_ref == self.LITERAL


# --------------------------------------------------------------------------- #
# Tool identity, canonical targets, predicted effects
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToolIdentity:
    """Stable identity of the tool being invoked.

    ``manifest_hash`` pins MCP tool schemas so post-approval drift is detected;
    ``bundle_id`` pins macOS apps; both default empty for built-in tools.
    """

    name: str
    manifest_hash: str = ""
    bundle_id: str = ""
    namespace: str = "hearth"
    publisher: str = "hearth"
    server: str = ""

    def key(self) -> str:
        return stable_hash(
            {
                "name": self.name,
                "manifest_hash": self.manifest_hash,
                "bundle_id": self.bundle_id,
                "namespace": self.namespace,
                "publisher": self.publisher,
                "server": self.server,
            }
        )


@dataclass(frozen=True)
class CanonicalTarget:
    """A resolved, stable identity for the resource an effect touches.

    ``kind`` is one of recipient/file/url/calendar/reminder/app/shortcut/mcp/
    device/service/none. ``canonical_id`` is the comparable identity string
    (realpath, normalized email, host:port/path, event uid…). ``attributes``
    carries the extra bindings a policy may need (host, port, protocol,
    content_hash, group_size…).
    """

    kind: str
    canonical_id: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return stable_hash([self.kind, self.canonical_id, self.attributes])


# The empty target, referenced as ``CanonicalTarget.NONE`` throughout. Assigned
# after the class body so it is not mistaken for a dataclass field.
CanonicalTarget.NONE = CanonicalTarget(kind="none", canonical_id="")  # type: ignore[attr-defined]


@dataclass(frozen=True)
class PredictedEffect:
    """What an effect adapter predicts a tool call will do to the world."""

    action_class: ActionClass
    target: CanonicalTarget = CanonicalTarget.NONE
    effect_kinds: frozenset[EffectKind] = frozenset()
    audience: tuple[str, ...] = ()  # canonical recipient ids the effect reaches
    data_out: frozenset[DataClass] = frozenset()  # data classes leaving the sink
    reversible: bool = True
    quantity: int = 1  # number of items affected (bulk detection)
    egress: bool = False  # value crosses the machine boundary
    physical: bool = False
    pre_state_hash: str = ""  # snapshot the decision was made against
    description: str = ""  # human-readable, shown on the confirmation card
    # Extra signals a canonicalizer / effect adapter computes that are not
    # derivable from the action class alone, e.g. "manifest_drift",
    # "precondition_mismatch", "outside_root", "identity_conflict",
    # "ambiguous", "unapproved_scheme", "redirect_to_local".
    flags: frozenset[str] = frozenset()

    def with_pre_state(self, pre_state_hash: str) -> PredictedEffect:
        return replace(self, pre_state_hash=pre_state_hash)


# --------------------------------------------------------------------------- #
# Intent capsule
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ApprovalPolicy:
    """When a capsule requires explicit user approval before an effect runs."""

    require_for_writes: bool = True
    require_for_egress: bool = True
    require_for_physical: bool = True
    require_for_irreversible: bool = True


@dataclass(frozen=True)
class IntentCapsule:
    """The frozen, turn-scoped statement of what the user actually wants.

    The model may *draft* a capsule, but an effectful or ambiguous capsule is
    confirmed by the user and then frozen. Once frozen it cannot be widened —
    later model output can only propose effects that fit inside it. ``None`` in
    a collection means "unconstrained" (legacy compatibility); an *empty* set
    means "nothing allowed".
    """

    goal: str
    principal: Principal
    allowed_action_classes: frozenset[ActionClass] | None = None
    allowed_resources: tuple[str, ...] | None = None  # canonical-id prefixes
    allowed_recipients: tuple[str, ...] | None = None  # canonical recipient ids
    protected_data_classes: frozenset[DataClass] = SENSITIVE_DATA
    max_quantity: int = 1
    max_cost: float = 0.0
    approval: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    created_at: float = field(default_factory=time.time)
    ttl_s: float = 900.0  # capsules expire so stale intent cannot be replayed
    frozen: bool = False

    def freeze(self) -> IntentCapsule:
        """Return a frozen copy. Freezing is what confirmation produces."""
        return replace(self, frozen=True)

    @property
    def expiry(self) -> float:
        return self.created_at + self.ttl_s

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) > self.expiry

    def intent_hash(self) -> str:
        """Content identity of the frozen goal + authority envelope."""
        return stable_hash(
            {
                "goal": self.goal,
                "principal": self.principal.key(),
                "actions": sorted(self.allowed_action_classes)
                if self.allowed_action_classes is not None
                else None,
                "resources": list(self.allowed_resources)
                if self.allowed_resources is not None
                else None,
                "recipients": list(self.allowed_recipients)
                if self.allowed_recipients is not None
                else None,
                "protected": sorted(self.protected_data_classes),
                "max_quantity": self.max_quantity,
                "max_cost": self.max_cost,
            }
        )


# --------------------------------------------------------------------------- #
# Proposal, policy result, seal
# --------------------------------------------------------------------------- #
@dataclass
class Proposal:
    """One proposed side effect, assembled by the gate before authorization."""

    tool: ToolIdentity
    args: dict[str, Any]
    effect: PredictedEffect
    bound_args: tuple[BoundArg, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    approval_id: str = ""  # set once the user has approved (for ASK outcomes)

    def args_hash(self) -> str:
        return stable_hash(self.args)


@dataclass
class PolicyResult:
    decision: Decision
    reasons: tuple[str, ...] = ()
    redact_fields: tuple[str, ...] = ()  # arg names/paths to strip before the sink
    requires_approval: bool = False
    escalation: str = ""  # why the user is being asked (shown on the card)

    @property
    def blocked(self) -> bool:
        return self.decision in BLOCKING

    @property
    def permitted(self) -> bool:
        return self.decision in PERMISSIVE


@dataclass(frozen=True)
class Seal:
    """A fresh, one-use capability mandate bound to an exact proposed effect.

    Verified and consumed by the executor immediately before the tool runs.
    Any drift in args, resource, pre-state, tool identity, or expiry — or a
    replay of a spent nonce — makes verification fail closed.
    """

    nonce: str
    principal: str
    intent_hash: str
    action: str  # tool name + action class
    tool_identity: str
    predicted_effect: str
    policy_decision: str
    approval_state: str
    provenance_hash: str
    canonical_resource: str
    args_hash: str
    audience_hash: str
    data_labels: tuple[str, ...]
    pre_state_hash: str
    approval_id: str
    budget: int
    issued_at: float
    expiry: float
    signature: str = ""  # filled in by the seal issuer

    def payload(self) -> dict[str, Any]:
        """The signed content — every field except the signature itself."""
        return {
            "nonce": self.nonce,
            "principal": self.principal,
            "intent_hash": self.intent_hash,
            "action": self.action,
            "tool_identity": self.tool_identity,
            "predicted_effect": self.predicted_effect,
            "policy_decision": self.policy_decision,
            "approval_state": self.approval_state,
            "provenance_hash": self.provenance_hash,
            "canonical_resource": self.canonical_resource,
            "args_hash": self.args_hash,
            "audience_hash": self.audience_hash,
            "data_labels": list(self.data_labels),
            "pre_state_hash": self.pre_state_hash,
            "approval_id": self.approval_id,
            "budget": self.budget,
            "issued_at": self.issued_at,
            "expiry": self.expiry,
        }

    def expired(self, now: float | None = None) -> bool:
        return (now or time.time()) > self.expiry
