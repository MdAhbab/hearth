"""Seal issuer and verifier — the one-use capability broker.

A seal is a short-lived HMAC-signed mandate bound to an exact proposed effect.
The executor verifies and *consumes* it immediately before the tool runs. The
binding covers principal, frozen intent, tool+action, canonical resource,
argument hash, audience, data labels, and a pre-state hash. Verification fails
closed on any of: bad signature, expiry, a spent nonce (replay), edited
arguments, resource drift, tool-identity/manifest drift, or a pre-state that no
longer matches (TOCTOU).

The signing key lives in the OS credential store in production; tests pass an
explicit key. No LLM can mint or verify a seal — that is the whole point.
"""

from __future__ import annotations

import hmac
import secrets as _secrets
import time
from dataclasses import asdict
from hashlib import sha256
from typing import Protocol

from .types import (
    IntentCapsule,
    PolicyResult,
    Principal,
    Proposal,
    Seal,
    new_nonce,
    stable_hash,
    stable_json,
)

_KEY_NAME = "intentseal-signing-key"


class SealStore(Protocol):
    """Persists spent nonces so a seal can be used at most once."""

    def mark_used(self, nonce: str) -> bool:
        """Record ``nonce`` as consumed. Return True if newly used, False if
        it was already spent (i.e. a replay attempt)."""
        ...

    def is_used(self, nonce: str) -> bool: ...


