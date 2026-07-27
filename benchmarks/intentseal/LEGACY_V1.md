# Legacy v1 benchmark artifacts

The following files are retained only for audit history and are not canonical:

- `scenarios.jsonl`
- `schema.json`
- `results/v1/results.json`
- `results/v1/results.csv`
- `results/v1/summary.json`

They are kept rather than deleted so the change of method between the two
versions can be inspected instead of taken on trust. The three result files
used to sit loose in `results/`, beside the current per-model directories,
where they were easy to mistake for current output; they now have their own
directory.

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
