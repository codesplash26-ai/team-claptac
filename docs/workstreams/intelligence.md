# Intelligence layer (Aeshan)

The screenshot assignment supersedes the older ownership table in `TEAM_PLAN.md`: Aeshan owns
`atlas/intelligence/`, the shared planner, LLM wrapper and ported A2/A3/A5 orchestration.

## Implemented

- Anthropic adapter over the existing client, optional OpenAI adapter with bounded tool loops,
  retries and rate limiting. `LLM_PROVIDER=none` forces offline operation. No provider call runs in a
  bus handler or database transaction. The rolling rate limiter's recursive-lock deadlock is fixed.
- An API-process worker captures COP/incident/plan/approval snapshots, stores advice once per incident
  cycle, and publishes the existing `REASONING_COMPLETED` event. Pending publication recovers after
  restart using the same event id. A provider timeout, invalid JSON, missing key or exhausted budget
  produces grounded template advice. Only the latest completed cycle observed by the worker is
  analyzed; rapid replay may skip intermediate cycles.
- Exact snapshot grounding rejects invented subjects, changed values, missing citations and increased
  confidence. Display text is generated from validated claims; arbitrary model prose cannot claim
  something different. Rejected claims remain visible. Evidence snapshots include provenance,
  confidence and observation time. “Verified” means supported by that recorded snapshot, not verified
  in the physical world.
- Read-only `search_facts` and `graph_neighbors` tools. The latter exposes explicit references between
  known snapshot entities; it does not invent topology or replace Sumudu's domain graph.
- Natural-language questions through `POST /intelligence/ask`; offline mode uses keyword retrieval.
  Answers include accepted/rejected claims and evidence references.
- Field-report previews through `POST /intelligence/extract`: exact area ids/names/aliases, literal
  person counts, conservative English condition matching, unresolved questions. Models can suggest
  unresolved verbatim spans. Family counts are not converted to people; negation requires review.
  Extracted claims describe what was reported, remain unverified, and never update A5 automatically.
- `/manager/#/intelligence`: advice, evidence inspection, uncertainty, model questions, token/latency
  metadata, decision/validation traces, ask box and extraction preview. These traces record tools,
  evidence and checks, not a model's hidden chain of thought.
- Optional PlanU-inspired route comparison (`INTELLIGENCE_PLANNER=true`) using A4 options, A5 headcount
  and A3 group size. It uses A3's configuration in `config/reasoning.yaml`, checks snapshot version and
  expiry, and records a shadow comparison with shortest-feasible routing. It does not alter A3 actions.
- Incremental event delivery reads ledger batches after subscriber cursors rather than deserializing
  the entire history on every publish. Durable receipts still govern failure recovery and resume.

## Run locally

From the repository root:

```bash
source .venv/bin/activate
LLM_PROVIDER=none uvicorn atlas.api.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/manager/#/intelligence`. In **Playground**, replay the synthetic flood, then
return to **Intelligence**. For the persisted real-gauge scenario choose Ratnapura / Cyclone Ditwah.
No API key or radio is needed. The reference shelters, fleet and populations are demonstration data.

Offline terminal demos, including a deliberately bad fake model:

```bash
python -m atlas.intelligence.demo
python -m atlas.intelligence.demo --bad-model
python -m atlas.intelligence.demo --bad-model --scenario data/scenarios/ratnapura_ditwah_2025.json
python -m eval.run_reasoning --trials 30
```

`--bad-model` returns one supported claim and two rejected claims (invented entity and changed value
with a real citation). The CLI always uses an in-memory database and no network provider.

For a live model, put credentials in the git-ignored `.env`; do not commit them. Set
`LLM_PROVIDER=anthropic` with `ANTHROPIC_API_KEY`/`LLM_API_KEY`, or `LLM_PROVIDER=openai` with
`OPENAI_API_KEY`, and set `LLM_MODEL` to a model available to that account. The optional adapter follows
the [OpenAI Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create).
Live model calls were not exercised during this implementation; provider protocols were tested with
fakes and an HTTP mock transport.

