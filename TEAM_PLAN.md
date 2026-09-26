# ATLAS Team Plan

## Aeshan/A1-MCTS implementation and proposed integration handoff

Workstream A now also supplies `atlas/reasoning/`, the reusable PlanU-inspired
search engine. The original owners retain their agents. A1's adapter is included;
the A3 adapter is an integration example, not a completed Incident Commander.

- Implemented on this branch: local runtime/event delivery, versioned SQLite COP,
  hash-linked append-only ledger, internal evidence store, rebuildable graph,
  structured A1 rules, replay, HTTP/MQTT wiring, planner modes/adapters, tests,
  synthetic evaluation, and persistent Pi outbox.
- Team review needed: raw versus validated observation events, optional measurement
  IDs, planning snapshot references, and subscriber registration interfaces.
- B integrates A3 and the shared LLM wrapper; C replaces illustrative outcome
  assumptions with domain models and evaluates them; D retains final policy,
  approval and execution authority.
- Physical calibration, live-broker/Pi drills, OCR/STT, and the complete nine-agent
  loop are not claimed complete by this software implementation.

See [reasoning integration](docs/reasoning-integration.md). The original checklist
below remains the team's roadmap; these additions do not silently mark other
workstreams complete or replace their ownership.

The work is split into **four workstreams**, one per person. Each owner builds their modules and writes the tests for them. They also demo their part at each weekly sync.

| Workstream | Owner | Owns |
|---|---|---|
| **A — Platform, Sensing & Hardware** | Aeshan Rosa | `core/`, `memory/`, `ingest/`, `hardware/`, A1 Sentinel |
| **B — Orchestration & Intelligence** | Hasindu Viduranga | `tools/llm.py`, A2 Hazard Intel, A3 Incident Commander, A5 Triage, end-to-end integration |
| **C — Routing & Optimization** | Sumudu Ratnayake | `tools/cognitive_map/`, `tools/geo.py`, `tools/optimisation.py`, A4 Evacuation, A6 Logistics, A7 Shelter, `eval/` |
| **D — Control Boundary & Action** | Dinuka Wickramasinghe (team lead) | `control/`, `config/policy.yaml`, A8 Dispatch, A9 Comms, `tools/cap.py`, `tools/translation.py`, `api/`, `dashboard/` |

### What changed with the hardware addition

- **A takes the Raspberry Pi work.** A already feeds A1, so the sensor pipeline stays in one person's hands, from the physical sensor to the trigger.
- **A2 Hazard Intel moves from A to B** to make room for the hardware. It fits well with A5 Triage, since A2's vulnerability signals feed straight into A5.
- **C maps sensors onto the road network**: a node's `area_id` / lat-lon becomes an OSM edge or area in the COP.
- **D adds a live sensor panel** to the dashboard.
- **Everyone helps build the tabletop flood model** in Week 4.

---

## Workstream details

### A — Platform, Sensing & Hardware (Aeshan)
The foundation the other three workstreams build on, plus the physical sensor node. Ship the platform first.

**Platform**
- [ ] Freeze `core/events.py` contracts together with the team (Week 0)
- [ ] In-process async event bus, which can later be swapped for Redis or NATS
- [ ] Common Operating Picture on SQLite: beliefs with confidence, provenance and TTL
- [ ] Append-only Action Ledger with idempotency keys and receipts
- [ ] Temporal knowledge graph that projects ledger events (NetworkX to start)
- [ ] Feed adapters plus a **scenario replayer** that plays DMC warnings, river sensors and field reports from `data/scenarios/`

**A1 Sentinel**
- [ ] Normalize, deduplicate, corroborate, score confidence
- [ ] Rolling anomaly score; emit a trigger when the score crosses the threshold; flag conflicting reports
- [ ] Sensor rules: water-level threshold and rate of rise, rainfall accumulation, soil saturation
- [ ] Give each sensor a reliability weight so that one faulty sensor alone can't fire a trigger unless nothing contradicts it

**Hardware** (`hardware/`; the starter code is already in place)
- [ ] Order parts in **Week 0** (see the parts list in `hardware/README.md`)
- [ ] Set up the Pi OS, enable I2C, bench-test each sensor on its own
- [ ] Calibrate: ultrasonic mount height, rain gauge mm per tip, soil sensor dry and wet voltages
- [ ] Run Mosquitto on the demo laptop; Pi → MQTT → `atlas/ingest/sensors.py` → bus
- [ ] Start on boot with systemd, check offline buffering (unplug Wi-Fi, confirm the readings flush later)
- [ ] Lead the **tabletop flood model** build and the demo-day fallback, switching to `mock` + `flood_ramp`

