"""IntentSeal — a provenance-bound, one-use capability reference monitor.

Its singular invariant:

    No side effect may execute unless it carries a fresh, one-use seal binding
    the authenticated principal, frozen user intent, exact tool identity,
    canonical target, normalized arguments, provenance labels, predicted
    effect, pre-state, policy decision, approval (when required), budget,
    expiry, and nonce.

This is a deterministic security control, not a prompt filter or second-LLM
judge. A model may propose an intent or an effect and may explain a decision,
but it can never issue or verify a seal.
"""

from __future__ import annotations

from .audit import HashChainAudit, PostconditionResult, check_postcondition, redact_text
from .canonical import (
    LOCAL_ZONES,
    canonical_account,
    canonical_app,
    canonical_device,
    canonical_file,
    canonical_mcp_tool,
    canonical_recipient,
    canonical_shortcut,
    canonical_url,
    classify_host,
)
from .effects import EffectAdapterRegistry, register_builtin_adapters
from .evidence import EvidenceStore, classify_value, content_hash
from .monitor import AuthorizationResult, IntentSeal, TurnContext
from .policy import PolicyConfig, PolicyEngine, injection_score
from .seal import InMemorySealStore, SealIssuer, SealStore, get_or_create_key
from .transaction import (
    SemanticDiff,
    StagedFileWrite,
    Transaction,
    UndoRecord,
)
from .types import (
    BLOCKING,
    PERMISSIVE,
    SENSITIVE_DATA,
    TRUSTED_ORIGINS,
    ActionClass,
    ApprovalPolicy,
    BoundArg,
    CanonicalTarget,
    DataClass,
    Decision,
    EffectKind,
    EvidenceRef,
    IntentCapsule,
    Origin,
    PolicyResult,
    PredictedEffect,
    Principal,
    Proposal,
    Seal,
    ToolIdentity,
    stable_hash,
)

__all__ = [
    # monitor
    "IntentSeal",
    "TurnContext",
    "AuthorizationResult",
    # policy
    "PolicyConfig",
    "PolicyEngine",
    "injection_score",
    # types / enums
    "Decision",
    "Origin",
    "DataClass",
    "ActionClass",
    "EffectKind",
    "Principal",
    "EvidenceRef",
    "BoundArg",
    "ToolIdentity",
    "CanonicalTarget",
    "PredictedEffect",
    "IntentCapsule",
    "ApprovalPolicy",
    "Proposal",
    "PolicyResult",
    "Seal",
    "SENSITIVE_DATA",
    "TRUSTED_ORIGINS",
    "PERMISSIVE",
    "BLOCKING",
    "stable_hash",
    # evidence
    "EvidenceStore",
    "content_hash",
    "classify_value",
    # canonical
    "canonical_recipient",
    "canonical_account",
    "canonical_file",
    "canonical_url",
    "canonical_app",
    "canonical_shortcut",
    "canonical_mcp_tool",
    "canonical_device",
    "classify_host",
    "LOCAL_ZONES",
    # effects
    "EffectAdapterRegistry",
    "register_builtin_adapters",
    # seal
    "SealIssuer",
    "SealStore",
    "InMemorySealStore",
    "get_or_create_key",
    # transaction
    "Transaction",
    "StagedFileWrite",
    "SemanticDiff",
    "UndoRecord",
    # audit
    "HashChainAudit",
    "check_postcondition",
    "PostconditionResult",
    "redact_text",
]