Advisor requests have a 15-second overall timeout, up to 30 claims and 2,048 output tokens per model
round. Provider tool loops are bounded at seven rounds. Rate limits/retries use the existing
`LLM_REQUESTS_PER_MINUTE`, `LLM_MAX_RETRIES`, and `LLM_TIMEOUT_SECONDS` settings. The durable per-incident
budget defaults to four completion attempts (`INTELLIGENCE_MAX_CALLS_PER_INCIDENT`); failed attempts
also count. Completed-cycle advice is reused across restarts. Token usage covers successful provider
rounds; dollar cost is not estimated. Interactive queries have provider rate limits and timeouts,
but do not consume an incident's worker budget.

## API handoff

| Endpoint | Purpose |
|---|---|
| `GET /intelligence/advice?incident_id=...` | Latest 100 persisted cycle records |
| `GET /intelligence/evidence/{ref}` | Cited immutable snapshot with provenance |
| `POST /intelligence/ask` | `{ "question": "What is waiting for approval?" }` |
| `POST /intelligence/extract` | `{ "node_id": "operator", "text": "ratnapura-demo-river: 12 people stranded" }` |

Routes reuse `run:read` / `trace:read` permissions and existing token configuration. Report preview
never submits a field report; the team's field-ingestion contract remains an open integration item.

## Remaining integration limits

- Sumudu's `YamlOntology` and entity linker remain stubs. The intelligence layer ships with a strict
  **snapshot adapter**, never `PermissiveOntology`; full hazard/relation semantics, aliases and temporal
  graph impact queries still need the ontology workstream.
- The planner's independent-edge probabilities, static shelter capacity and synthetic reward model
  are illustrative. Enabling shadow comparison is not an operational forecast or automatic replan.
- Field extraction is deliberately conservative English parsing, with source claims for review.
  General multilingual extraction, trusted sender validation and submission to A5 remain separate work.
- LoRa decoding, signing, retry/ACK and physical range tests remain Hasindu's unfinished implementation.

See [verification report](../intelligence-verification.md) for what was actually run.

## Configure the LLM in the dashboard

Open **LLM settings** in the sidebar (`/manager/#/llm-settings`). Choose Offline, OpenAI or Anthropic;
enter the exact API model id and API key; choose thinking options; then **Save and apply**. New
advisor/query/extraction requests use the settings immediately. Existing persisted advice is retained.

- OpenAI settings use the Responses API and `reasoning.effort`. The existing lower-level legacy
  Chat Completions adapter remains available. The tool loop preserves encrypted reasoning items
  internally and does not expose them in traces.
- Anthropic settings support default, disabled, adaptive and manual-budget thinking. Manual budget
  must be at least 1,024 tokens and below the output token limit. Available modes/efforts depend on
  the model; the UI does not pretend every model supports every option.
- **Test connection** tests the draft settings using a short “OK” prompt, with no incident data or
  tools, and does not save changes. It verifies a completion, not every tool-calling capability.
- Blank API key keeps that provider's existing key. A new key replaces it. **Remove key** clears that
  provider's saved credential and disables its environment-key fallback. Credentials never transfer
  from one provider to the other.
- Configuration and testing require `llm:configure` (admin only when authentication is enabled).
  Without server authentication, the existing open development mode applies. Metadata reads use
  `settings:read`; keys are never returned by the API, reflected in validation errors, stored in
  browser storage, or included in the audit ledger.
- On disk-backed servers, settings persist in `.local/llm-settings.json`, ignored by Git and written
  atomically with owner-only file permissions (`0600`). This file is not encrypted; protect the local
  OS account and backups. `ATLAS_LLM_SETTINGS_PATH` can override its location. In-memory runtimes
  keep configuration in memory. Saved settings take precedence over environment defaults.
- Output token limit, overall request timeout and requests/minute are configurable. Output token
  caps include reasoning tokens. Saved profiles supersede the earlier fixed 15-second advisor and
  2,048-token defaults described above. The worker's per-incident call budget still applies.

Provider references: [OpenAI reasoning](https://developers.openai.com/api/docs/guides/reasoning),
[OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), and
[Anthropic thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking).
