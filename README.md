# ATLAS — Autonomous Triage, Logical Agentic System

## Aeshan's intelligence layer

The advisor, grounded questions, report previews and evidence viewer now run at
`/manager/#/intelligence`. Start with `LLM_PROVIDER=none` for an offline demo; replay a flood from
Playground to generate advice. Models run in a separate worker and cannot change actions.

Configure the provider, API key, model and reasoning/thinking options at
`/manager/#/llm-settings`. Test the connection, then save to apply it to new intelligence
requests. Settings persist locally; saved profiles override environment defaults.

```bash
python -m atlas.intelligence.demo --bad-model
```

See the [intelligence run guide](docs/workstreams/intelligence.md) and
[verified results and remaining team work](docs/intelligence-verification.md).

## A1 and shared planning implementation

The `Aeshan/A1-MCTS` work adds the local event bus, SQLite COP/ledger, structured
Sentinel, replay, sensor outbox, and optional PlanU-inspired planning. See
[the integration guide](docs/reasoning-integration.md) for scope, teammate adapters,
contract changes, configuration, and recovery limitations. The offline nine-agent and control loop is assembled in `atlas/system.py`; LoRa and the
domain ontology still have unfinished workstream modules.

Quick verification without hardware or model keys:

```bash
pip install -r requirements-a1.txt
python -m pytest -q
python -m atlas.ingest.simulator data/scenarios/flood.json
python -m eval.run_reasoning --trials 30
```

## Routing, shelter, logistics and evaluation (Workstream C)

The `Sumudu/C-Routing-and-Optimization` work adds the belief-weighted cognitive map ported from the
original engine, real OSM road networks with elevation (Colombo Fort, Ratnapura, Kandy), the
sensor-to-road mapping, **A4 Evacuation**, **A6 Logistics**, **A7 Shelter** (CP-SAT), and the A/B/C
evaluation. See [the integration guide](docs/routing-integration.md) for the interfaces other
workstreams code against, the evaluation findings, the demo script and the open decisions.

```bash
pip install -r requirements-c.txt
python -m pytest -q
python -m eval.run_abc                    # reproduces the proposal's results table (24/24)
python -m eval.run_abc --site ratnapura   # real network; --pipeline for compute latency
```

One result to read before the pitch: on the real hilly networks the original engine's uphill penalty
strands evacuees (Ratnapura flood: 61% reach a shelter against 100% for both baselines), so ATLAS's
planning agents turn it off. The engine's published grid results are unchanged and reproduce exactly.

> **Team ClapTac** · General Sir John Kotelawala Defence University
> CodeSplash '26 Agentic AI Phase · Theme 3: AI Disaster Response Coordinator

ATLAS is a disaster-response coordinator for rapid-onset disasters in Sri Lanka, such as floods, landslides and cyclones. It runs on its own and keeps its state between cycles. An always-on **Sentinel** agent watches incoming data. When something significant changes, it wakes an **Incident Commander**, which coordinates nine specialist agents. Every proposed action passes through a **Control Boundary**, and high-consequence actions need a human's sign-off.

The LLM does the orchestration and writes the text. Routes, allocations, priorities and risk scores come from deterministic tools that work on a **belief-weighted cognitive map**.

---

## What is running today

The whole loop runs end to end on a laptop, with no hardware and no API key: sensor readings go in, Sentinel and the specialist agents react, the Gateway gates every action, and a human answers the approvals.

| Area | What you get | Read more |
|---|---|---|
| **Agents A1–A9** | Sentinel, hazard, commander, evacuation, triage, logistics, shelter, dispatch and comms, all talking over one event bus. | [orchestration](docs/orchestration.md) |
| **Control boundary** | Deny-by-default policy, per-agent identity, an approvals queue, stale-plan refusal, idempotent execution, and a hash-chained ledger. | [control boundary](docs/control-boundary.md) |
| **Real data** | Adapters for flood, rainfall, soil, earthquake, tsunami and drought sources, with an offline cache. Includes a replay of Cyclone Ditwah (Nov 2025) at Ratnapura from the real river gauge. | [data and hazards](docs/data-and-hazards.md) |
| **Agent manager** | A console at `/manager/`: runs and traces, the agent graph, a live registry with suspend and resume, evaluations and monitors, experiments, guardrails and an audit trail. | below |
| **Command line** | `atlasctl` drives the same API: agents, traces, evaluations, approvals, scenarios, audit. | below |
| **Stand-out tools** | A red-team suite that attacks the boundary (16 attacks, all caught), a what-if replay that changes one thing and shows the difference, a latency meter, and an uncertainty map. | Playground |
| **LoRa link** | Planned: the field-node radio path, owned by the hardware workstream. | [roadmap](docs/roadmap.md) |

### The agent manager

