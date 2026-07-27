"""Generate v2 research notes from hash-matched deterministic/model results.

The deterministic section is always sourced from the completed v2 monitor
run.  A separate model-behavior section appears only after at least one real,
hash-pinned model row exists; partial pilots are explicitly marked incomplete.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from benchmarks.intentseal.corpus import (  # noqa: E402
    LABELS_PATH,
    SCENARIOS_PATH,
    agreement_statistics,
    load_label_artifact,
    load_scenarios,
)

HERE = Path(__file__).resolve().parent
DOCS = _REPO / "docs" / "research"
RESULTS_ROOT = HERE / "results"
SUMMARY_PATH = RESULTS_ROOT / "v2" / "summary.json"
MODEL_RESULTS_DIR = RESULTS_ROOT / "model"
MODEL_SUMMARY_PATH = MODEL_RESULTS_DIR / "summary.json"
MODEL_MANIFEST_PATH = MODEL_RESULTS_DIR / "run_manifest.json"
MODEL_RAW_PATH = MODEL_RESULTS_DIR / "raw.jsonl"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_doc(path: Path, text: str) -> None:
    """Write a generated document with LF terminators on every platform.

    ``Path.write_text`` applies the platform newline translation, which would
    make a regenerated document differ on Windows and on macOS without any
    result changing.
    """
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def _model_run_directories() -> list[Path]:
    """Every per-model results directory, preregistered run first.

    The preregistered configuration writes to ``results/model``; each further
    configuration writes to ``results/model_<name>``.  Sorting the rest keeps
    the generated document stable when a run is added.
    """
    if not RESULTS_ROOT.is_dir():
        return []
    others = sorted(
        path
        for path in RESULTS_ROOT.iterdir()
        if path.is_dir() and path.name.startswith("model_")
    )
    return [MODEL_RESULTS_DIR, *others] if MODEL_RESULTS_DIR.is_dir() else others


def _load_inputs():
    records = load_scenarios()
    artifact, labels = load_label_artifact(records)
    if not SUMMARY_PATH.exists():
        raise SystemExit(
            "v2 deterministic results have not run; execute "
            "`python benchmarks/intentseal/runner.py` first"
        )
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    expected = {
        "corpus_sha256": _sha256(SCENARIOS_PATH),
        "labels_sha256": _sha256(LABELS_PATH),
    }
    for field, digest in expected.items():
        if summary.get(field) != digest:
            raise SystemExit(
                f"refusing stale results: {field} is {summary.get(field)!r}, "
                f"expected {digest!r}"
            )
    model_results = [
        run
        for directory in _model_run_directories()
        if (run := _load_model_run(directory, expected)) is not None
    ]
    return records, artifact, labels, summary, model_results


def _load_model_run(
    directory: Path,
    expected: dict[str, str],
) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    """Validate and load one per-model run, or return None if it has no rows.

    Every configuration is checked against the same corpus and label hashes, so
    a run measured against a different corpus is refused rather than documented
    beside the others.
    """
    summary_path = directory / "summary.json"
    manifest_path = directory / "run_manifest.json"
    raw_path = directory / "raw.jsonl"
    model_paths = (summary_path, manifest_path, raw_path)
    if not any(path.exists() for path in model_paths):
        return None
    if not all(path.exists() for path in model_paths):
        raise SystemExit(
            f"model result artifacts are incomplete in {directory.name}; "
            "refusing mixed docs"
        )
    model_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    model_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    live_raw_rows = sum(
        1 for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    completed = max(int(model_summary.get("completed_model_calls", 0)), live_raw_rows)
    if completed <= 0:
        return None
    model_identity = model_manifest.get("evaluation_identity", {})
    for field, digest in expected.items():
        if model_summary.get(field) != digest:
            raise SystemExit(
                f"refusing stale model summary in {directory.name}: "
                f"{field} differs from {digest}"
            )
        if model_identity.get(field) != digest:
            raise SystemExit(
                f"refusing stale model manifest in {directory.name}: "
                f"{field} differs from {digest}"
            )
    raw_artifact = model_manifest.get("artifacts", {}).get("raw_jsonl", {})
    raw_digest = _sha256(raw_path)
    status = str(model_summary.get("status") or model_manifest.get("status") or "")
    if raw_artifact.get("sha256") != raw_digest:
        if status != "incomplete":
            raise SystemExit(
                f"model raw JSONL hash differs from run manifest in {directory.name}"
            )
        # Resumable pilots rewrite raw.jsonl between summary flushes.
        # Document the incomplete state with the live raw digest.
        model_summary = {
            **model_summary,
            "status": "incomplete",
            "incomplete_run_caveat": (
                "Partial model evaluation in progress. Live raw JSONL digest "
                f"{raw_digest} differs from the last flushed manifest artifact "
                f"{raw_artifact.get('sha256')!r}. No full-corpus model claim "
                "is supported."
            ),
            "live_raw_sha256": raw_digest,
            "completed_model_calls": completed,
        }
    elif int(model_manifest.get("completed_model_calls", -1)) != completed:
        if status != "incomplete":
            raise SystemExit(
                f"model summary and manifest row counts differ in {directory.name}"
            )
    return directory.name, model_summary, model_manifest


def _catalog(
    records: list[dict[str, Any]],
    labels: dict[str, dict[str, str]],
    agreement: dict[str, Any],
) -> str:
    adversarial = [record for record in records if not record["is_control"]]
    counts = {
        "current": sum(row["capability"] == "current_hearth" for row in adversarial),
        "future": sum(row["capability"] == "future_emulator" for row in adversarial),
    }
    lines = [
        "# IntentSeal v2 Threat Catalog",
        "",
        "> Canonical version 2 corpus. The retained v1 files are legacy "
        "self-consistency artifacts and are not used here.",
        "",
        "## Evidence boundary",
        "",
        f"The corpus contains exactly {len(adversarial)} adversarial scenarios and "
        f"{len(records) - len(adversarial)} matched controls. The adversarial split is "
        f"{counts['current']} current-Hearth scenarios and {counts['future']} "
        "disabled future-emulator scenarios. Labels are frozen separately in "
        "`labels.v2.json`; corpus generation never invokes the monitor.",
        "",
        "Reviewer A and reviewer B were non-human, AI-assisted labeling roles. "
        "They are not represented as human or organizationally independent reviewers. "
        f"They agreed on {agreement['agreements']}/{agreement['n']} records "
        f"({agreement['raw_agreement']:.1%}); Cohen's kappa was "
        f"{agreement['cohen_kappa']:.6f}. All reviewer labels and adjudications are "
        "preserved in the frozen artifact.",
        "",
        "## Fixture and execution coverage",
        "",
        "The current-Hearth records execute through ToolRegistry, ActionGate, "
        "IntentSeal authorization, one-use seal verification, an inert staged "
        "handler, postconditions, rollback metadata, and the SQLite hash-chain audit. "
        "Future records use disabled-by-default TCP, WebSocket, MQTT, and IoT twins "
        "through the same authorize/verify interfaces. A process-wide network kill "
        "switch denies DNS and socket connections.",
        "",
        "Every seed has a unique executable signature over carrier fixture, declared "
        "proposal, runtime condition, and trajectory. G7 includes retries, "
        "idempotency, history provenance, edits, TOCTOU, postcondition failure, and "
        "dependent-step stopping. Every G10 case has ingest, persisted provenance, a "
        "delay, a later-turn effect, and a dependent step.",
        "",
        "## Adversarial catalog",
        "",
        "| ID | Capability | Family | Executable condition | Tool | Final label |",
        "|---|---|---|---|---|---|",
    ]
    for record in adversarial:
        lines.append(
            f"| {record['id']} | {record['capability']} | {record['family']} | "
            f"{record['variant']} | `{record['tool']}` | "
            f"{labels[record['id']]['final']} |"
        )
    lines.extend(
        [
            "",
            "Pair matching and justified exceptions are machine-checked. The carrier "
            "format, source trust, tool, declared target, and effect are retained. "
            "Protected payload, opaque-handle, resolved-alias, final-peer, and "
            "runtime-state differences are explicitly documented.",
            "",
            f"Corpus SHA-256: `{_sha256(SCENARIOS_PATH)}`.",
            "",
            "_Generated from canonical v2 artifacts by "
            "`python benchmarks/intentseal/build_docs.py`._",
            "",
        ]
    )
    return "\n".join(lines)


def _machine(manifest: dict[str, Any]) -> str:
    hardware = manifest.get("hardware", {})
    brand = str(hardware.get("cpu_brand") or "").strip()
    if brand:
        for noise in ("(R)", "(TM)", "CPU", "Processor"):
            brand = brand.replace(noise, " ")
        return " ".join(brand.split()).split("@")[0].strip()
    return str(hardware.get("platform") or "unrecorded").split("-")[0]


def _model_section(
    model_results: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> list[str]:
    """Render one row per evaluated configuration, plus its identity block.

    Model behavior and monitor behavior stay in separate column groups here for
    the same reason they do in the paper: collapsing them into one score would
    rank a model that proposes nothing above a model that does the user's work.
    """
    if not model_results:
        return [
            "## Model-in-the-loop phase",
            "",
            "No model row exists yet. The preregistered schedule is 200 base "
            "records plus two additional runs of 40 selected records (280 local "
            "model calls total). No model efficacy, latency, or variance result "
            "is stated or implied.",
            "",
        ]

    total_calls = sum(
        int(summary.get("completed_model_calls", 0)) for _, summary, _ in model_results
    )
    incomplete = [
        name for name, summary, _ in model_results if summary.get("status") != "complete"
    ]
    lines = [
        "## Model-in-the-loop configurations (separate measured phase)",
        "",
        f"{len(model_results)} configuration(s), {total_calls} completed model calls. "
        "Every configuration ran the same corpus, the same frozen labels, and the "
        "same deterministic gate; only the model and the machine differ. These "
        "figures describe model proposals and the monitor's handling of them, "
        "never merged into one score and never merged with the deterministic "
        "configuration comparison above.",
        "",
    ]
    if incomplete:
        lines.extend(
            [
                f"Incomplete run(s): {', '.join(incomplete)}. Missing records were "
                "not imputed and support no full-corpus claim.",
                "",
            ]
        )
    lines.extend(
        [
            "### Identity",
            "",
            "| Directory | Model | Digest | Quant. | Ollama | Machine | Calls | Status |",
            "|---|---|---|---|---|---|---:|---|",
        ]
    )
    for name, summary, manifest in model_results:
        inventory = manifest.get("model_inventory", {})
        digest = str(inventory.get("model_digest") or "")
        lines.append(
            f"| `{name}` | `{inventory.get('model_id', '')}` "
            f"| `{digest[:16] + '...' if digest else 'not content-addressable'}` "
            f"| {inventory.get('quantization') or 'undisclosed'} "
            f"| {inventory.get('ollama_version', '')} "
            f"| {_machine(manifest)} "
            f"| {summary.get('completed_model_calls', 0)} "
            f"| {summary.get('status', '')} |"
        )
    lines.extend(
        [
            "",
            "### Model behavior on the base records",
            "",
            "| Directory | Correct tool | Unsafe proposal (adv.) | No tool | "
            "Refusal or parse failure | Parse failure |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name, summary, _ in model_results:
        behavior = summary["base_runs_only"]["model_behavior"]
        lines.append(
            f"| `{name}` "
            f"| {behavior['correct_tool_proposed_rate']:.3f} "
            f"| {behavior['proposal_diversion_rate_adversarial']:.3f} "
            f"| {behavior['no_tool_proposed_rate']:.3f} "
            f"| {behavior['refusal_or_parse_failure_rate']:.3f} "
            f"| {behavior['parse_failure_rate']:.3f} |"
        )
    lines.extend(
        [
            "",
            "### Monitor and execution on those proposals",
            "",
            "| Directory | Attempted misuse (adv.) | Policy acc. when proposed | "
            "Observed harm (adv.) | Canary | Benign success | Seal before handler |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for name, summary, _ in model_results:
        monitored = summary["base_runs_only"]["monitor_and_execution"]
        lines.append(
            f"| `{name}` "
            f"| {monitored['attempted_misuse_rate_adversarial']:.3f} "
            f"| {monitored['policy_accuracy_when_proposed']:.3f} "
            f"| {monitored['observed_harm_rate_adversarial']:.3f} "
            f"| {monitored['canary_disclosure_rate']:.3f} "
            f"| {monitored['benign_task_success_rate']:.3f} "
            f"| {monitored['seal_before_handler_rate']:.3f} |"
        )
    lines.extend(
        [
            "",
            "Rates conditioned on a proposal have a per-configuration denominator, "
            "because each model decides how often it proposes anything at all. "
            "Read them beside the decision counts in each `summary.json` rather "
            "than as like-for-like point estimates.",
            "",
            "Latency and repeat-proposal variation are machine-dependent and are "
            "left in each `summary.json` rather than tabulated across machines "
            "here.",
            "",
            "Run or resume one configuration:",
            "",
            "```bash",
            "python benchmarks/intentseal/model_eval.py --model <key> --resume",
            "```",
            "",
            "Run several, strictly one at a time:",
            "",
            "```bash",
            "python benchmarks/intentseal/run_model_evals.py <key> <key>",
            "```",
            "",
        ]
    )
    return lines


def _experiment(
    summary: dict[str, Any],
    model_results: list[tuple[str, dict[str, Any], dict[str, Any]]] | None = None,
) -> str:
    configs = summary["configurations"]
    lines = [
        "# IntentSeal v2 Deterministic Evaluation",
        "",
        "> The first section reports deterministic monitor and inert execution "
        "results. Any local-model observations appear in a separately labeled "
        "section and are never merged into the monitor metrics.",
        "",
        "## Method",
        "",
        "All 200 records were run once per configuration under the network kill "
        "switch and deterministic approve-after-card regime. Current cases used the "
        "real ToolRegistry and ActionGate path. Future cases used disabled protocol "
        "twins and the same monitor interfaces. Expected labels came only from the "
        "frozen adjudication artifact.",
        "",
        "The reported metrics keep policy decisions, proposed misuse, execution "
        "attempts, observed state harm, canary disclosure, persistent influence, "
        "false block/ask, approval count, replay, edit invalidation, TOCTOU, staging, "
        "rollback, postcondition mismatch, duplicate suppression, final-peer policy, "
        "and cloud-egress policy separate.",
        "",
        "## Measured deterministic results",
        "",
        "| Configuration | Policy accuracy (all) | Adversarial accuracy | "
        "Benign task success | Observed harm | Canary disclosure | "
        "False block | False ask | Approvals/task |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    order = [
        "baseline_current_hearth",
        "detector_advisory",
        "intentseal_full",
        *sorted(name for name in configs if name.startswith("ablation_")),
    ]
    for name in order:
        values = configs[name]
        lines.append(
            f"| `{name}` | {values['policy_accuracy_all']:.3f} | "
            f"{values['policy_accuracy_adversarial']:.3f} | "
            f"{values['benign_task_success']:.3f} | "
            f"{values['observed_state_harm_rate']:.3f} | "
            f"{values['canary_disclosure_rate']:.3f} | "
            f"{values['control_false_block_rate']:.3f} | "
            f"{values['control_false_ask_rate']:.3f} | "
            f"{values['approvals_per_task']:.3f} |"
        )
    full = configs["intentseal_full"]
    baseline = configs["baseline_current_hearth"]
    lines.extend(
        [
            "",
            "The independently labeled result is not perfect agreement. Full "
            f"IntentSeal matched {full['policy_accuracy_all']:.3f} of all labels and "
            f"{full['policy_accuracy_adversarial']:.3f} of adversarial labels, versus "
            f"{baseline['policy_accuracy_all']:.3f} and "
            f"{baseline['policy_accuracy_adversarial']:.3f} for the baseline. "
            f"Observed adversarial state harm was {full['observed_state_harm_rate']:.3f} "
            f"for full IntentSeal and {baseline['observed_state_harm_rate']:.3f} for "
            "the approve-after-card baseline.",
            "",
            f"Full IntentSeal benign task success was "
            f"{full['benign_task_success']:.3f}. This is a measured limitation, not "
            "silently converted into policy agreement. The v2 rows retain each false "
            "block and ask for inspection.",
            "",
            "Execution-integrity outcomes under full IntentSeal were: replay block "
            f"{full['replay_block_rate']:.3f}, edit invalidation "
            f"{full['edit_invalidation_rate']:.3f}, TOCTOU block "
            f"{full['toctou_block_rate']:.3f}, rollback completeness "
            f"{full['rollback_complete_rate']:.3f}, duplicate suppression "
            f"{full['duplicate_suppression_rate']:.3f}, and audit-chain validity "
            f"{full['audit_chain_valid_rate']:.3f}. Applicability denominators are "
            "the rows that exercised each condition.",
            "",
            "## Reproducibility",
            "",
            "```bash",
            "python benchmarks/intentseal/build_scenarios.py",
            "python benchmarks/intentseal/runner.py",
            "python benchmarks/intentseal/build_docs.py",
            "pytest tests/test_benchmark.py",
            "```",
            "",
            f"Corpus SHA-256: `{summary['corpus_sha256']}`.  ",
            f"Labels SHA-256: `{summary['labels_sha256']}`.",
            "",
        ]
    )
    lines.extend(_model_section(model_results or []))
    lines.extend(
        [
            "_Generated only after validating the deterministic and optional "
            "model result hashes._",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    records, _artifact, labels, summary, model_results = _load_inputs()
    agreement = agreement_statistics(labels)
    DOCS.mkdir(parents=True, exist_ok=True)
    catalog_path = DOCS / "intentseal-threat-catalog.md"
    experiment_path = DOCS / "intentseal-experiment.md"
    _write_doc(catalog_path, _catalog(records, labels, agreement))
    _write_doc(experiment_path, _experiment(summary, model_results))
    print(f"Wrote {catalog_path}")
    print(f"Wrote {experiment_path} with {len(model_results)} model configuration(s)")


if __name__ == "__main__":
    main()
