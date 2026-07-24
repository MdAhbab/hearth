"""ActionGate — the single chokepoint between a proposed tool call and any
effect on the world, now mediated by IntentSeal.

Order of operations (one path, no bypass):
1. Validate arguments against the tool's schema.
2. Check the connector/folder permission.
3. Build a Proposal (tool identity, provenance-bound args, predicted effect)
   and run ``IntentSeal.authorize`` before any handler.
4. On DENY/QUARANTINE: audit and return without a misleading approval card.
5. On ASK/REDACT — or any WRITE tool (legacy confirmation is preserved until
   tests prove a narrower policy is safe) — show a richer confirmation card.
6. Any edit re-validates, re-canonicalizes, re-runs policy, and re-issues the
   seal.
7. The executor verifies and consumes the one-use seal immediately before the
   effect, then verifies postconditions and closes the audit record.

Constructed with only ``(db, registry, permission_checker, request_approval)``
the gate builds a default in-memory IntentSeal, so existing call sites and
tests keep working; the app injects a Keychain-keyed, SQLite-backed instance.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from ..assurance import (
    CanonicalTarget,
    EffectAdapterRegistry,
    InMemorySealStore,
    IntentCapsule,
    IntentSeal,
    Origin,
    PolicyConfig,
    Proposal,
    ToolIdentity,
    TurnContext,
    canonical_file,
    register_builtin_adapters,
    stable_hash,
)
from ..assurance.types import ActionClass, DataClass, Decision, EffectKind, PredictedEffect
from ..storage.db import Database
from .tools import (
    RiskLevel,
    StagedAction,
    ToolRegistry,
    ToolResult,
    ToolValidationError,
    reset_authorized_local_resources,
    set_authorized_local_resources,
)

log = logging.getLogger(__name__)


@dataclass
class ApprovalRequest:
    action_id: int
    tool: str
    args: dict[str, Any]
    preview: str
    editable: bool
    # Richer IntentSeal context for the confirmation card (all optional so the
    # legacy card and existing tests keep working).
    decision: str = "ASK"
    reasons: tuple[str, ...] = ()
    escalation: str = ""
    canonical_target: str = ""
    audience: tuple[str, ...] = ()
    data_out: tuple[str, ...] = ()
    reversible: bool = True
    redact_fields: tuple[str, ...] = ()
    intent_confirmation: bool = False
    intent_goal: str = ""
    principal: str = ""
    account: str = ""
    semantic_diff: str = ""
    provenance: tuple[str, ...] = ()


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


class DbSealStore:
    """SealStore backed by the SQLite ``seal_nonces`` table (persistent,
    cross-session replay protection)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def mark_used(self, nonce: str) -> bool:
        return self._db.mark_seal_nonce(nonce)

    def is_used(self, nonce: str) -> bool:
        return self._db.is_seal_nonce_used(nonce)


def build_default_intentseal(db: Database | None = None) -> IntentSeal:
    """A permissive-by-default IntentSeal for callers that don't inject one.

    Uses a per-process ephemeral key and (if a db is given) the persistent
    nonce store. Real deployments inject a Keychain-keyed instance via app.py.
    """
    import secrets

    store = DbSealStore(db) if db is not None else InMemorySealStore()
    return IntentSeal(key=secrets.token_bytes(32), config=PolicyConfig.full(), seal_store=store)


