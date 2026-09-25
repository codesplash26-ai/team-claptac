# Intelligence layer (Aeshan)

**Goal:** the system explains itself, questions its own plan, and reads free text, without ever putting
a model on the safety path or letting it assert an ungrounded fact.

## You own
`atlas/intelligence/`, `atlas/reasoning/` (the PlanU-style planner), `atlas/tools/llm.py`, and the
orchestration agents A2, A3, A5 (`atlas/agents/`, `atlas/orchestration/`, `docs/orchestration.md`),
which are Hasindu's original work ported onto the platform. Keep the platform (`atlas/core/`,
`atlas/memory/`, A1) too. *Assumed: please confirm you want A2, A3, A5.*

## Rules that do not bend
- No model call inside a bus handler (one transaction, one lock, a stalled call freezes everything).
- The model explains, questions and extracts. It never computes routes, scores, allocations or policy.
- Every factual statement is a `Claim` and passes `grounding.ground` before anyone sees it as fact.
- With no key, or the provider down, behaviour is unchanged (`advise_without_llm`).

## Tasks, in order
- [ ] `providers.py`: `AnthropicProvider` over `LLMClient`, optional `OpenAIProvider`, `provider_from_env`. Keys from `.env` only.
- [ ] `advisor.py`: the LLM advisor with read-only tools (incident, COP snapshot, plans, graph), producing `Advice`. Run it from a worker next to `ControlPlane.start_worker`, publish `REASONING_COMPLETED`.
- [ ] Wire `grounding.ground` to Sumudu's ontology (`PermissiveOntology` until it lands) and to a real `evidence_exists` over the ledger and COP.
- [ ] Reasoning traces: every step recorded so Dinuka's UI can show how the advice was reached.
- [ ] `nlquery.py`: ask-the-system over read-only tools; answers are grounded claims with evidence links.
- [ ] `extraction.py`: free-text field reports to `FieldReport` plus claims, unresolved phrases returned as questions.
- [ ] Wire `atlas/reasoning` (PlanU-style) where it earns its place (route choice under uncertainty) and measure it against the deterministic baseline.
- [ ] Uncertainty: surface `information_gaps` and low confidence in advice, and let advice recommend a replan (`recommend_replan`) that A3's own rules or a human accept.
- [ ] Cost and latency controls: rate limit, cache, per-incident budget; record tokens and latency in the trace.
- [ ] Tests with a fake provider: grounding rejects invented entities, advice never mutates state, no key means template advice.
- [ ] Bus performance: `EventBus._drain` rescans the whole ledger on each publish; fix before large replays.

## Use meanwhile
`PermissiveOntology` and `InMemoryWorldGraph` (Sumudu), a fake `LLMProvider` in tests.

## Done when
For a replayed flood, the UI shows advice with accepted claims linked to evidence and at least one
rejected claim from a deliberately bad fake model, and the loop still runs with no key set.
