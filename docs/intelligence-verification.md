# Codesplash intelligence implementation verification

Verified locally on 25–26 September 2026, Python 3.13.1 and Node 22.22.3.

## What runs

| Check | Result |
|---|---|
| Intelligence and LLM configuration PR, isolated checkout | 622 backend tests passed; production frontend build and scoped Ruff checks passed |
| Browser smoke test | Flood replay, generated advice, grounded risk question, evidence retrieval and field preview passed |
| TypeScript check and production frontend build | Passed; committed dashboard rebuilt |
| LLM configuration browser check | Provider/model/reasoning controls, Anthropic manual budget, missing-key feedback, offline save and reload passed |
| npm dependency audit | 0 vulnerabilities after updating Vite 5.4.21 to compatible 6.4.3 |
| Offline synthetic flood + intelligence | Valid ledger; fake provider: 1 accepted and 2 rejected claims |
| Cached Ratnapura Ditwah 2025 replay + intelligence | Valid ledger; 12 accepted template claims; no hardware/key/network required |
| Cached real replay + deliberately bad provider | Valid ledger; 1 accepted and 2 rejected claims |
| Synthetic A/B/C evaluation | All 24 proposal-table values reproduced; B and C each met 4/4 existing targets |
| Ratnapura ATLAS evaluation | 100% success in the seeded flood/misinformation cases; 4/4 existing targets met |
| Ratnapura planner pipeline, 20 runs | Median 19.5 ms; p95 28.7 ms; max 28.8 ms on this laptop |

The pipeline timing excludes A1 detection, human approval and delivery. It is not measured response
latency in the field. Cached real gauge data is paired with synthetic shelter/fleet/population data.
Live upstream downloads and real LLM services were not exercised.

## Planner comparison

`python -m eval.run_reasoning --trials 30` on the committed synthetic scenario:

| Policy | Mean utility | Success rate | Mean failed route attempts |
|---|---:|---:|---:|
| Shortest feasible | 40.17 | 93.33% | 0.833 |
| Scenario evaluation | 64.67 | 93.33% | 0.133 |
| MCTS | 64.67 | 93.33% | 0.133 |

This supports reduced failed attempts on this toy scenario, not a claim of improved success rate.
The real-network ATLAS cognitive policy also matched the reactive baseline's success rate, rather than
beating it. The older elevation-penalty engine still achieved only 61.5% / 60.5% on the two Ratnapura
flood cases; the existing ATLAS planners disable that penalty.

## Tests added

The settings tests verify administrator permissions, key redaction, private atomic persistence,
provider-specific key isolation, key removal, validation, immediate application and unsaved
connection tests. HTTP mocks verify Anthropic thinking payloads and OpenAI Responses reasoning
continuation across tool calls. A browser-discovered identity issue was fixed so development mode
and valid legacy administrator tokens receive their actual permissions. The local settings file
was verified as mode `0600`; it is excluded from Git and is not encrypted. Real provider calls
remain unverified because no API key was supplied.

Fake providers exercise invented entities, contradictory values with valid citations, confidence
inflation, malformed output, timeouts, fallback, caching and call budgets. Worker tests verify that
model calls hold neither the bus lock nor a database transaction, another event can be published
while the model waits, and interrupted publication recovers without duplicate advice. API tests cover
role enforcement, bounded field inputs and extraction previews. An HTTP mock covers the OpenAI tool
loop and aggregate token counts. The rate-limiter regression proves quota waits release the lock.

## Items the team still needs

- Hasindu: LoRa gateway/codec/field-message implementations and radio bench/range tests.
- Sumudu: domain ontology validation and entity linking; full temporal graph and hazard-to-map extensions.
- Dinuka/integration: field-preview submission contract, broader UI polish, Docker and CI.
- Live credentials/provider access, MQTT broker/Pi drills and physical calibration are unverified.

No shared event enum or sensor message schema changed. Published changes are split into reviewable pull requests; live provider calls remain unverified.

## Timeline verification

Timeline coverage checks pagination without gaps/duplicates, incident input ancestry, unrelated event exclusion, measured work across A1–A9, offline advice provenance, read permissions and planner-record ownership. Browser checks exercised recorded events, filters, evidence, replay, scrubbing, input/output inspection and planner alternatives.

Timeline PR isolated checkout: **629 backend tests passed**; production frontend build and scoped Ruff checks passed.

## Simulation benchmark

See [the benchmark guide](simulation-benchmark.md) for the ten tasks, replayable random schedules, recorded results and scope limitations. API-backed batch benchmark execution is not enabled; benchmark advice remains offline.

Final benchmark PR isolated checkout: **641 backend tests passed**, the production frontend build and scoped Ruff checks passed, and all ten tasks passed on seeds 42, 43 and 44 (30/30 cases).
