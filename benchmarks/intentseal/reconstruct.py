"""Map frozen v2 records to the production monitor's typed interfaces."""

from __future__ import annotations

from typing import Any

from hearth.assurance import (
    ActionClass,
    CanonicalTarget,
    DataClass,
    EffectKind,
    IntentCapsule,
    Origin,
    PredictedEffect,
    Principal,
    Proposal,
    ToolIdentity,
    TurnContext,
)


def _data_classes(names: list[str] | None) -> frozenset[DataClass]:
    return frozenset(DataClass(name) for name in (names or []))


def build_effect(record: dict[str, Any]) -> PredictedEffect:
    declared = record["proposal"]
    runtime = record["runtime"]
    target_data = declared["target"]
    attributes = dict(target_data["attributes"])
    if declared["egress"]:
        attributes["host"] = runtime["final_peer"]
        attributes["zone"] = (
            "loopback"
            if runtime["final_peer"] in {"127.0.0.1", "::1"}
            else "public"
        )
    flags = set(runtime["effect_flags"])
    effect_kinds = {EffectKind(value) for value in declared["effect_kinds"]}
    mechanism = record["mechanism"]
    unsafe = not record["is_control"]
    if unsafe and mechanism == "audience_expansion":
        effect_kinds.add(EffectKind.AUDIENCE_EXPANSION)
    if unsafe and mechanism == "account_boundary":
        flags.add("ambiguous")
    if unsafe and mechanism in {"privacy_scope", "scope_expansion"}:
        flags.add("ambiguous")
    return PredictedEffect(
        action_class=ActionClass(declared["action_class"]),
        target=CanonicalTarget(
            target_data["kind"], target_data["canonical_id"], attributes
        ),
        effect_kinds=frozenset(effect_kinds),
        audience=tuple(declared["audience"]),
        data_out=_data_classes(declared["data_out"]),
        reversible=declared["reversible"],
        quantity=declared["quantity"],
        egress=declared["egress"],
        physical=declared["physical"],
        flags=frozenset(flags),
        description=record["variant"],
    )


def build_capsule(record: dict[str, Any]) -> IntentCapsule:
    intent = record["intent"]
    return IntentCapsule(
        goal=intent["goal"],
        principal=Principal(intent["principal"], intent["account"]),
        allowed_action_classes=frozenset(
            ActionClass(value) for value in intent["allowed_action_classes"]
        ),
        allowed_resources=tuple(intent["allowed_resources"]),
        allowed_recipients=tuple(intent["allowed_recipients"]),
        protected_data_classes=_data_classes(intent["protected_data_classes"]),
        max_quantity=int(intent["max_quantity"]),
    ).freeze()


def build_turn(
    record: dict[str, Any],
    *,
    persisted_lineage: tuple[str, ...] = (),
) -> TurnContext:
    capsule = build_capsule(record)
    turn = TurnContext(principal=capsule.principal, capsule=capsule)
    runtime = record["runtime"]
    origin = Origin.MEMORY if persisted_lineage else Origin(runtime["argument_origin"])
    for name in runtime["tainted_args"]:
        if name not in record["proposal"]["args"]:
            continue
        classes = _data_classes(runtime["data_classes"].get(name))
        turn.evidence.record(
            origin,
            record["proposal"]["args"][name],
            classes,
            preview=f"{record['id']}:{name}",
            source=(
                f"memory://{record['pair_id']}"
                if persisted_lineage
                else f"{record['source_origin']}://{record['pair_id']}"
            ),
            field_path=f"proposal.args.{name}",
            lineage=persisted_lineage,
            ttl_s=3600,
        )
    return turn


def to_runtime(
    record: dict[str, Any],
    *,
    persisted_lineage: tuple[str, ...] = (),
) -> tuple[Proposal, IntentCapsule, TurnContext]:
    turn = build_turn(record, persisted_lineage=persisted_lineage)
    effect = build_effect(record)
    args = dict(record["proposal"]["args"])
    bound = tuple(
        turn.evidence.bind_arg(name, value, literal=True)
        for name, value in args.items()
    )
    evidence = tuple(
        ref
        for value in args.values()
        if (ref := turn.evidence.match(value)) is not None
    )
    fixture = record["carrier"]
    manifest = (
        fixture.get("manifest_hash_before", "")
        if fixture.get("kind") == "mcp_manifest"
        else ""
    )
    proposal = Proposal(
        tool=ToolIdentity(
            name=record["tool"],
            manifest_hash=manifest,
            namespace="hearth.benchmark",
            publisher="hearth-benchmark",
            server=fixture.get("server", ""),
        ),
        args=args,
        effect=effect,
        bound_args=bound,
        evidence=tuple(dict.fromkeys(evidence)),
    )
    return proposal, turn.capsule, turn
