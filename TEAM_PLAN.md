# ATLAS Team Plan

The work is split into **four workstreams**, one per person. Each owner builds their modules and writes the tests for them. They also demo their part at each weekly sync.

> The names below are a starting suggestion. Swap them based on who knows what, especially if someone already built the Agent 4 cognitive-map engine. That person should take **Workstream C**.

| Workstream | Owner | Owns |
|---|---|---|
| **A — Platform & Sensing** | Sumudu Ratnayake | `core/`, `memory/`, `ingest/`, A1 Sentinel, A2 Hazard Intel |
| **B — Orchestration & Triage** | Dinuka Wickramasinghe (lead) | `tools/llm.py`, A3 Incident Commander, A5 Triage, end-to-end integration |
| **C — Routing & Optimization** | Hasindu Viduranga | `tools/cognitive_map/`, `tools/geo.py`, `tools/optimisation.py`, A4 Evacuation, A6 Logistics, A7 Shelter, `eval/` |
| **D — Control Boundary & Action** | Aeshan Rosa | `control/`, `config/policy.yaml`, A8 Dispatch, A9 Comms, `tools/cap.py`, `tools/translation.py`, `api/`, `dashboard/` |

---

## Workstream details

### A — Platform & Sensing
The foundation the other three workstreams build on. Ship it first.
- [ ] Freeze `core/events.py` contracts together with the team (Week 0)
- [ ] In-process async event bus, which can later be swapped for Redis or NATS
- [ ] Common Operating Picture on SQLite: beliefs with confidence, provenance and TTL
- [ ] Append-only Action Ledger with idempotency keys and receipts
- [ ] Temporal knowledge graph that projects ledger events (NetworkX to start)
- [ ] Feed adapters plus a **scenario replayer** that plays DMC warnings, river sensors and field reports from `data/scenarios/`
- [ ] **A1 Sentinel**: normalize, deduplicate, corroborate, score confidence, rolling anomaly score, emit a trigger when the score crosses the threshold, flag conflicts
- [ ] **A2 Hazard Intel**: risk-zone layer, overlay with vulnerable populations, information-gap reports

### B — Orchestration & Triage
The agent's reasoning core and the person who makes the whole thing run end to end.
- [ ] LLM client wrapper: tool-calling, retries, rate limiting, prompt logging
- [ ] **A3 Incident Commander**: wake on a trigger, assess, delegate to A4–A7 as LLM tools, reconcile candidate plans (for example, a route that leads to a full shelter), submit to the Gateway, decide when to replan, return to standby
- [ ] Handle several incidents at once, each running its own cycle
- [ ] **A5 Triage**: transparent scoring of urgency × vulnerability, continuous re-ranking, explanations
- [ ] Fallback: if the LLM is down, keep A4 routing running without it
- [ ] Owns the **end-to-end demo script** and integration tests

### C — Routing & Optimization
The most mature part of the system. Bring the existing engine in, then extend it.
- [ ] Port the existing simulation core into `tools/cognitive_map/` (beliefs.py, routing.py)
- [ ] Load and cache the OSM graphs for Colombo Fort, Ratnapura and Kandy, with elevation, in `data/osm/`
- [ ] **A4 Evacuation**: wrap the engine as an agent and tool; routes, alternatives, per-route risk and uncertainty; proactive replanning
- [ ] **A6 Logistics**: resource inventory in the COP, CP-SAT model matching resources to tasks, shortage alerts
- [ ] **A7 Shelter**: capacity and reservation state, CP-SAT shelter assignment, resupply requests to A6
- [ ] `eval/`: re-run the A/B/C scenarios against the baselines and add the section 8 metrics

### D — Control Boundary & Action
The safety story, and the part the judges will look at.
- [ ] **Policy & Authority Engine**: load `policy.yaml`, check each agent's role scope, classify consequence
- [ ] **Action Gateway**: preflight → authorize, escalate or block → execute with verify, retry and compensate → write to the ledger
- [ ] **Human approval queue**: approve, edit or reject, exposed through the API
- [ ] **A8 Dispatch**: mission tickets, acknowledgement and progress tracking, exception reports
- [ ] **A9 Comms**: CAP 1.2 XML, translation into Sinhala, Tamil and English, periodic SITREPs, cancellation notices
- [ ] **FastAPI** endpoints: incidents, approvals, ledger, missions, alerts
- [ ] **Dashboard**: Folium map of hazards and routes, agent activity feed, pending approvals, mission board
- [ ] Red-team tests: 100% of out-of-scope actions blocked

---

## Timeline

It follows the proposal's roadmap, with the phases run in parallel. Adjust the week lengths to fit the competition deadline.

| Week | A — Platform & Sensing | B — Orchestration | C — Routing & Optimization | D — Control & Action | Team milestone |
|---|---|---|---|---|---|
| **0** | Draft event contracts | LLM provider chosen, API key | Audit existing engine | Draft policy.yaml | **Contracts frozen**, repo set up |
| **1** | Event bus, COP, ledger | LLM wrapper, A3 skeleton with mock tools | Port the engine; A4 as a tool | Policy engine, Gateway skeleton | — |
| **2** | A1 Sentinel + scenario replayer | A3 calls A4 on a real trigger | OSM graphs for 3 sites | Gateway + ledger writes + approval queue | **Phase 1+2:** trigger → A3 → A4 → Gateway → ledger |
| **3** | A2 Hazard Intel, knowledge graph | A5 Triage, plan reconciliation | A6 Logistics, A7 Shelter (CP-SAT) | A8 Dispatch, FastAPI endpoints | **Phase 3:** all planning agents live |
| **4** | Feed hardening, dedup tuning | Multi-incident cycles, LLM fallback | Eval harness + metrics | A9 Comms (CAP + translation), dashboard | **Phase 4:** full 9-agent loop |
| **5** | Bug fixes, latency metrics | End-to-end demo script | A/B/C evaluation runs | Red-team tests, dashboard polish | **Phase 5:** demo, Dockerfile, reproducibility guide |

## Dependencies and handoffs

- **A → everyone:** event contracts and the COP interface are the first blocker. Freeze them in Week 0, and until then each workstream uses in-memory mocks.
- **C → B:** A4 exposes `plan_evacuation(origin, destinations) -> RoutePlan`. B can start against a stub that returns fixed data.
- **D → B:** the Gateway exposes `submit(ProposedAction) -> Decision`. B can use a stub that approves everything until D is ready.
- **A → D:** the ledger API, which the Gateway writes to.
- **C ↔ A:** A6 and A7 read state from the COP, and A4 writes road beliefs to it.

## Working agreements

- **Daily:** a 10-minute async check-in in the group chat covering what you did, what's next and what's blocking you.
- **Weekly sync:** each owner gives a live demo. Merge to `main` only when the end-to-end smoke test passes.
- **PRs:** at least one reviewer, small and frequent. Changes to `events.py` need everyone's approval.
- **Definition of done:** the code, tests, a short docstring on the module's inputs and outputs, and the code runs from the scenario replayer.

## Decisions to make in Week 0

1. **Backend:** the proposal names both Flask (section 6.2) and FastAPI (section 6.1). This skeleton uses FastAPI for the API and Folium for the maps. Decide whether to reuse the existing Flask demo or migrate it.
2. **LLM provider and model**, plus the budget and rate limits.
3. **Translation:** use the LLM or a dedicated API for Sinhala and Tamil.
4. **Demo scenario:** a flood in Ratnapura is the suggested default.