class ActionGate:
    # Seals must outlive a human deciding on the confirmation card, but stay
    # short so a stale approval cannot be executed much later. The seal is
    # consumed the instant approval lands, so this is only the card lifetime.
    _SEAL_TTL_S = 600.0

    def __init__(
        self,
        db: Database,
        registry: ToolRegistry,
        permission_checker: PermissionChecker,
        request_approval: ApprovalCallback,
        intentseal: IntentSeal | None = None,
        effects: EffectAdapterRegistry | None = None,
    ) -> None:
        self._db = db
        self._registry = registry
        self._has_permission = permission_checker
        self._request_approval = request_approval
        if effects is None:
            effects = EffectAdapterRegistry()
            register_builtin_adapters(effects)
        self._effects = effects
        self._seal = intentseal or build_default_intentseal(db)

    # -- proposal assembly --------------------------------------------------- #
    def _predict(self, spec, params) -> Any:
        args = params.model_dump(mode="json")
        if spec.effect_adapter is not None:
            effect = spec.effect_adapter(args)
        else:
            effect = self._effects.predict(spec.name, args, spec.risk is RiskLevel.WRITE)
        effectful = effect.action_class is not ActionClass.READ
        if effectful and (spec.irreversible or not (spec.rollback_supported or spec.stager)):
            effect = replace(
                effect,
                reversible=False,
                effect_kinds=effect.effect_kinds | {EffectKind.IRREVERSIBLE},
            )
        if spec.data_classes:
            classes = frozenset(DataClass(value) for value in spec.data_classes)
            effect = replace(effect, data_out=effect.data_out | classes)
        return effect

    def _build_proposal(self, spec, params, turn: TurnContext) -> Proposal:
        args = params.model_dump(mode="json")
        effect = self._predict(spec, params)
        # Bind each argument to its provenance. ``bind_arg`` downgrades any
        # value whose bytes match untrusted evidence recorded this turn, so a
        # laundered instruction cannot masquerade as a user literal.
        bound = tuple(
            turn.evidence.bind_arg(name, value, literal=True) for name, value in args.items()
        )
        evidence_by_id = {}
        for value in _leaf_values(args):
            ref = turn.evidence.match(value)
            if ref is not None:
                evidence_by_id[ref.ref_id] = ref
        return Proposal(
            tool=ToolIdentity(
                name=spec.name,
                manifest_hash=spec.manifest_hash,
                namespace=spec.identity_namespace,
                publisher=spec.publisher,
                server=spec.server_identity,
            ),
            args=args,
            effect=effect,
            bound_args=bound,
            evidence=tuple(evidence_by_id.values()),
        )

    def _redact_params(self, spec, params, redact_fields):
        """Mask the named string fields, then re-validate against the schema.

        Masking (rather than dropping) keeps the call well-formed while ensuring
        the protected value never reaches the sink. Re-validation raises
        ``ToolValidationError`` if the masked call is no longer valid, which the
        caller turns into a fail-closed DENY.
        """
        data = params.model_dump(mode="json")
        for name in redact_fields:
            if name in data and isinstance(data[name], str):
                data[name] = "[redacted by IntentSeal]"
        return self._registry.validate_args(spec.name, data)

    def _approval_request(
        self,
        action_id,
        spec,
        params,
        proposal,
        auth,
        *,
        turn: TurnContext,
        intent_confirmation: bool = False,
        semantic_diff: str = "",
    ) -> ApprovalRequest:
        eff = proposal.effect
        active_capsule = turn.capsule
        return ApprovalRequest(
            action_id=action_id,
            tool=spec.name,
            args=params.model_dump(mode="json"),
            preview=spec.render_preview(params),
            editable=True,
            decision=str(auth.decision),
            reasons=auth.policy.reasons,
            escalation=auth.policy.escalation,
            canonical_target=eff.target.canonical_id,
            audience=tuple(eff.audience),
            data_out=tuple(str(c) for c in eff.data_out),
            reversible=eff.reversible,
            redact_fields=auth.policy.redact_fields,
            intent_confirmation=intent_confirmation,
            intent_goal=active_capsule.goal if active_capsule is not None else "",
            principal=active_capsule.principal.user_id if active_capsule is not None else "",
            account=active_capsule.principal.account if active_capsule is not None else "",
            semantic_diff=semantic_diff,
            provenance=tuple(
                f"{ref.origin}:{ref.source or ref.field_path or ref.ref_id}"
                for ref in proposal.evidence
            ),
        )

    async def _refresh_proposal(self, spec, params, turn: TurnContext) -> Proposal:
        proposal = self._build_proposal(spec, params, turn)
        state = await _probe_state(spec, params, proposal.effect)
        effect = proposal.effect
        if effect.pre_state_hash and state != effect.pre_state_hash:
            effect = replace(effect, flags=effect.flags | {"manifest_drift"})
        return replace(proposal, effect=effect.with_pre_state(state))

    async def _prepare(self, spec, params) -> StagedAction | ToolResult | None:
        if spec.stager is None or not self._seal.config.staging:
            return None
        return await spec.stager(params)

    def _safe_args(self, proposal: Proposal) -> dict[str, Any]:
        sensitive = {
            arg.name
            for arg in proposal.bound_args
            if arg.data_classes
            & {
                DataClass.SECRET,
                DataClass.CANARY,
                DataClass.PII,
                DataClass.PRIVATE_DOC,
                DataClass.SPATIAL,
                DataClass.HEALTH,
                DataClass.CLIPBOARD,
            }
        }
        safe: dict[str, Any] = {}
        for name, value in proposal.args.items():
            if name in sensitive:
                safe[name] = "[redacted by IntentSeal]"
            elif isinstance(value, str):
                safe[name] = f"[value-hash:{stable_hash(value)[:16]}]"
            elif isinstance(value, (dict, list, tuple)):
                safe[name] = f"[value-hash:{stable_hash(value)[:16]}]"
            else:
                safe[name] = value
        return safe

    @staticmethod
    def _audit_preview(proposal: Proposal) -> str:
        return (
            f"{proposal.tool.name}:{proposal.effect.action_class} "
            f"target_hash={stable_hash(proposal.effect.target.key())}"
        )

    def _persist_outcome(
        self,
        proposal: Proposal,
        auth,
        outcome: str,
        *,
        action_id: int | None = None,
        seal_nonce: str = "",
        semantic_diff: str = "",
        undo: dict[str, Any] | None = None,
    ) -> None:
        payload = {
            "tool": proposal.tool.name,
            "tool_identity": proposal.tool.key(),
            "action": str(proposal.effect.action_class),
            "decision": str(auth.decision),
            "reasons": list(auth.policy.reasons),
            "outcome": outcome,
            "action_id": action_id,
            "args": self._safe_args(proposal),
            "target": {
                "kind": proposal.effect.target.kind,
                "hash": stable_hash(proposal.effect.target.canonical_id),
            },
            "provenance": [
                {
                    "origin": str(ref.origin),
                    "source_hash": stable_hash(ref.source) if ref.source else "",
                    "field": ref.field_path,
                    "classes": sorted(str(value) for value in ref.data_classes),
                }
                for ref in proposal.evidence
            ],
            "seal_nonce": seal_nonce,
            "semantic_diff_hash": stable_hash(semantic_diff) if semantic_diff else "",
            "undo": (
                {
                    "kind": (undo or {}).get("kind", ""),
                    "reversible": bool((undo or {}).get("reversible")),
                    "detail_hash": stable_hash((undo or {}).get("detail", {})),
                }
                if undo
                else {}
            ),
        }
        self._db.append_intentseal_audit(payload)

    def _blocked_result(
        self,
        spec,
        params,
        proposal: Proposal,
        auth,
        conversation_id: int | None,
        *,
        action_id: int | None = None,
    ) -> ToolResult:
        reason = auth.policy.reasons[0] if auth.policy.reasons else str(auth.decision)
        status = f"denied_{str(auth.decision).lower()}"
        if action_id is None:
            action_id = self._db.record_action(
                spec.name,
                self._safe_args(proposal),
                spec.risk.value,
                status,
                self._audit_preview(proposal),
                conversation_id,
            )
        else:
            self._db.update_action(action_id, status, reason)
        self._db.set_action_decision(action_id, str(auth.decision))
        self._persist_outcome(proposal, auth, "blocked", action_id=action_id)
        log.info("IntentSeal %s for %s: %s", auth.decision, spec.name, reason)
        return ToolResult(
            ok=False,
            error=(
                f"IntentSeal blocked this action ({auth.decision}): {reason}. "
                "Do not retry it; report this to the user."
            ),
        )

    async def confirm_cloud_egress(
        self,
        *,
        trusted_text: str,
        untrusted_content: list[tuple[str, object]] | None = None,
        history_messages: list[tuple[str, object]] | None = None,
        resource: str,
        principal=None,
        conversation_id: int | None = None,
    ) -> bool:
        """Authorize an opt-in local-to-cloud fallback before provider I/O.

        This does not execute a tool handler. It uses the same provenance
        policy, confirmation UI, seal verification, and persistent audit as
        tool effects, then returns a one-shot consent result to app.py.
        History that will leave the machine is included in the provenance
        set so consent covers the full outbound payload.
        """
        principal = principal or TurnContext().principal
        turn = TurnContext(principal=principal)
        refs = list(
            turn.evidence.record_fields(
                Origin.USER,
                trusted_text,
                source=f"turn:{turn.turn_id}:direct-user",
                field_path="request",
            )
        )
        for name, value in untrusted_content or []:
            refs.extend(
                turn.evidence.record_fields(
                    Origin.FILE,
                    value,
                    {DataClass.PRIVATE_DOC},
                    source=f"attachment:{name}",
                    field_path="content",
                )
            )
        for index, (role, value) in enumerate(history_messages or []):
            refs.extend(
                turn.evidence.record_fields(
                    Origin.HISTORY,
                    value,
                    {DataClass.PRIVATE_DOC},
                    source=f"history:{index}:{role}",
                    field_path="content",
                )
            )
        classes = frozenset(
            data_class for ref in refs for data_class in ref.data_classes
        )
        turn.capsule = IntentCapsule(
            goal="Use cloud fallback for this turn",
            principal=principal,
            allowed_action_classes=frozenset({ActionClass.EGRESS}),
            allowed_resources=(resource,),
            protected_data_classes=frozenset({DataClass.SECRET, DataClass.CANARY}),
            max_quantity=1,
        ).freeze()
        effect = PredictedEffect(
            action_class=ActionClass.EGRESS,
            target=CanonicalTarget("cloud_provider", resource),
            effect_kinds=frozenset({EffectKind.EGRESS, EffectKind.IRREVERSIBLE}),
            data_out=classes,
            reversible=False,
            egress=True,
            description=f"send this turn to {resource}",
        )
        bound_args = tuple(
            turn.evidence.bind_arg(
                f"content_{index}",
                trusted_text if index == 0 else ref.preview,
                declared_ref=ref.ref_id,
            )
            for index, ref in enumerate(refs)
        )
        proposal = Proposal(
            tool=ToolIdentity(
                "internal_cloud_fallback",
                manifest_hash=stable_hash("cloud-egress-consent-v1"),
                namespace="hearth.internal",
            ),
            args={
                "resource": resource,
                "content_hash": stable_hash(
                    [
                        trusted_text,
                        [
                            (name, stable_hash(value))
                            for name, value in untrusted_content or []
                        ],
                    ]
                ),
            },
            effect=effect.with_pre_state(stable_hash(resource)),
            bound_args=bound_args,
            evidence=tuple(refs),
        )
        auth = self._seal.authorize(
            proposal, turn, approval_state="pending", ttl_s=self._SEAL_TTL_S
        )
        if auth.blocked:
            self._persist_outcome(proposal, auth, "blocked")
            return False
        action_id = self._db.record_action(
            proposal.tool.name,
            self._safe_args(proposal),
            RiskLevel.WRITE.value,
            "pending",
            self._audit_preview(proposal),
            conversation_id,
        )
        request = ApprovalRequest(
            action_id=action_id,
            tool="cloud_fallback",
            args={"provider": resource},
            preview=(
                f"Send this turn to cloud provider {resource}.\n"
                "Local/attachment content may leave this Mac for this turn only."
            ),
            editable=False,
            decision=str(auth.decision),
            reasons=auth.policy.reasons,
            escalation="local model unavailable; data-egress consent required",
            canonical_target=resource,
            data_out=tuple(sorted(str(value) for value in classes)),
            reversible=False,
            intent_confirmation=True,
            intent_goal=turn.capsule.goal,
            principal=principal.user_id,
            account=principal.account,
            provenance=tuple(
                f"{ref.origin}:{ref.source or ref.field_path}" for ref in refs
            ),
        )
        response = await self._request_approval(request)
        if not response.approved:
            self._db.update_action(action_id, "rejected")
            self._persist_outcome(proposal, auth, "blocked", action_id=action_id)
            return False
        auth = self._seal.authorize(
            proposal,
            turn,
            approval_id=str(action_id),
            approval_state="approved",
            ttl_s=self._SEAL_TTL_S,
        )
        ok, why = self._seal.verify(
            auth.seal,
            proposal,
            turn,
            current_pre_state=proposal.effect.pre_state_hash,
            policy=auth.policy,
            approval_id=str(action_id),
            approval_state="approved",
        )
        if not ok:
            self._db.update_action(action_id, "failed", why)
            self._persist_outcome(proposal, auth, "blocked", action_id=action_id)
            return False
        self._db.update_action(action_id, "completed", "cloud egress consented")
        self._persist_outcome(
            proposal,
            auth,
            "cloud_egress_authorized",
            action_id=action_id,
            seal_nonce=auth.seal.nonce if auth.seal else "",
        )
        return True

    async def execute(
        self,
        tool_name: str,
        raw_args: dict[str, Any],
        conversation_id: int | None = None,
        turn: TurnContext | None = None,
    ) -> ToolResult:
        """Validate, authorize via IntentSeal, then execute. The only entry point."""
        explicit_turn = turn is not None
        turn = turn or TurnContext()
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

        proposal = await self._refresh_proposal(spec, params, turn)
        effectful = proposal.effect.action_class is not ActionClass.READ

        # Backward compatibility for old direct callers: production AgentLoop
        # always supplies an explicit TurnContext, while legacy tests/plugins
        # that omit it receive a narrowly synthesized exact-effect capsule.
        if not explicit_turn and effectful and turn.capsule is None:
            turn.capsule = _capsule_for_effect(turn, "legacy direct tool call", proposal).freeze()
            proposal = await self._refresh_proposal(spec, params, turn)

        approved = False
        approval_state = ""
        action_id: int | None = None
        prepared: StagedAction | None = None
        semantic_diff = ""

        # An effectful draft has exactly one way to gain authority: the normal
        # confirmation card binds its exact canonical target/audience and then
        # freezes it. Missing intent and out-of-draft action classes never get
        # a misleading card.
        draft = turn.capsule
        if effectful and draft is not None and not draft.frozen:
            if (
                draft.allowed_action_classes is not None
                and proposal.effect.action_class not in draft.allowed_action_classes
            ):
                auth = self._seal.authorize(proposal, turn, ttl_s=self._SEAL_TTL_S)
                return self._blocked_result(
                    spec, params, proposal, auth, conversation_id
                )

            # Do not let attachment/tool evidence widen a draft recipient or
            # resource. Trusted ambiguity can be resolved by the card; an
            # untrusted scope expansion is quarantined before any prompt.
            if any(not ref.trusted for ref in proposal.evidence):
                original = turn.capsule
                turn.capsule = original.freeze()
                precheck = self._seal.authorize(proposal, turn, ttl_s=self._SEAL_TTL_S)
                turn.capsule = original
                if precheck.blocked:
                    return self._blocked_result(
                        spec, params, proposal, precheck, conversation_id
                    )

            candidate = _capsule_for_effect(turn, draft.goal, proposal)
            turn.capsule = candidate.freeze()
            auth = self._seal.authorize(
                proposal,
                turn,
                approval_state="pending",
                ttl_s=self._SEAL_TTL_S,
            )
            if auth.blocked:
                turn.capsule = draft
                return self._blocked_result(spec, params, proposal, auth, conversation_id)

            staged = await self._prepare(spec, params)
            if isinstance(staged, ToolResult):
                turn.capsule = draft
                return staged
            prepared = staged
            semantic_diff = prepared.semantic_diff if prepared is not None else ""
            action_id = self._db.record_action(
                tool_name,
                self._safe_args(proposal),
                spec.risk.value,
                "pending_intent",
                self._audit_preview(proposal),
                conversation_id,
            )
            self._db.set_action_decision(action_id, str(auth.decision))
            try:
                response = await self._request_approval(
                    self._approval_request(
                        action_id,
                        spec,
                        params,
                        proposal,
                        auth,
                        turn=turn,
                        intent_confirmation=True,
                        semantic_diff=semantic_diff,
                    )
                )
            except asyncio.CancelledError:
                if prepared is not None:
                    await prepared.discard()
                turn.capsule = draft
                self._db.update_action(action_id, "cancelled")
                raise
            if not response.approved:
                if prepared is not None:
                    await prepared.discard()
                turn.capsule = draft
                self._db.update_action(action_id, "rejected")
                self._persist_outcome(
                    proposal, auth, "blocked", action_id=action_id, semantic_diff=semantic_diff
                )
                return ToolResult(ok=False, error="The user rejected this action. Do not retry it.")

            if response.edited_args is not None:
                if prepared is not None:
                    await prepared.discard()
                try:
                    params = self._registry.validate_args(tool_name, response.edited_args)
                except ToolValidationError as exc:
                    turn.capsule = draft
                    self._db.update_action(action_id, "failed", f"invalid edit: {exc}")
                    return ToolResult(
                        ok=False,
                        error=f"The edited arguments were invalid, so nothing ran: {exc}",
                    )
                proposal = await self._refresh_proposal(spec, params, turn)
                turn.capsule = _capsule_for_effect(turn, draft.goal, proposal).freeze()
                staged = await self._prepare(spec, params)
                if isinstance(staged, ToolResult):
                    turn.capsule = draft
                    self._db.update_action(action_id, "failed", staged.error)
                    return staged
                prepared = staged
                semantic_diff = prepared.semantic_diff if prepared is not None else ""

            approved = True
            approval_state = "approved"
            proposal = _rebind_approved_manifest(proposal)
            auth = self._seal.authorize(
                proposal,
                turn,
                approval_id=str(action_id),
                approval_state=approval_state,
                ttl_s=self._SEAL_TTL_S,
            )
            if auth.blocked:
                if prepared is not None:
                    await prepared.discard()
                turn.capsule = draft
                return self._blocked_result(
                    spec, params, proposal, auth, conversation_id, action_id=action_id
                )
        else:
            auth = self._seal.authorize(proposal, turn, ttl_s=self._SEAL_TTL_S)

        # Hard block: missing/frozen intent, provenance, canonicalization, and
        # policy denials stop before staging or a confirmation card.
        if auth.blocked:
            return self._blocked_result(spec, params, proposal, auth, conversation_id)

        needs_approval = (
            spec.risk is RiskLevel.WRITE
            or auth.requires_approval
            or auth.decision is Decision.REDACT
        )

        if needs_approval and not approved:
            staged = await self._prepare(spec, params)
            if isinstance(staged, ToolResult):
                return staged
            prepared = staged
            semantic_diff = prepared.semantic_diff if prepared is not None else ""
            action_id = self._db.record_action(
                tool_name,
                self._safe_args(proposal),
                spec.risk.value,
                "pending",
                self._audit_preview(proposal),
                conversation_id,
            )
            self._db.set_action_decision(action_id, str(auth.decision))
            try:
                response = await self._request_approval(
                    self._approval_request(
                        action_id,
                        spec,
                        params,
                        proposal,
                        auth,
                        turn=turn,
                        semantic_diff=semantic_diff,
                    )
                )
            except asyncio.CancelledError:
                if prepared is not None:
                    await prepared.discard()
                self._db.update_action(action_id, "cancelled")
                raise
            if not response.approved:
                if prepared is not None:
                    await prepared.discard()
                self._db.update_action(action_id, "rejected")
                log.info("Action %s (%s) rejected by user", action_id, tool_name)
                self._persist_outcome(
                    proposal, auth, "blocked", action_id=action_id, semantic_diff=semantic_diff
                )
                return ToolResult(ok=False, error="The user rejected this action. Do not retry it.")
            if response.edited_args is not None:
                if prepared is not None:
                    await prepared.discard()
                try:
                    params = self._registry.validate_args(tool_name, response.edited_args)
                except ToolValidationError as exc:
                    self._db.update_action(action_id, "failed", f"invalid edit: {exc}")
                    return ToolResult(
                        ok=False,
                        error=f"The edited arguments were invalid, so nothing ran: {exc}",
                    )
                proposal = await self._refresh_proposal(spec, params, turn)
                staged = await self._prepare(spec, params)
                if isinstance(staged, ToolResult):
                    self._db.update_action(action_id, "failed", staged.error)
                    return staged
                prepared = staged
                semantic_diff = prepared.semantic_diff if prepared is not None else ""
            approved = True
            approval_state = "approved"
            # Approval and every edit invalidate any pre-card mandate. Mint a
            # fresh seal bound to the final approval id/state and exact args.
            proposal = _rebind_approved_manifest(proposal)
            auth = self._seal.authorize(
                proposal,
                turn,
                approval_id=str(action_id),
                approval_state=approval_state,
                ttl_s=self._SEAL_TTL_S,
            )
            if auth.blocked:
                if prepared is not None:
                    await prepared.discard()
                return self._blocked_result(
                    spec, params, proposal, auth, conversation_id, action_id=action_id
                )
        else:
            if action_id is None:
                action_id = self._db.record_action(
                    tool_name,
                    self._safe_args(proposal),
                    spec.risk.value,
                    "auto_approved",
                    self._audit_preview(proposal),
                    conversation_id,
                )
                self._db.set_action_decision(action_id, str(auth.decision))

        if auth.policy.redact_fields:
            if prepared is not None:
                await prepared.discard()
            try:
                params = self._redact_params(spec, params, auth.policy.redact_fields)
            except ToolValidationError as exc:
                self._db.update_action(action_id, "denied_redact", str(exc))
                self._db.set_action_decision(action_id, "DENY")
                return ToolResult(
                    ok=False,
                    error=("IntentSeal could not safely redact the protected field(s) "
                           f"{list(auth.policy.redact_fields)}, so nothing ran."),
                )
            proposal = await self._refresh_proposal(spec, params, turn)
            staged = await self._prepare(spec, params)
            if isinstance(staged, ToolResult):
                self._db.update_action(action_id, "failed", staged.error)
                return staged
            prepared = staged
            semantic_diff = prepared.semantic_diff if prepared is not None else ""
            if approved:
                proposal = _rebind_approved_manifest(proposal)
            auth = self._seal.authorize(
                proposal,
                turn,
                approval_id=str(action_id) if approved else "",
                approval_state=approval_state,
                ttl_s=self._SEAL_TTL_S,
            )
            if auth.blocked:
                if prepared is not None:
                    await prepared.discard()
                return self._blocked_result(
                    spec, params, proposal, auth, conversation_id, action_id=action_id
                )

        # Re-run validation, canonicalization, effect prediction, manifest/state
        # probes immediately before execution. The seal is verified against this
        # fresh proposal, not the stale object used to render the card.
        proposal = await self._refresh_proposal(spec, params, turn)
        if approved:
            proposal = _rebind_approved_manifest(proposal)
        idempotency_key = ""
        if effectful and spec.idempotency:
            idempotency_key = stable_hash(
                {
                    "tool": proposal.tool.key(),
                    "args": proposal.args_hash(),
                    "principal": turn.principal.key(),
                    "intent": turn.capsule.intent_hash() if turn.capsule else "",
                }
            )
            if not self._db.reserve_idempotency(idempotency_key, action_id):
                if prepared is not None:
                    await prepared.discard()
                self._db.update_action(action_id, "duplicate_suppressed")
                self._persist_outcome(
                    proposal,
                    auth,
                    "duplicate_suppressed",
                    action_id=action_id,
                    semantic_diff=semantic_diff,
                )
                return ToolResult(
                    ok=False,
                    error="IntentSeal suppressed this duplicate effect; it was already attempted.",
                )

        ok, why = self._seal.verify(
            auth.seal,
            proposal,
            turn,
            current_pre_state=proposal.effect.pre_state_hash,
            policy=auth.policy,
            approval_id=str(action_id) if approved else "",
            approval_state=approval_state,
        )
        if not ok:
            if prepared is not None:
                await prepared.discard()
            if idempotency_key:
                self._db.release_idempotency(idempotency_key)
            self._db.update_action(action_id, "failed", f"seal verification failed: {why}")
            self._seal.record_outcome(proposal, auth.policy, "", "blocked")
            self._persist_outcome(proposal, auth, "blocked", action_id=action_id)
            return ToolResult(
                ok=False,
                error=f"IntentSeal could not verify a one-use mandate for this action ({why}).",
            )

        local_resources = frozenset(
            turn.capsule.allowed_resources
            if turn.capsule is not None and turn.capsule.allowed_resources is not None
            else ()
        )
        local_token = set_authorized_local_resources(local_resources)
        try:
            operation = prepared.commit() if prepared is not None else spec.handler(params)
            result = await asyncio.wait_for(operation, timeout=spec.timeout_s)
        except TimeoutError:
            self._db.update_action(action_id, "failed", f"timeout after {spec.timeout_s}s")
            if idempotency_key:
                self._db.finish_idempotency(idempotency_key, "failed")
            return ToolResult(ok=False, error=f"{tool_name} timed out after {spec.timeout_s}s")
        except asyncio.CancelledError:
            self._db.update_action(action_id, "cancelled")
            if idempotency_key:
                self._db.finish_idempotency(idempotency_key, "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 — surface tool bugs as tool errors
            log.exception("Tool %s raised", tool_name)
            self._db.update_action(action_id, "failed", str(exc))
            if idempotency_key:
                self._db.finish_idempotency(idempotency_key, "failed")
            return ToolResult(ok=False, error=f"{tool_name} failed: {exc}")
        finally:
            reset_authorized_local_resources(local_token)

        if effectful:
            turn.spend(1)
        seal_nonce = auth.seal.nonce if auth.seal is not None else ""
        outcome = "executed"
        if result.ok and spec.postcondition_supported and self._seal.config.postconditions:
            post_state = await _probe_state(spec, params, proposal.effect)
            expected_state = (
                spec.expected_post_state(params)
                if spec.expected_post_state is not None
                else None
            )
            if expected_state is not None:
                from ..assurance.audit import PostconditionResult

                postcondition = PostconditionResult(
                    post_state == expected_state,
                    (
                        "postcondition satisfied"
                        if post_state == expected_state
                        else "observed state does not match the staged expected state"
                    ),
                    expected_state,
                    post_state,
                )
            else:
                postcondition = self._seal.check_postcondition(
                    expected_change=effectful,
                    pre_state_hash=proposal.effect.pre_state_hash,
                    post_state_hash=post_state,
                )
            if not postcondition.ok:
                outcome = "postcondition_failed"
                if prepared is not None and prepared.undo is not None:
                    await prepared.undo()
                    outcome = "rolled_back"
                self._seal.record_outcome(proposal, auth.policy, seal_nonce, outcome)
                self._persist_outcome(
                    proposal,
                    auth,
                    outcome,
                    action_id=action_id,
                    seal_nonce=seal_nonce,
                    semantic_diff=semantic_diff,
                    undo=prepared.undo_metadata() if prepared is not None else {},
                )
                self._db.update_action(action_id, "failed", postcondition.reason)
                if idempotency_key:
                    self._db.finish_idempotency(idempotency_key, "postcondition_failed")
                return ToolResult(
                    ok=False,
                    error=f"IntentSeal postcondition failed: {postcondition.reason}",
                )

        self._seal.record_outcome(proposal, auth.policy, seal_nonce, outcome)
        status = "completed" if result.ok else "failed"
        summary = result.error if not result.ok else _summarize(result)
        self._db.update_action(action_id, status, summary)
        if idempotency_key:
            self._db.finish_idempotency(idempotency_key, status)
        self._persist_outcome(
            proposal,
            auth,
            outcome if result.ok else "failed",
            action_id=action_id,
            seal_nonce=seal_nonce,
            semantic_diff=semantic_diff,
            undo=prepared.undo_metadata() if prepared is not None else {},
        )
        return result


def _current_pre_state(effect: PredictedEffect) -> str:
    """Recompute a deterministic state/identity binding for supported targets."""
    target = effect.target
    if target.kind == "file" and target.canonical_id:
        return canonical_file(target.canonical_id).attributes.get("content_hash", "")
    return effect.pre_state_hash or target.key()


async def _probe_state(spec, params, effect: PredictedEffect) -> str:
    if spec.state_probe is not None:
        observed = spec.state_probe(params)
        if inspect.isawaitable(observed):
            observed = await observed
        return str(observed)
    return _current_pre_state(effect)


def _leaf_values(value: object):
    if isinstance(value, dict):
        for child in value.values():
            yield from _leaf_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _leaf_values(child)
    else:
        yield value


def _capsule_for_effect(turn: TurnContext, goal: str, proposal: Proposal) -> IntentCapsule:
    existing = turn.capsule
    target = proposal.effect.target.canonical_id
    resources = (target,) if target else ()
    recipients = tuple(proposal.effect.audience)
    if existing is None:
        return IntentCapsule(
            goal=goal,
            principal=turn.principal,
            allowed_action_classes=frozenset({proposal.effect.action_class}),
            allowed_resources=resources,
            allowed_recipients=recipients,
            max_quantity=max(1, proposal.effect.quantity),
        )
    return replace(
        existing,
        principal=turn.principal,
        allowed_resources=resources,
        allowed_recipients=recipients,
        max_quantity=max(existing.max_quantity, proposal.effect.quantity),
        frozen=False,
    )


def _rebind_approved_manifest(proposal: Proposal) -> Proposal:
    """Bind a drift reapproval to the manifest observed on the approval path."""
    if "manifest_drift" not in proposal.effect.flags:
        return proposal
    observed = proposal.effect.pre_state_hash
    tool = replace(proposal.tool, manifest_hash=observed)
    attributes = dict(proposal.effect.target.attributes)
    attributes["manifest_hash"] = observed
    target = replace(proposal.effect.target, attributes=attributes)
    effect = replace(
        proposal.effect,
        target=target,
        flags=proposal.effect.flags - {"manifest_drift"},
    )
    return replace(proposal, tool=tool, effect=effect)


def _summarize(result: ToolResult, limit: int = 200) -> str:
    text = result.for_model()
    return text[:limit] + ("…" if len(text) > limit else "")
