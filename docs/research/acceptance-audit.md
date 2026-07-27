# IntentSeal Acceptance Audit

Date: 2026-07-24  
Scope: production ActionGate path and v2 benchmark validity after Critical/High remediation.

## Production enforcement

| Finding | Severity | Resolution |
|---|---|---|
| Cross-host public redirect swapped sealed peer | Critical | Fixed: `fetch_with_validated_redirects` refuses host drift; regression added |
| Final-peer check failed open without `network_stream` | Critical | Fixed: fail closed without connected-peer proof |
| Weather used automatic redirects | High | Fixed: Open-Meteo allowlist + validated redirect helper |
| Resource inclusion used prefix match | High | Fixed: exact canonical identity only |
| Cloud fallback omitted history from consent | High | Fixed: history messages recorded in `confirm_cloud_egress` |
| Postcondition ablation bypassed by `expected_post_state` | High | Fixed: gate honors `config.postconditions` |
| Staging/postconditions only on `files_write` | Accepted limitation | Documented; not claimed as universal |
| Labels are AI-assisted dual review | Accepted limitation | Disclosed in catalog and paper methods |
| No production TCP/MQTT/WebSocket/IoT tools | Accepted limitation | Future cases remain emulator-only |

## Benchmark validity

| Finding | Severity | Resolution |
|---|---|---|
| Experiment doc claimed no LM was run | Critical | Regenerated docs; incomplete pilot section is separate |
| `persistent_influence_rate` was fixture coverage | High | Now measures realized later-step harm; coverage reported separately |
| Postcondition detection non-causal under ablation | High | Ablation now disables expected-state checks; detect rate 1.0 vs 0.0 |
| Detector advisory is decision-identical to baseline | Accepted limitation | Reported as null decision contrast |
| Approve-after-card baseline is not human safety | Accepted limitation | Stated in methods |

## Verdict

Production IntentSeal may be described as an ActionGate-integrated one-use authorization monitor with validated private/cross-host web controls, MCP restrictive defaults, durable audit/nonces, and measured deterministic monitor outcomes. Claims must include residual harm 0.420, non-human labels, staging limited to staged file writes, and incomplete model-in-the-loop status until the preregistered run finishes.

---

## Addendum, 2026-07-27: second machine and cross-platform port

Scope: porting the benchmark to a second machine, re-running the deterministic
regime there, and evaluating one pinned artifact on both. The findings above are
unchanged; this addendum records what the port exposed.

### Defects found and fixed

| Finding | Severity | Resolution |
|---|---|---|
| `core.autocrlf` rewrote `labels.v2.json` and `repeat_subset.v1.json` to CRLF on checkout, changing their SHA-256 and breaking every recorded run identity | Critical | Restored to LF, verified against all five existing manifests, and pinned with `.gitattributes`. CSV stays CRLF because the csv writer terminates rows that way and the manifests record those bytes |
| JSON artifacts were written with `Path.write_text`, so the same result set hashed differently on Windows and macOS | High | All JSON writers now emit LF explicitly, in `runner.py`, `model_eval.py`, `build_docs.py` |
| `model_eval.py` was POSIX-only (`fcntl`) and macOS-only (`sysctl`), and was hard-wired to one model | High | Portable locking, cross-platform hardware capture, and a `MODEL_SPECS` registry with a `--model` flag and a sequential driver |
| Run manifests did not record the accelerator backend | High | `_accelerator_inventory` now captures it. The one completed run that predates the change carries it as a recorded manifest migration, with its source named |
| Paper generators hard-coded a `Build/hearth` path that existed on one machine, and failed silently elsewhere by printing a skip message and leaving stale tables in place | High | Shared `_paths.py` searches for the results directory and raises if absent |

### Benchmark validity

| Finding | Severity | Resolution |
|---|---|---|
| The package claimed behavior rates are machine-independent | Critical | False for model proposals. One digest-pinned artifact, identical prompts and seeds at temperature zero, proposed differently on 36 of 200 paired records across two machines; 4 of 8 paired outcomes differ after Holm-Bonferroni correction. Corrected in the manuscript, the supplementary, the package README, and the generator. `check_prose_numbers.py` now fails on the removed phrasings |
| The package claimed repeat variation separates local from remote configurations | High | No longer true. A local, digest-pinned configuration varied on 20.0 percent of repeated records, more than one of the two cloud tags at 15.0 percent. A `\cmpvariationseparates` macro records whether the separation holds, and the checker refuses the old sentence while it does not |
| Prose stated local inference dominates latency "by three orders of magnitude" | Medium | Measured median ratio is 810, which is 2.91 orders. Replaced with a generated `\modeloverheadratio` macro |
| `fig_results.pdf` was exported at 7.16 in and placed in a 3.5 in column, scaling nominal 9 pt labels to about 4.4 pt | Medium | Figure promoted to a full-width float at its design width |
| The baseline grey and the agent blue had nearly equal relative luminance (0.167 against 0.153), so the two series printed as one shade in greyscale | Medium | Series fill changed to a lighter neutral, separating at 2.7:1 from blue and 1.6:1 from orange |
| Deterministic regime not verified off one machine | Accepted, now closed | Re-run on Windows x86-64: `results.json`, `results.csv`, `summary.json`, and `run_manifest.json` byte-identical to the macOS ARM64 files |
| Model-in-the-loop status | Accepted, now closed | The preregistered run is complete at 280/280 with 0 errors |

### Verdict

The monitor's decisions are machine-independent, and that is now measured rather
than assumed. The model's proposals are not, and the paper states so with a
paired test rather than an assurance. Every claim about reproducibility is
generated from the result files, and the two claims that the second machine
falsified are guarded by checks that fail if the old wording returns.
