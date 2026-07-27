#!/usr/bin/env python3
"""Drive one or more model-in-the-loop evaluations, strictly one at a time.

The comparison quotes a median latency and a median monitor overhead per
configuration.  Those numbers are only meaningful if no second model competed
for the same hardware while they were measured, so this driver runs each
configuration to completion, unloads it from the Ollama daemon, and only then
starts the next.  Each configuration writes its own results directory and each
run is resumable, so re-issuing the same command after an interruption
continues from the calls already on disk.

    python benchmarks/intentseal/run_model_evals.py qwen35-4b-x86 gemma4-e4b-x86

Passing no configuration names lists the registered ones and exits.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for candidate in (_REPO, _REPO / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from benchmarks.intentseal.model_eval import (  # noqa: E402
    MODEL_SPECS,
    ProductionLocalOllamaProvider,
    fetch_ollama_inventory,
    run_evaluation,
)


def unload(model_id: str) -> None:
    """Ask the daemon to evict a model so the next run starts from a cold slot.

    A failure here is reported and tolerated: keep_alive expires on its own,
    and the next run still measures only its own model.
    """
    try:
        subprocess.run(
            ["ollama", "stop", model_id],
            check=False,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  could not unload {model_id}: {exc}")


async def run_one(key: str, limit: int | None) -> None:
    spec = MODEL_SPECS[key]
    print(f"\n=== {spec.label}  ({spec.model_id}, {spec.execution}) ===")
    started = time.perf_counter()
    inventory = await fetch_ollama_inventory(spec)
    outcome = await run_evaluation(
        provider=ProductionLocalOllamaProvider(spec),
        inventory=inventory,
        limit=limit,
        spec=spec,
    )
    manifest = outcome["manifest"]
    elapsed = time.perf_counter() - started
    print(
        f"  {manifest['completed_model_calls']}/{manifest['planned_model_calls']} "
        f"calls, {outcome['new_rows']} new, {outcome['remaining_rows']} remaining, "
        f"{manifest['errors']} row(s) with errors, {elapsed / 60:.1f} min"
    )
    print(f"  status: {manifest['status']}  ->  {spec.results_dir}")
    unload(spec.model_id)
    if outcome["interrupted"]:
        # Do not start the next configuration against a daemon that just
        # stopped answering; it would fail the same way and look like a run.
        raise SystemExit(
            f"  stopped early: {outcome['interrupted']}\n"
            "  No row was written for that case. Restart the local daemon and "
            "re-issue the same command to resume."
        )


async def main_async(keys: list[str], limit: int | None) -> None:
    for key in keys:
        await run_one(key, limit)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "models",
        nargs="*",
        choices=[*sorted(MODEL_SPECS), []],
        help="registered configuration keys, run in the order given",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="run at most this many missing calls per configuration",
    )
    args = parser.parse_args()
    if not args.models:
        print("registered configurations:")
        for key, spec in sorted(MODEL_SPECS.items()):
            print(f"  {key:18s} {spec.model_id:24s} {spec.execution:5s} {spec.label}")
        return
    asyncio.run(main_async(args.models, args.limit))


if __name__ == "__main__":
    main()
