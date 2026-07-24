"""IntentSeal — the single, non-bypassable reference monitor.

Everything from the other modules is composed here behind one API:

    result = intentseal.authorize(proposal, turn)   # decide + mint seal
    ...
    ok, why = intentseal.verify(seal, proposal, turn)   # consume before effect

An LLM may propose the intent (the capsule) and the effect (the proposal), and
may explain a denial, but only this object issues or verifies a seal. The
monitor is deterministic: given the same proposal, capsule, and config it
always returns the same decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .audit import HashChainAudit, PostconditionResult, check_postcondition
from .evidence import EvidenceStore
from .policy import PolicyConfig, PolicyEngine, injection_score
from .seal import InMemorySealStore, SealIssuer, SealStore
from .types import (
    PERMISSIVE,
    Decision,
    IntentCapsule,
    PolicyResult,
    Principal,
    Proposal,
    Seal,
    new_nonce,
)


@dataclass
class TurnContext:
    """Per-turn scope: principal, frozen intent, provenance, and budget.

    Created fresh each user turn so untrusted evidence, recipients, and budgets
    from an earlier task cannot contaminate the current one.
    """

    turn_id: str = field(default_factory=new_nonce)
    principal: Principal = field(default_factory=lambda: Principal(user_id="local-user"))
    capsule: IntentCapsule | None = None
    evidence: EvidenceStore = field(default_factory=EvidenceStore)
    budget_used: int = 0

    def spend(self, n: int = 1) -> None:
        self.budget_used += n


@dataclass
class AuthorizationResult:
    decision: Decision
    policy: PolicyResult
    seal: Seal | None
    detector_score: float = 0.0

    @property
    def allowed(self) -> bool:
        return self.decision in PERMISSIVE

    @property
    def requires_approval(self) -> bool:
        return self.decision is Decision.ASK

    @property
    def blocked(self) -> bool:
        return not self.allowed


class IntentSeal:
    """The composed monitor. One instance per app (or per benchmark config)."""

    def __init__(
        self,
        *,
        key: bytes,
        config: PolicyConfig | None = None,
        seal_store: SealStore | None = None,
    ) -> None:
        self.config = config or PolicyConfig.full()
        self.policy = PolicyEngine(self.config)
        self.issuer = SealIssuer(key, seal_store or InMemorySealStore())
        self.audit = HashChainAudit()

    # -- decision ----------------------------------------------------------- #
    def authorize(
        self,
        proposal: Proposal,
        turn: TurnContext,
        *,
        approval_id: str = "",
        approval_state: str = "",
        ttl_s: float = 120.0,
    ) -> AuthorizationResult:
        """Decide the proposal and, when permitted, mint a one-use seal."""
        if turn.capsule is not None and turn.capsule.principal.key() != turn.principal.key():
            policy = PolicyResult(
                Decision.DENY,
                ("intent principal/account does not match the active turn",),
            )
        else:
            policy = self.policy.evaluate(
                proposal, turn.capsule, turn.evidence, turn.budget_used
            )
        score = injection_score(proposal) if self.config.mode == "detector" else 0.0

        if policy.decision not in PERMISSIVE:
            self.audit.append(
                tool=proposal.tool.name,
                action=str(proposal.effect.action_class),
                decision=policy.decision,
                reasons=policy.reasons,
                seal_nonce="",
                outcome="blocked",
            )
            return AuthorizationResult(policy.decision, policy, None, score)

        seal: Seal | None = None
        if self.config.mode == "intentseal" and self.config.one_use_seal:
            cap_budget = turn.capsule.max_quantity if turn.capsule else 1
            seal = self.issuer.issue(
                proposal,
                turn.capsule,
                approval_id=approval_id or proposal.approval_id,
                approval_state=approval_state,
                policy=policy,
                principal=turn.principal,
                budget=max(1, cap_budget - turn.budget_used),
                ttl_s=ttl_s,
            )
        return AuthorizationResult(policy.decision, policy, seal, score)

    # -- verification / consumption ---------------------------------------- #
    def verify(
        self,
        seal: Seal | None,
        proposal: Proposal,
        turn: TurnContext,
        *,
        current_pre_state: str | None = None,
        policy: PolicyResult | None = None,
        approval_id: str = "",
        approval_state: str = "",
    ) -> tuple[bool, str]:
        """Verify and consume a seal immediately before the effect runs.

        When one-use seals are ablated away there is nothing to verify, so this
        returns ``(True, "seal disabled")`` — modeling exactly the safety that
        removing the capability loses (no replay/TOCTOU protection)."""
        if self.config.mode != "intentseal" or not self.config.one_use_seal:
            return True, "seal disabled"
        if seal is None:
            return False, "no seal issued"
        effective_policy = policy or self.policy.evaluate(
            proposal, turn.capsule, turn.evidence, turn.budget_used
        )
        cap_budget = turn.capsule.max_quantity if turn.capsule else 1
        return self.issuer.verify_and_consume(
            seal,
            proposal,
            turn.capsule,
            current_pre_state=current_pre_state,
            policy=effective_policy,
            principal=turn.principal,
            approval_id=approval_id or proposal.approval_id,
            approval_state=approval_state,
            budget=max(1, cap_budget - turn.budget_used),
        )

    # -- postconditions + audit -------------------------------------------- #
    def check_postcondition(
        self, *, expected_change: bool, pre_state_hash: str, post_state_hash: str
    ) -> PostconditionResult:
        if not self.config.postconditions:
            return PostconditionResult(True, "postconditions disabled")
        return check_postcondition(
            expected_change=expected_change,
            pre_state_hash=pre_state_hash,
            post_state_hash=post_state_hash,
        )

    def record_outcome(
        self, proposal: Proposal, policy: PolicyResult, seal_nonce: str, outcome: str
    ) -> None:
        self.audit.append(
            tool=proposal.tool.name,
            action=str(proposal.effect.action_class),
            decision=policy.decision,
            reasons=policy.reasons,
            seal_nonce=seal_nonce,
            outcome=outcome,
        )
