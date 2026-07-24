# Legacy v1 benchmark artifacts

The following files are retained only for audit history and are not canonical:

- `scenarios.jsonl`
- `schema.json`
- `results/results.json`
- `results/results.csv`
- `results/summary.json`

Version 1 generated labels from the monitor it evaluated and applied permitted
effects directly to an emulator. Its reported policy match is therefore a
self-consistency result, not independently grounded accuracy.

The canonical evaluation is version 2:

- `scenarios.v2.jsonl`
- `schema.v2.json`
- `labels.v2.json`
- `LABELING.md`
- `corpus.v2.manifest.json`
- `results/v2/`

Only v2 is consumed by `runner.py`, `build_docs.py`, and the benchmark tests.