class InMemorySealStore:
    """Nonce store for tests and the benchmark harness."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def mark_used(self, nonce: str) -> bool:
        if nonce in self._used:
            return False
        self._used.add(nonce)
        return True

    def is_used(self, nonce: str) -> bool:
        return nonce in self._used


def get_or_create_key(secret_store, key_name: str = _KEY_NAME) -> bytes:
    """Fetch the hex signing key from a SecretStore, creating it once.

    The key never leaves the credential store as anything but bytes held in
    process memory for signing; it is never written to SQLite, config, or logs.
    """
    existing = secret_store.get(key_name)
    if existing:
        return bytes.fromhex(existing)
    key = _secrets.token_bytes(32)
    secret_store.set(key_name, key.hex())
    return key


def _audience_hash(proposal: Proposal) -> str:
    return stable_hash(sorted(proposal.effect.audience))


def _data_labels(proposal: Proposal) -> tuple[str, ...]:
    labels: set[str] = set()
    for arg in proposal.bound_args:
        labels.update(str(c) for c in arg.data_classes)
    labels.update(str(c) for c in proposal.effect.data_out)
    return tuple(sorted(labels))


def _action_name(proposal: Proposal) -> str:
    return f"{proposal.tool.name}:{proposal.effect.action_class}"


def _effect_hash(proposal: Proposal) -> str:
    return stable_hash(asdict(proposal.effect))


def _provenance_hash(proposal: Proposal) -> str:
    return stable_hash(
        {
            "args": [
                {
                    "name": arg.name,
                    "source": arg.source_ref,
                    "classes": sorted(arg.data_classes),
                }
                for arg in proposal.bound_args
            ],
            "evidence": [
                {
                    "id": ref.ref_id,
                    "origin": ref.origin,
                    "classes": sorted(ref.data_classes),
                    "source": ref.source,
                    "field": ref.field_path,
                    "lineage": list(ref.lineage),
                }
                for ref in proposal.evidence
            ],
        }
    )


class SealIssuer:
    def __init__(self, key: bytes, store: SealStore | None = None) -> None:
        self._key = key
        self._store = store or InMemorySealStore()

    # -- signing ------------------------------------------------------------ #
    def _sign(self, seal: Seal) -> str:
        return hmac.new(self._key, stable_json(seal.payload()).encode("utf-8"), sha256).hexdigest()

    def issue(
        self,
        proposal: Proposal,
        capsule: IntentCapsule | None,
        *,
        approval_id: str = "",
        approval_state: str = "",
        policy: PolicyResult | None = None,
        principal: Principal | None = None,
        budget: int = 1,
        ttl_s: float = 120.0,
        now: float | None = None,
    ) -> Seal:
        """Mint a fresh one-use seal bound to this exact proposal."""
        now = now if now is not None else time.time()
        intent_hash = capsule.intent_hash() if capsule is not None else ""
        effective_principal = principal or (capsule.principal if capsule is not None else None)
        seal = Seal(
            nonce=new_nonce(),
            principal=effective_principal.key() if effective_principal is not None else "",
            intent_hash=intent_hash,
            action=_action_name(proposal),
            tool_identity=proposal.tool.key(),
            predicted_effect=_effect_hash(proposal),
            policy_decision=str(policy.decision) if policy is not None else "",
            approval_state=approval_state,
            provenance_hash=_provenance_hash(proposal),
            canonical_resource=proposal.effect.target.key(),
            args_hash=proposal.args_hash(),
            audience_hash=_audience_hash(proposal),
            data_labels=_data_labels(proposal),
            pre_state_hash=proposal.effect.pre_state_hash,
            approval_id=approval_id,
            budget=budget,
            issued_at=now,
            expiry=now + ttl_s,
        )
        object.__setattr__(seal, "signature", self._sign(seal))
        return seal

    # -- verification ------------------------------------------------------- #
    def verify_and_consume(
        self,
        seal: Seal,
        proposal: Proposal,
        capsule: IntentCapsule | None,
        *,
        current_pre_state: str | None = None,
        policy: PolicyResult | None = None,
        principal: Principal | None = None,
        approval_id: str = "",
        approval_state: str = "",
        budget: int | None = None,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """Verify a seal against the *current* proposal and consume its nonce.

        Called immediately before the effect. A binding mismatch fails closed
        without spending the nonce (so a corrected proposal can be re-sealed);
        a replay of an already-spent nonce fails as a replay.
        """
        now = now if now is not None else time.time()

        # Signature integrity first — nothing else is trustworthy otherwise.
        expected_sig = self._sign(seal)
        if not hmac.compare_digest(expected_sig, seal.signature):
            return False, "seal signature invalid"
        if seal.expired(now):
            return False, "seal expired"
        # Replay: has this nonce already been spent?
        if self._store.is_used(seal.nonce):
            return False, "seal already used (replay)"

        # Binding: the seal must still describe the proposal about to run.
        if seal.args_hash != proposal.args_hash():
            return False, "arguments changed after the seal was issued"
        if seal.tool_identity != proposal.tool.key():
            return False, "full tool identity or manifest changed after the seal was issued"
        if seal.predicted_effect != _effect_hash(proposal):
            return False, "predicted effect changed after the seal was issued"
        if seal.canonical_resource != proposal.effect.target.key():
            return False, "canonical resource drifted after the seal was issued"
        if seal.action != _action_name(proposal):
            return False, "tool/action identity drifted after the seal was issued"
        if seal.audience_hash != _audience_hash(proposal):
            return False, "audience changed after the seal was issued"
        if seal.data_labels != _data_labels(proposal):
            return False, "provenance labels changed after the seal was issued"
        if seal.provenance_hash != _provenance_hash(proposal):
            return False, "provenance lineage changed after the seal was issued"
        expected_principal = principal or (capsule.principal if capsule is not None else None)
        if seal.principal != (expected_principal.key() if expected_principal is not None else ""):
            return False, "principal/account changed after the seal was issued"
        expected_intent = capsule.intent_hash() if capsule is not None else ""
        if seal.intent_hash != expected_intent:
            return False, "intent capsule changed after the seal was issued"
        expected_decision = str(policy.decision) if policy is not None else ""
        if seal.policy_decision != expected_decision:
            return False, "policy decision changed after the seal was issued"
        if seal.approval_id != approval_id:
            return False, "approval identity changed after the seal was issued"
        if seal.approval_state != approval_state:
            return False, "approval state changed after the seal was issued"
        if budget is not None and seal.budget != budget:
            return False, "budget changed after the seal was issued"
        # TOCTOU: pre-state must still match what the decision was made against.
        observed = (
            current_pre_state if current_pre_state is not None else proposal.effect.pre_state_hash
        )
        if seal.pre_state_hash != observed:
            return False, "resource pre-state changed since the seal was issued (TOCTOU)"

        # All bindings hold: consume the nonce exactly once.
        if not self._store.mark_used(seal.nonce):
            return False, "seal already used (replay)"
        return True, "verified"
