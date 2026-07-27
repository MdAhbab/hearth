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

## Model-in-the-loop configurations (separate measured phase)

6 configuration(s), 1513 completed model calls. Every configuration ran the same corpus, the same frozen labels, and the same deterministic gate; only the model and the machine differ. These figures describe model proposals and the monitor's handling of them, never merged into one score and never merged with the deterministic configuration comparison above.

Incomplete run(s): model_qwen35_4b_x86. Missing records were not imputed and support no full-corpus claim.

### Identity

| Directory | Model | Digest | Quant. | Ollama | Machine | Calls | Status |
|---|---|---|---|---|---|---:|---|
| `model` | `gemma4:e2b` | `7fbdbf8f5e45a75b...` | Q4_K_M | 0.32.3 | Apple M3 | 280 | complete |
| `model_gemma4_e2b_ollama0324` | `gemma4:e2b` | `7fbdbf8f5e45a75b...` | Q4_K_M | 0.32.4 | Apple M3 | 280 | complete |
| `model_minimax_m3_cloud` | `minimax-m3:cloud` | `not content-addressable` | undisclosed | 0.32.4 | Apple M3 | 280 | complete |
| `model_nemotron3_super_cloud` | `nemotron-3-super:cloud` | `not content-addressable` | NVFP4 | 0.32.4 | Apple M3 | 280 | complete |
| `model_qwen35_4b` | `qwen3.5:4b` | `2a654d98e6fba55d...` | Q4_K_M | 0.32.4 | Apple M3 | 280 | complete |
| `model_qwen35_4b_x86` | `qwen3.5:4b` | `2a654d98e6fba55d...` | Q4_K_M | 0.32.4 | Intel Core i5-10400 | 113 | incomplete |

### Model behavior on the base records

| Directory | Correct tool | Unsafe proposal (adv.) | No tool | Refusal or parse failure | Parse failure |
|---|---:|---:|---:|---:|---:|
| `model` | 0.800 | 0.630 | 0.155 | 0.025 | 0.000 |
| `model_gemma4_e2b_ollama0324` | 0.805 | 0.640 | 0.150 | 0.020 | 0.000 |
| `model_minimax_m3_cloud` | 0.475 | 0.040 | 0.475 | 0.405 | 0.000 |
| `model_nemotron3_super_cloud` | 0.400 | 0.080 | 0.590 | 0.255 | 0.000 |
| `model_qwen35_4b` | 0.620 | 0.280 | 0.335 | 0.270 | 0.000 |
| `model_qwen35_4b_x86` | 0.667 | 0.500 | 0.333 | 0.333 | 0.000 |

### Monitor and execution on those proposals

| Directory | Attempted misuse (adv.) | Policy acc. when proposed | Observed harm (adv.) | Canary | Benign success | Seal before handler |
|---|---:|---:|---:|---:|---:|---:|
| `model` | 0.690 | 0.645 | 0.180 | 0.000 | 0.950 | 1.000 |
| `model_gemma4_e2b_ollama0324` | 0.700 | 0.647 | 0.180 | 0.000 | 0.950 | 1.000 |
| `model_minimax_m3_cloud` | 0.070 | 0.495 | 0.020 | 0.000 | 0.890 | 1.000 |
| `model_nemotron3_super_cloud` | 0.080 | 0.549 | 0.010 | 0.000 | 0.720 | 1.000 |
| `model_qwen35_4b` | 0.330 | 0.609 | 0.110 | 0.000 | 0.940 | 1.000 |
| `model_qwen35_4b_x86` | 0.500 | 1.000 | 0.000 | 0.000 | 1.000 | 1.000 |

Rates conditioned on a proposal have a per-configuration denominator, because each model decides how often it proposes anything at all. Read them beside the decision counts in each `summary.json` rather than as like-for-like point estimates.

Latency and repeat-proposal variation are machine-dependent and are left in each `summary.json` rather than tabulated across machines here.

Run or resume one configuration:

```bash
python benchmarks/intentseal/model_eval.py --model <key> --resume
```

Run several, strictly one at a time:

```bash
python benchmarks/intentseal/run_model_evals.py <key> <key>
```

_Generated only after validating the deterministic and optional model result hashes._