### B — Orchestration & Intelligence (Hasindu)
The agent's reasoning core and the person who makes the whole thing run end to end.
- [ ] LLM client wrapper: tool-calling, retries, rate limiting, prompt logging
- [ ] **A2 Hazard Intel**: risk-zone layer from observations (including sensor readings), overlay with vulnerable populations, information-gap reports
- [ ] **A3 Incident Commander**: wake on a trigger, assess, delegate to A4–A7 as LLM tools, reconcile candidate plans (for example, a route that leads to a full shelter), submit to the Gateway, decide when to replan, return to standby
- [ ] Handle several incidents at once, each running its own cycle
- [ ] **A5 Triage**: transparent scoring of urgency × vulnerability, continuous re-ranking, explanations
- [ ] Fallback: if the LLM is down, keep A4 routing running without it
- [ ] Owns the **end-to-end demo script** and integration tests

### C — Routing & Optimization (Sumudu)
The most mature part of the system. Bring the existing engine in, then extend it.
Implemented on `Sumudu/C-Routing-and-Optimization`; see [routing integration](docs/routing-integration.md).
- [x] Port the existing simulation core into `tools/cognitive_map/` (beliefs.py, routing.py). Reproduces all 60 original episodes exactly.
- [x] Load and cache the OSM graphs for Colombo Fort, Ratnapura and Kandy, with elevation, in `data/osm/`. Elevation is OpenTopoData SRTM (Open-Elevation's certificate has expired).
- [x] **Sensor-to-map mapping**: an `area_id` maps to a gauge on the road network; water-level readings become belief updates on the roads it floods (demo scale, `data/reference/sensor_map.yaml`)
- [x] **A4 Evacuation**: agent and tool; ranked safe routes with per-route risk and uncertainty; `RISK_UPDATE` when roads close or reopen so A3 can replan
- [x] **A6 Logistics**: CP-SAT matching of teams and vehicles to jobs, shortage alerts. Fleet is reference data; committed units are recorded in the COP.
- [~] **A7 Shelter**: CP-SAT shelter assignment, occupancy in the COP, resupply requests in the plan for A3 to hand to A6. **Not done:** nothing yet records occupancy or unit availability when missions are created (needs a headcount on A8's `create_mission`; open with Dinuka and Hasindu).
- [x] `eval/`: A/B/C scenarios against the baselines on the grids and all three real networks, section 8 metrics, and a proposal-table gate (`python -m eval.run_abc`). Latency measured is compute time after the trigger (about 47 ms); A1's detection latency is A's.
- [x] **Ontology and world knowledge graph**: vocabulary and validator that rejects unknown entities, units and values; a world graph of roads, shelters, gauges, missions and incidents; impact and neighbourhood queries; a guard against invented entities in claims and prose; an evidence-existence check for grounding; read-only `/knowledge` routes. See [knowledge graph](docs/knowledge-graph.md).

**Findings to act on before the pitch** (details in the integration guide):
- [ ] On real hilly networks the original engine's uphill penalty strands evacuees (Ratnapura flood: 61% success vs 100%). ATLAS's planning agents turn it off. Confirm this, and how the pitch describes "elevation-aware travel time".
- [ ] On the real networks the belief agent matches the naive baseline rather than beating it; the "robust to misinformation" claim rests on the synthetic grid results. Decide how the pitch frames this.
- [ ] Hasindu: confirm the A3 to A4/A6/A7 payloads in `atlas/agents/contracts.py`.

### D — Control Boundary & Action (Dinuka)
The safety story, and the part the judges will look at.
- [ ] **Policy & Authority Engine**: load `policy.yaml`, check each agent's role scope, classify consequence
- [ ] **Action Gateway**: preflight → authorize, escalate or block → execute with verify, retry and compensate → write to the ledger
- [ ] **Human approval queue**: approve, edit or reject, exposed through the API
- [ ] **A8 Dispatch**: mission tickets, acknowledgement and progress tracking, exception reports
- [ ] **A9 Comms**: CAP 1.2 XML, translation into Sinhala, Tamil and English, periodic SITREPs, cancellation notices
- [ ] **FastAPI** endpoints: incidents, approvals, ledger, missions, alerts (`/ingest/sensor` already exists)
- [ ] **Dashboard**: Folium map of hazards and routes, agent activity feed, pending approvals, mission board, **live sensor panel** (current value, sparkline, threshold line, node online/offline)
- [ ] Red-team tests: 100% of out-of-scope actions blocked

---

## Timeline

It follows the proposal's roadmap, with the phases run in parallel. Adjust the week lengths to fit the competition deadline.

| Week | A — Aeshan | B — Hasindu | C — Sumudu | D — Dinuka | Team milestone |
|---|---|---|---|---|---|
| **0** | Draft event contracts; **order hardware parts** | LLM provider chosen, API key | Audit existing engine | Draft policy.yaml | **Contracts frozen**, repo set up, parts ordered |
| **1** | Event bus, COP, ledger; Pi OS setup, sensor bench tests | LLM wrapper, A3 skeleton with mock tools | Port the engine; A4 as a tool | Policy engine, Gateway skeleton | — |
| **2** | A1 Sentinel + scenario replayer; Pi → MQTT → ingest live | A3 calls A4 on a real trigger | OSM graphs for 3 sites; sensor-to-edge mapping | Gateway + ledger writes + approval queue | **Phase 1+2:** trigger → A3 → A4 → Gateway → ledger |
| **3** | Knowledge graph; A1 sensor rules + calibration | A2 Hazard Intel, A5 Triage, plan reconciliation | A6 Logistics, A7 Shelter (CP-SAT) | A8 Dispatch, FastAPI endpoints | **Phase 3:** all planning agents live; **real sensor fires a real trigger** |
| **4** | Feed hardening; systemd + offline buffering; **tabletop model build** (all help) | Multi-incident cycles, LLM fallback | Eval harness + metrics | A9 Comms (CAP + translation), dashboard + sensor panel | **Phase 4:** full 9-agent loop driven by hardware |
| **5** | Latency metrics; demo fallback drill | End-to-end demo script | A/B/C evaluation runs | Red-team tests, dashboard polish | **Phase 5:** demo, Dockerfile, reproducibility guide |

## Dependencies and handoffs

- **A → everyone:** event contracts and the COP interface are the first blocker. Freeze them in Week 0, and until then each workstream uses in-memory mocks.
- **A → C:** the sensor reading format is `atlas/ingest/sensors.py::SensorReading`, which is already defined. C can build the sensor-to-edge mapping against `mock` readings before the hardware arrives.
- **A → D:** the ledger API, which the Gateway writes to, and the sensor event stream for the dashboard panel.
- **C → B:** A4 exposes `plan_evacuation(origin, destinations) -> RoutePlan`. B can start against a stub that returns fixed data.
- **D → B:** the Gateway exposes `submit(ProposedAction) -> Decision`. B can use a stub that approves everything until D is ready.
- **Hardware is never a blocker.** Until the parts arrive, everyone uses `hardware/pi/sensor_node.py` with `driver: mock`. It sends exactly the same messages.

## Working agreements

- **Daily:** a 10-minute async check-in in the group chat covering what you did, what's next and what's blocking you.
- **Weekly sync:** each owner gives a live demo. Merge to `main` only when the end-to-end smoke test passes.
- **PRs:** at least one reviewer, small and frequent. Changes to `events.py` or the sensor message format need everyone's approval.
- **Definition of done:** the code, tests, a short docstring on the module's inputs and outputs, and the code runs from the scenario replayer (and from live sensors, where relevant).

## Risks specific to the hardware

| Risk | Mitigation |
|---|---|
| Parts arrive late | Order in Week 0; use mock mode until they arrive |
| A 5V echo pin damages the Pi | Voltage divider on ECHO (see the wiring table) |
| Wi-Fi at the venue is unreliable | Run the broker on the demo laptop and connect it and the Pi through a phone hotspot or a travel router |
| Hardware fails on demo day | Switch the node to `mock` + `flood_ramp`; the same pipeline, the same story |
| Water near electronics | Only the waterproof probe goes near the container; keep the Pi and breadboard in a box, well away |

## Decisions to make in Week 0

1. **Backend:** the proposal names both Flask (section 6.2) and FastAPI (section 6.1). This skeleton uses FastAPI for the API and Folium for the maps. Decide whether to reuse the existing Flask demo or migrate it.
2. **LLM provider and model**, plus the budget and rate limits.
3. **Translation:** use the LLM or a dedicated API for Sinhala and Tamil.
4. **Demo scenario:** a flood in Ratnapura is the suggested default, and it fits the water-level sensor.
5. **Which sensors to use:** water level + rain gauge + BME280 is the minimum. Add soil moisture only if there's time left in Week 3.
6. **Proposal update:** mention the hardware node in the final pitch as an addition to "machine feeds (weather stations, river-level sensors)". The guidelines ask you to explain any deviation from the proposal.
