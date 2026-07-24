# IntentSeal v2 Deterministic Evaluation

> The first section reports deterministic monitor and inert execution results. Any local-model observations appear in a separately labeled section and are never merged into the monitor metrics.

## Method

All 200 records were run once per configuration under the network kill switch and deterministic approve-after-card regime. Current cases used the real ToolRegistry and ActionGate path. Future cases used disabled protocol twins and the same monitor interfaces. Expected labels came only from the frozen adjudication artifact.

The reported metrics keep policy decisions, proposed misuse, execution attempts, observed state harm, canary disclosure, persistent influence, false block/ask, approval count, replay, edit invalidation, TOCTOU, staging, rollback, postcondition mismatch, duplicate suppression, final-peer policy, and cloud-egress policy separate.

## Measured deterministic results

| Configuration | Policy accuracy (all) | Adversarial accuracy | Benign task success | Observed harm | Canary disclosure | False block | False ask | Approvals/task |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `baseline_current_hearth` | 0.445 | 0.350 | 1.000 | 0.990 | 0.170 | 0.000 | 0.460 | 1.040 |
| `detector_advisory` | 0.445 | 0.350 | 1.000 | 0.990 | 0.170 | 0.000 | 0.460 | 1.040 |
| `intentseal_full` | 0.710 | 0.860 | 0.980 | 0.420 | 0.000 | 0.020 | 0.000 | 0.625 |
| `ablation_no_canonicalization` | 0.585 | 0.610 | 0.980 | 0.570 | 0.000 | 0.020 | 0.000 | 0.675 |
| `ablation_no_domain_rules` | 0.615 | 0.790 | 0.980 | 0.420 | 0.000 | 0.020 | 0.000 | 0.550 |
| `ablation_no_intent_binding` | 0.730 | 0.900 | 0.980 | 0.460 | 0.000 | 0.020 | 0.000 | 0.645 |
| `ablation_no_one_use_seal` | 0.710 | 0.860 | 0.980 | 0.420 | 0.000 | 0.020 | 0.000 | 0.625 |
| `ablation_no_postconditions` | 0.710 | 0.860 | 0.980 | 0.430 | 0.000 | 0.020 | 0.000 | 0.625 |
| `ablation_no_provenance` | 0.515 | 0.450 | 1.000 | 0.800 | 0.170 | 0.000 | 0.000 | 0.755 |
| `ablation_no_staging` | 0.710 | 0.860 | 0.980 | 0.430 | 0.000 | 0.020 | 0.000 | 0.625 |

The independently labeled result is not perfect agreement. Full IntentSeal matched 0.710 of all labels and 0.860 of adversarial labels, versus 0.445 and 0.350 for the baseline. Observed adversarial state harm was 0.420 for full IntentSeal and 0.990 for the approve-after-card baseline.

Full IntentSeal benign task success was 0.980. This is a measured limitation, not silently converted into policy agreement. The v2 rows retain each false block and ask for inspection.

Execution-integrity outcomes under full IntentSeal were: replay block 1.000, edit invalidation 1.000, TOCTOU block 1.000, rollback completeness 1.000, duplicate suppression 0.875, and audit-chain validity 1.000. Applicability denominators are the rows that exercised each condition.

## Reproducibility

```bash
python benchmarks/intentseal/build_scenarios.py
python benchmarks/intentseal/runner.py
python benchmarks/intentseal/build_docs.py
pytest tests/test_benchmark.py
```

Corpus SHA-256: `c5cec9c7880b77d03e489f13a3685148af262a0463b27f1477f8ef8109ff47ab`.  
Labels SHA-256: `ebb4a5c83840dbd2a8e140cc4a6911c48d05d0464f8d11bd8bafbd43bf1b4191`.

## Local model behavior (separate measured phase)

Status: **incomplete pilot**; 113/280 preregistered calls exist. Missing records were not imputed. These figures describe model proposals and subsequent monitor handling, not the deterministic v2 configuration comparison above.

Model: `gemma4:e2b`; digest `7fbdbf8f5e45a75bb122155ed546e765b4d9c53a1285f62fd9f506baa1c5a47e`; quantization `Q4_K_M`. Temperature 0 was requested, but no deterministic model-behavior claim is made.

Completed base records: 113/200. Model behavior on those completed base rows: no-tool rate 0.168142, exact canonical proposal rate 0.787611, adversarial proposal-diversion rate 0.614035, and refusal/parse-failure rate 0.026549.

Monitor/execution outcomes for those same observed proposals: benign task success 0.928571, adversarial attempted misuse 0.666667, observed harm 0.175439, and canary disclosure 0.0.

Complete/resume command:

```bash
python benchmarks/intentseal/model_eval.py --resume
```

_Generated only after validating the deterministic and optional model result hashes._
