"""Loading, integrity checks, pairing checks, and agreement statistics for v2."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from hearth.assurance import stable_hash

HERE = Path(__file__).resolve().parent
SCENARIOS_PATH = HERE / "scenarios.v2.jsonl"
LABELS_PATH = HERE / "labels.v2.json"
SCHEMA_PATH = HERE / "schema.v2.json"


def load_scenarios() -> list[dict[str, Any]]:
    with SCENARIOS_PATH.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def load_label_artifact(
    records: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    artifact = json.loads(LABELS_PATH.read_text(encoding="utf-8"))
    actual_hash = hashlib.sha256(SCENARIOS_PATH.read_bytes()).hexdigest()
    if actual_hash != artifact["scenario_sha256"]:
        raise ValueError(
            "frozen labels do not match corpus: "
            f"expected {artifact['scenario_sha256']}, got {actual_hash}"
        )
    fields = artifact["record_fields"]
    labels = {
        row[0]: dict(zip(fields[1:], row[1:], strict=True))
        for row in artifact["records"]
    }
    scenarios = records if records is not None else load_scenarios()
    scenario_ids = {record["id"] for record in scenarios}
    if set(labels) != scenario_ids:
        missing = sorted(scenario_ids - set(labels))
        extra = sorted(set(labels) - scenario_ids)
        raise ValueError(f"label ids differ from scenario ids; missing={missing}, extra={extra}")
    return artifact, labels


def agreement_statistics(labels: dict[str, dict[str, str]]) -> dict[str, Any]:
    pairs = [(row["reviewer_a"], row["reviewer_b"]) for row in labels.values()]
    n = len(pairs)
    agreements = sum(a == b for a, b in pairs)
    count_a = Counter(a for a, _ in pairs)
    count_b = Counter(b for _, b in pairs)
    categories = set(count_a) | set(count_b)
    observed = agreements / n if n else 0.0
    expected = (
        sum((count_a[value] / n) * (count_b[value] / n) for value in categories)
        if n
        else 0.0
    )
    kappa = (observed - expected) / (1.0 - expected) if expected < 1.0 else 1.0
    return {
        "n": n,
        "agreements": agreements,
        "disagreements": n - agreements,
        "raw_agreement": round(observed, 6),
        "cohen_kappa": round(kappa, 6),
        "reviewer_a_distribution": dict(sorted(count_a.items())),
        "reviewer_b_distribution": dict(sorted(count_b.items())),
    }


def executable_signature(record: dict[str, Any]) -> str:
    """Signature fixture state and trajectory, not prose or labels."""
    return stable_hash(
        {
            "seed": record["seed"],
            "carrier": record["carrier"],
            "proposal": record["proposal"],
            "runtime": record["runtime"],
            "trajectory": record["trajectory"],
        }
    )


def pair_similarity_errors(records: list[dict[str, Any]]) -> list[str]:
    by_id = {record["id"]: record for record in records}
    errors: list[str] = []
    preserved = (
        ("carrier.kind", lambda row: row["carrier"]["kind"]),
        ("carrier.source_trust", lambda row: row["carrier"]["source_trust"]),
        ("source_origin", lambda row: row["source_origin"]),
        ("source_trust", lambda row: row["source_trust"]),
        ("tool", lambda row: row["tool"]),
        ("proposal.target", lambda row: row["proposal"]["target"]),
        ("proposal.action_class", lambda row: row["proposal"]["action_class"]),
        ("proposal.effect_kinds", lambda row: row["proposal"]["effect_kinds"]),
        ("proposal.audience", lambda row: row["proposal"]["audience"]),
        ("proposal.reversible", lambda row: row["proposal"]["reversible"]),
        ("proposal.egress", lambda row: row["proposal"]["egress"]),
        ("proposal.physical", lambda row: row["proposal"]["physical"]),
        ("proposal.quantity", lambda row: row["proposal"]["quantity"]),
    )
    for adversarial in (row for row in records if not row["is_control"]):
        control = by_id.get(adversarial["benign_control_id"])
        if control is None:
            errors.append(f"{adversarial['id']}: missing control")
            continue
        if control["control_of"] != adversarial["id"]:
            errors.append(f"{adversarial['id']}: reverse pair link differs")
        for path, getter in preserved:
            if getter(adversarial) != getter(control):
                errors.append(f"{adversarial['id']}: preserved field differs: {path}")
        adv_args = adversarial["proposal"]["args"]
        ctl_args = control["proposal"]["args"]
        documented = " ".join(
            adversarial["pair_contract"]["justified_exceptions"]
            + control["pair_contract"]["justified_exceptions"]
        )
        for key in sorted(set(adv_args) | set(ctl_args)):
            if adv_args.get(key) != ctl_args.get(key):
                path = f"proposal.args.{key}"
                if path not in documented:
                    errors.append(
                        f"{adversarial['id']}: undocumented argument difference {path}"
                    )
        if adversarial["source_trust"] != "untrusted_data":
            errors.append(f"{adversarial['id']}: source trust is not preserved as data")
    return errors


def _is_type(node: Any, expected: str) -> bool:
    return {
        "object": isinstance(node, dict),
        "array": isinstance(node, list),
        "string": isinstance(node, str),
        "integer": isinstance(node, int) and not isinstance(node, bool),
        "number": isinstance(node, (int, float)) and not isinstance(node, bool),
        "boolean": isinstance(node, bool),
        "null": node is None,
    }.get(expected, True)


def _validate(node: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    declared_type = schema.get("type")
    allowed = (
        declared_type if isinstance(declared_type, list)
        else [declared_type] if declared_type else []
    )
    if allowed and not any(_is_type(node, value) for value in allowed):
        errors.append(f"{path}: expected {allowed}, got {type(node).__name__}")
        return
    if "const" in schema and node != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and node not in schema["enum"]:
        errors.append(f"{path}: {node!r} not in enum")
    if "pattern" in schema and isinstance(node, str):
        if re.search(schema["pattern"], node) is None:
            errors.append(f"{path}: {node!r} does not match {schema['pattern']}")
    if "minLength" in schema and isinstance(node, str):
        if len(node) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
    if "minItems" in schema and isinstance(node, list):
        if len(node) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
    if "minimum" in schema and isinstance(node, (int, float)):
        if node < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
    if isinstance(node, dict):
        for required in schema.get("required", []):
            if required not in node:
                errors.append(f"{path}: missing required {required!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in node:
                if key not in properties:
                    errors.append(f"{path}: unexpected property {key!r}")
        for key, subschema in properties.items():
            if key in node:
                _validate(node[key], subschema, f"{path}.{key}", errors)
    if isinstance(node, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(node):
            _validate(item, schema["items"], f"{path}[{index}]", errors)


def validate_all(
    records: list[dict[str, Any]], schema: dict[str, Any] | None = None
) -> list[str]:
    selected = schema or json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors: list[str] = []
    for record in records:
        _validate(record, selected, record.get("id", "?"), errors)
    return errors
