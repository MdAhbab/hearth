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
