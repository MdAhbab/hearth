"""The deterministic policy engine.

Given a :class:`Proposal`, a frozen :class:`IntentCapsule`, and a
:class:`PolicyConfig`, it returns exactly one :class:`Decision` with reasons.
It is a pure function of its inputs — no model call, no I/O, no randomness — so
the same proposal always yields the same decision. That determinism is what
makes it a reference monitor rather than another probabilistic filter.

The checks, in order of severity, cover the eight predicates from the research
canvas: intent inclusion, provenance-to-sink flow, least privilege, account/
audience binding, current state, budgets, tool identity, and reversibility. A
:class:`PolicyConfig` can disable individual capabilities for ablation studies;
disabling one only ever *removes* safety, never adds it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .canonical import LOCAL_ZONES
from .evidence import EvidenceStore
from .types import (
    BLOCKING,
    SENSITIVE_DATA,
    ActionClass,
    DataClass,
    Decision,
    EffectKind,
    IntentCapsule,
    PolicyResult,
    PredictedEffect,
    Proposal,
)

# Decision precedence, most restrictive first. The final decision is the
# highest-precedence one any check produced (ALLOW if none fired).
_PRECEDENCE = [Decision.DENY, Decision.QUARANTINE, Decision.ASK, Decision.REDACT, Decision.ALLOW]

# Data leaving the machine that is always fatal, never redactable.
_NEVER_LEAVES = frozenset({DataClass.SECRET, DataClass.CANARY})
# Data that may leave only after being stripped from the payload.
_REDACTABLE = frozenset(
    {DataClass.PII, DataClass.PRIVATE_DOC, DataClass.SPATIAL, DataClass.HEALTH, DataClass.CLIPBOARD}
)


@dataclass(frozen=True)
class PolicyConfig:
    """Which capabilities are active. Full IntentSeal has all flags on.

    ``mode`` selects a whole evaluation strategy:
      * ``"intentseal"`` — run the deterministic checks below, honoring flags.
      * ``"baseline"``   — legacy Hearth: decision from action class only
                           (read → ALLOW, effectful → ASK). No IntentSeal.
      * ``"detector"``   — baseline decisions plus an advisory injection score
                           that never changes the decision.
    """

    mode: str = "intentseal"
    intent_binding: bool = True
    provenance: bool = True
    canonicalization: bool = True
    one_use_seal: bool = True
    staging: bool = True
    postconditions: bool = True
    domain_rules: bool = True

    @classmethod
    def full(cls) -> PolicyConfig:
        return cls(mode="intentseal")

    @classmethod
    def baseline(cls) -> PolicyConfig:
        return cls(mode="baseline")

    @classmethod
    def detector(cls) -> PolicyConfig:
        return cls(mode="detector")

    @classmethod
    def ablation(cls, capability: str) -> PolicyConfig:
        """Full IntentSeal with exactly one capability removed."""
        from dataclasses import replace

        return replace(cls.full(), **{capability: False})


def _effectful(effect: PredictedEffect) -> bool:
    return effect.action_class is not ActionClass.READ


def _reaching_classes(proposal: Proposal) -> frozenset[DataClass]:
    """Data classes that would reach the sink for an egress/send effect."""
    classes: set[DataClass] = set()
    for arg in proposal.bound_args:
        classes |= set(arg.data_classes)
    classes |= set(proposal.effect.data_out)
    return frozenset(classes)


def _untrusted_triggered(proposal: Proposal) -> bool:
    return any(not ev.trusted for ev in proposal.evidence)


def _resource_allowed(capsule: IntentCapsule | None, target) -> bool:
    if capsule is None or capsule.allowed_resources is None:
        return True
    if target.kind == "none":
        return True
    cid = target.canonical_id
    # Exact identity only. Prefix matching silently widens a frozen mandate to
    # sibling resources (for example URL/path aliases) and is rejected here.
    return any(cid == pat for pat in capsule.allowed_resources)


def _action_allowed(capsule: IntentCapsule | None, effect: PredictedEffect) -> bool:
    if capsule is None or capsule.allowed_action_classes is None:
        return True
    return effect.action_class in capsule.allowed_action_classes


def _intent_includes(capsule: IntentCapsule | None, effect: PredictedEffect) -> bool:
    if not (_action_allowed(capsule, effect) and _resource_allowed(capsule, effect.target)):
        return False
    if capsule is None or capsule.allowed_recipients is None or not effect.audience:
        return True
    return set(effect.audience) <= set(capsule.allowed_recipients)


def _explicitly_allows(capsule: IntentCapsule | None, target) -> bool:
    """True only if the capsule *names* this resource. Unlike ``_resource_allowed``,
    an unconstrained (None) resource set is NOT an explicit grant — reaching a
    loopback/private peer requires the user to have named that exact resource."""
    if capsule is None or capsule.allowed_resources is None:
        return False
    cid = target.canonical_id
    return cid in capsule.allowed_resources


def injection_score(proposal: Proposal) -> float:
    """Advisory-only heuristic used by the detector baseline.

    Rough signal that an effect was steered by untrusted content: an effectful
    call whose evidence is untrusted and which lies outside the intent capsule.
    Reported alongside decisions but never allowed to change one.
    """
    score = 0.0
    if _untrusted_triggered(proposal):
        score += 0.5
    if _effectful(proposal.effect):
        score += 0.2
    if proposal.effect.egress:
        score += 0.2
    if proposal.effect.flags:
        score += 0.1
    return min(score, 1.0)


class PolicyEngine:
    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig.full()

    def evaluate(
        self,
        proposal: Proposal,
        capsule: IntentCapsule | None,
        evidence: EvidenceStore | None = None,
        budget_used: int = 0,
    ) -> PolicyResult:
        cfg = self.config
        if cfg.mode in ("baseline", "detector"):
            return self._legacy(proposal)

        effect = proposal.effect
        verdicts: list[tuple[Decision, str]] = []
        redact: set[str] = set()

        # A side effect has no authority without a user-confirmed, frozen
        # turn mandate. ActionGate has one narrow path that can present an
        # unfrozen draft to the user and freeze it; the policy itself never
        # treats a missing/draft capsule as executable authority.
        if _effectful(effect) and (capsule is None or not capsule.frozen):
            verdicts.append((Decision.DENY, "effectful action lacks a frozen intent capsule"))

        # -- 1. Canonicalization / current-state checks --------------------- #
        if cfg.canonicalization:
            self._check_canonical(proposal, capsule, verdicts)

        # -- 2. Provenance-to-sink flow ------------------------------------- #
        if cfg.provenance:
            self._check_provenance(proposal, capsule, verdicts, redact)

        # -- 3. Intent inclusion / least privilege / audience -------------- #
        if cfg.intent_binding and capsule is not None and capsule.frozen:
            self._check_intent(proposal, capsule, verdicts, budget_used)

        # -- 4. Domain rules: reversibility, physical, bulk, recurring ----- #
        if cfg.domain_rules:
            self._check_domain(effect, verdicts)

        decision = self._combine(verdicts)
        reasons = tuple(reason for _, reason in verdicts) or ("within mandate; no sensitive flow",)
        escalation = "; ".join(r for d, r in verdicts if d is Decision.ASK)
        return PolicyResult(
            decision=decision,
            reasons=reasons,
            redact_fields=tuple(sorted(redact)),
            requires_approval=decision is Decision.ASK,
            escalation=escalation,
        )

    # ------------------------------------------------------------------ #
    def _legacy(self, proposal: Proposal) -> PolicyResult:
        """Current-Hearth semantics: read auto-runs, everything else asks."""
        effect = proposal.effect
        if not _effectful(effect):
            return PolicyResult(decision=Decision.ALLOW, reasons=("read (auto-approved)",))
        return PolicyResult(
            decision=Decision.ASK,
            reasons=("effectful action requires user confirmation",),
            requires_approval=True,
            escalation="write action",
        )

    def _check_canonical(self, proposal, capsule, verdicts) -> None:
        effect = proposal.effect
        target = effect.target
        # Network egress reaching a local/private zone the user did not name.
        if effect.egress and target.kind in ("url", "device"):
            zone = target.attributes.get("zone")
            if zone in LOCAL_ZONES and not _explicitly_allows(capsule, target):
                verdicts.append(
                    (Decision.DENY, f"egress to {zone} host {target.attributes.get('host', '')} "
                     "was not user-authorized")
                )
        if "redirect_to_local" in effect.flags:
            verdicts.append((Decision.DENY, "redirect resolves to a private/loopback peer"))
        if "outside_root" in effect.flags:
            verdicts.append((Decision.DENY, "target resolves outside the approved roots"))
        if "identity_conflict" in effect.flags:
            verdicts.append(
                (Decision.DENY, "target identity conflicts with an existing trusted resource")
            )
        if "precondition_mismatch" in effect.flags:
            verdicts.append((Decision.ASK, "resource changed since it was previewed"))
        if "manifest_drift" in effect.flags:
            verdicts.append(
                (
                    Decision.ASK,
                    "tool manifest changed; re-approve this exact schema and argument set",
                )
            )
        if "unapproved_scheme" in effect.flags or target.attributes.get("custom_scheme"):
            verdicts.append((Decision.ASK, "URL uses an unapproved scheme/handler"))
        if effect.egress and target.attributes.get("has_credentials"):
            verdicts.append((Decision.ASK, "URL embeds credentials — confirm the destination"))
        if "ambiguous" in effect.flags:
            verdicts.append((Decision.ASK, "ambiguous target/time needs disambiguation"))

    def _check_provenance(self, proposal, capsule, verdicts, redact) -> None:
        effect = proposal.effect
        # Unaudited sensitive arguments fail closed.
        for arg in proposal.bound_args:
            if arg.source_ref == "" and (set(arg.data_classes) & SENSITIVE_DATA):
                verdicts.append((Decision.DENY, f"unaudited sensitive argument '{arg.name}'"))
        # Raw credentials must never be tool arguments.
        if effect.action_class is ActionClass.CREDENTIAL or "raw_secret_arg" in effect.flags:
            verdicts.append((Decision.DENY, "raw credential/token requested as an argument"))
        # Sensitive data crossing to an external sink.
        if effect.egress or effect.action_class is ActionClass.SEND_EXTERNAL:
            reaching = _reaching_classes(proposal)
            fatal = reaching & _NEVER_LEAVES
            # A canary embedded in an already-redactable document field can be
            # removed before the sink. A bare canary/secret remains a hard DENY.
            canary_redact_fields = {
                arg.name
                for arg in proposal.bound_args
                if DataClass.CANARY in arg.data_classes
                and DataClass.PRIVATE_DOC in arg.data_classes
            }
            if DataClass.CANARY in fatal and canary_redact_fields:
                fatal = fatal - {DataClass.CANARY}
                redact.update(canary_redact_fields)
                verdicts.append(
                    (Decision.REDACT, "strip synthetic canary-bearing field before the sink")
                )
            if fatal:
                verdicts.append(
                    (Decision.DENY, f"protected {sorted(fatal)} would leave the machine")
                )
            protected = capsule.protected_data_classes if capsule else SENSITIVE_DATA
            leaking = reaching & _REDACTABLE & protected
            if leaking:
                redact.update(
                    arg.name for arg in proposal.bound_args if set(arg.data_classes) & leaking
                )
                verdicts.append(
                    (Decision.REDACT, f"strip protected {sorted(leaking)} before the sink")
                )
        # Untrusted content trying to become a control instruction. This only
        # applies to *effects* — reading untrusted data is fine; letting it
        # drive a side effect it did not authorize is not.
        if (
            _effectful(effect)
            and _untrusted_triggered(proposal)
            and not _intent_includes(capsule, effect)
        ):
            verdicts.append(
                (Decision.QUARANTINE, "effect proposed by untrusted content, not user intent")
            )

    def _check_intent(self, proposal, capsule, verdicts, budget_used) -> None:
        effect = proposal.effect
        # Intent inclusion, resource, audience, and quantity constrain effects,
        # not reads. A read is governed by permission, provenance, and
        # sensitivity — never by the capsule's allowed *action* set.
        if not _effectful(effect):
            return
        # A stale mandate cannot silently authorize an effect: re-confirm intent
        # once the capsule's TTL has passed (defends against replaying an old
        # goal against a new, unrelated effect).
        if capsule.expired():
            verdicts.append((Decision.ASK, "the intent capsule has expired — re-confirm"))
        # When untrusted content is the trigger, an out-of-mandate effect is
        # already QUARANTINE'd by the provenance check; don't also DENY it, so
        # the more specific "untrusted data drove this" label wins. A *trusted*
        # out-of-mandate action is a plain DENY.
        untrusted = _untrusted_triggered(proposal)
        if not _action_allowed(capsule, effect) and not untrusted:
            verdicts.append(
                (Decision.DENY, f"action class {effect.action_class} is outside the mandate")
            )
        if not _resource_allowed(capsule, effect.target) and not untrusted:
            verdicts.append(
                (Decision.DENY, f"resource {effect.target.canonical_id!r} is outside the mandate")
            )
        # Audience binding: any recipient beyond the mandate requires a decision.
        if effect.audience and capsule.allowed_recipients is not None:
            extra = set(effect.audience) - set(capsule.allowed_recipients)
            if extra:
                verdicts.append((Decision.ASK, f"new recipient(s) not in mandate: {sorted(extra)}"))
        # Quantity / rate budget.
        if effect.quantity > capsule.max_quantity:
            verdicts.append(
                (Decision.ASK, f"affects {effect.quantity} items (budget {capsule.max_quantity})")
            )
        if budget_used >= capsule.max_quantity and _effectful(effect):
            verdicts.append((Decision.ASK, "turn action budget exhausted"))

    def _check_domain(self, effect: PredictedEffect, verdicts) -> None:
        irreversible = not effect.reversible or EffectKind.IRREVERSIBLE in effect.effect_kinds
        if _effectful(effect) and irreversible:
            verdicts.append((Decision.ASK, "irreversible effect — confirm before running"))
        if effect.physical or EffectKind.PHYSICAL in effect.effect_kinds:
            verdicts.append((Decision.ASK, "physical actuation — confirm current intent"))
        if EffectKind.AUDIENCE_EXPANSION in effect.effect_kinds:
            verdicts.append((Decision.ASK, "audience expansion — confirm recipients"))
        if EffectKind.BULK in effect.effect_kinds:
            verdicts.append((Decision.ASK, "bulk operation — confirm the itemized set"))
        if EffectKind.RECURRING in effect.effect_kinds:
            verdicts.append((Decision.ASK, "recurring effect — confirm the series scope"))

    def _combine(self, verdicts) -> Decision:
        present = {d for d, _ in verdicts}
        for decision in _PRECEDENCE:
            if decision in present:
                return decision
        return Decision.ALLOW


__all__ = [
    "PolicyConfig",
    "PolicyEngine",
    "injection_score",
    "BLOCKING",
]