```bash
pip install -r requirements.txt
uvicorn atlas.api.main:app --reload      # then open http://127.0.0.1:8000/  (redirects to /manager/)
```

- **Runs and traces.** Every incident is a trace: a tree of what each agent received, what it decided, and what the Gateway did with it. Traces follow the OpenTelemetry model and export as OTLP/JSON (`GET /am/v1/traces/{id}/otlp`), or push to a collector with `ATLAS_OTLP_ENDPOINT`.
- **Registry and lifecycle.** Each agent has an owner, version, tools, and the data it reads and writes. Suspend one and its work waits; resume it and the work is done in order. Nothing is lost, and the Gateway refuses any held plan that has gone stale.
- **Evaluations.** About eighteen checks score traces, at the level of a single span, a whole incident, or one agent. The critical ones are safety rules: nothing executes without authorisation, no plan is used after its data changed, and so on. Monitors run them on live traces on a schedule. Experiments replay a scenario with a change (a stricter policy, a lost sensor, a human who never answers) and report what regressed.
- **Access and audit.** Routes require a named permission. Roles are admin, commander, developer, ai_lead, platform_engineer and viewer, set with `ATLAS_TOKENS` (see `.env.example`). Every change is written to the ledger automatically, with who, what and the result, and never a secret.
- **API contract.** The API is described in [docs/manager_openapi.json](docs/manager_openapi.json), and a test fails if it drifts from the code. After changing an endpoint, run `python -m atlas.manager.openapi`.

```bash
python -m atlas.manager.cli agents list
python -m atlas.manager.cli scenario start flood --speed 60
python -m atlas.manager.cli traces list
python -m atlas.manager.cli eval run          # exits 2 on a critical safety failure, so CI can gate on it
```

### Working on the project

The code is split so each person has one place to work: see [where to find your work](#where-to-find-your-work) and [docs/workstreams](docs/workstreams). Changes to the shared contracts (`atlas/core/events.py`, the sensor format, `docs/contracts.md`) need everyone's approval.

---

## How it works

```
Feeds ──► A1 Sentinel ──(anomaly > threshold)──► TRIGGER
                                                   │
                         A2 Hazard & Vulnerability ┤
                                                   ▼
                              A3 Incident Commander (LLM)
                     ┌──────────┬─────────┬──────────┬──────────┐
                     ▼          ▼         ▼          ▼
                A4 Evacuation A5 Triage A6 Logistics A7 Shelter
                     └──────────┴────┬────┴──────────┘
                                     ▼  reconciled plan
                    ┌──── CONTROL BOUNDARY ────────────────────┐
                    │ Policy Engine → Action Gateway ──► Human │
                    │                     │   (high-consequence)│
                    └─────────────────────┼────────────────────┘
                                          ▼
                          A8 Dispatch      A9 Public Comms / SITREP
                                          │
                     Action Ledger → Knowledge Graph → COP → A1 (loop)
```

| Layer | Agents / components |
|---|---|
| Sense & Verify | A1 Sentinel (Multimodal Intake & Verification), A2 Hazard & Vulnerability Intelligence |
| Plan & Coordinate | A3 Incident Commander, A4 Evacuation & Access, A5 Rescue & Needs Triage, A6 Resource & Logistics, A7 Shelter & Relief |
| Act & Communicate | A8 Dispatch & Mission Execution, A9 Public Communications & SITREP |
| Control Boundary (not agents) | Policy & Authority Engine, Action Gateway, Human Incident Commander, Append-only Action Ledger |
| Shared memory | Common Operating Picture (COP), typed event bus, evidence store, temporal knowledge graph |

**Coordination rules**
- Agents never send messages to each other directly. They only use the event bus and the COP.
- Only A3 can delegate work. Specialist agents never delegate to each other.
- Only A8 and A9 carry out actions, and only after the Action Gateway authorizes them.
- Every action is written to the append-only ledger with an idempotency key, whether it was taken, blocked or escalated.

**Autonomy level:** Level 3 (high autonomy). Routine actions run and are logged. High-consequence actions, such as district-wide alerts, committing scarce resources or closing major routes, always go to a human.

---

## Tech stack

| Area | Tech |
|---|---|
| Language | Python 3.11+ |
| Cognitive map / routing | NetworkX, NumPy, SciPy, OSMnx, Open-Elevation |
| Geospatial | GeoPandas (PostGIS later) |
| Optimization | OR-Tools CP-SAT |
| LLM | LLM with tool-calling, used for orchestration and text only |
| Backend | FastAPI |
| Dashboard | Folium map + responder dashboard |
| Storage | SQLite for the COP and ledger (Postgres later) |
| Alerting | CAP 1.2; Sinhala, Tamil and English |
| Hardware | Raspberry Pi sensor node (water level, rainfall, BME280, soil moisture) over MQTT (Mosquitto) |
| Deploy | Docker on a cloud VM or lab server |

---

## Project structure

```
team-claptac/
├── atlas/
│   ├── core/              # Shared contracts: events, event bus, COP, config
│   │   ├── events.py      #   ← typed event + action schemas (team-wide contract)
│   │   ├── event_bus.py
│   │   ├── cop.py
│   │   └── config.py
│   ├── memory/            # Action ledger, temporal knowledge graph, evidence store
│   ├── agents/
│   │   ├── base.py        #   ← Agent base class
│   │   ├── a1_sentinel.py … a9_comms.py
│   ├── control/           # Policy engine, action gateway, human approval queue
│   ├── tools/
│   │   ├── cognitive_map/ #   ← existing belief-weighted routing engine goes here
│   │   ├── llm.py, geo.py, optimisation.py, cap.py, translation.py, ocr_stt.py
│   ├── ingest/            # Feed adapters, sensor ingest (MQTT/HTTP), scenario replayer
│   ├── api/               # FastAPI app
│   └── dashboard/         # Folium map + responder dashboard
├── hardware/              # Raspberry Pi sensor node
│   ├── pi/                #   sensor_node.py, sensor drivers, config, systemd unit
│   └── mosquitto/         #   MQTT broker config
├── config/policy.yaml     # Per-agent permissions and consequence thresholds
├── data/                  # osm/ graphs, scenarios/, reference/ (vulnerability, shelters)
├── eval/                  # Metrics + A/B/C scenario evaluation
├── tests/
├── docs/
├── requirements.txt
└── .env.example
```

---

## Where to find your work

| Workstream | Owner | Start here |
|---|---|---|
| Hardware and LoRa | Hasindu | [docs/workstreams/hardware-lora.md](docs/workstreams/hardware-lora.md), `hardware/lora/` |
| UI, data and integration | Dinuka | [docs/workstreams/ui-and-data.md](docs/workstreams/ui-and-data.md), `atlas/dashboard/`, `atlas/datasets/` |
| Intelligence layer | Aeshan | [docs/workstreams/intelligence.md](docs/workstreams/intelligence.md), `atlas/intelligence/` |
| Ontology and graph | Sumudu | [docs/workstreams/ontology-graph.md](docs/workstreams/ontology-graph.md), `atlas/knowledge/` |

The seams between them, with the mock to use until the other side exists, are in
[docs/contracts.md](docs/contracts.md). The order of work is in [docs/roadmap.md](docs/roadmap.md).
Anything not built yet raises `NotImplementedError` naming its owner.

## Getting started

```bash
git clone <repo-url> && cd team-claptac
python -m venv .venv
.venv\Scripts\activate          # Windows  (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
cp .env.example .env            # fill in keys
```

Run the API and the manager console:

```bash
uvicorn atlas.api.main:app --reload
```

To rebuild the console after editing `frontend/`: `cd frontend && npm install && npm run build` (the build is committed to `atlas/dashboard/static/manager/`).

Run the sensor pipeline (see [hardware/README.md](hardware/README.md) for Pi setup and wiring):

```bash
mosquitto -c hardware/mosquitto/mosquitto.conf
```

Run tests:

```bash
pytest
```

---

## Hardware sensor node

A Raspberry Pi with water-level, rainfall and environmental sensors publishes readings over MQTT. They enter ATLAS as **machine feeds** for A1 Sentinel. The Pi only measures; A1 decides whether a reading is an anomaly. For the demo, a tabletop flood model lets judges watch a rising water level trigger the full loop: Sentinel, then the Incident Commander, then rerouting, then the gated action. A mock mode produces the same messages, as a fallback if the hardware fails. Details: [hardware/README.md](hardware/README.md).

---

## Target metrics

| Metric | Target |
|---|---|
| Evacuation success rate | ≥ 90% (96% already achieved by the A4 core) |
| Avg. failed route attempts | < 1.5 |
| Detection-to-trigger latency | < 2 min |
| False trigger rate | < 10% |
| Tool-call success rate | ≥ 95% |
| Policy-violation catch rate | 100% in red-team tests |
| Replanning time | < 2 min |
| Human intervention on routine actions | < 15% |

---

## Contributing

- Branch from `main` as `<area>/<short-desc>`, for example `agents/a1-anomaly-scoring`.
- Open a PR that at least one teammate reviews. Any change to `atlas/core/events.py` needs the whole team to agree.
- Each module owner writes the tests for their module. See [TEAM_PLAN.md](TEAM_PLAN.md) for who owns what.

## Team

Dinuka Wickramasinghe (lead) · Sumudu Ratnayake · Hasindu Viduranga · Aeshan Rosa
