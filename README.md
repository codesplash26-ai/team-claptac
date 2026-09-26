# ATLAS — Autonomous Triage, Logical Agentic System

> **Team ClapTac** · General Sir John Kotelawala Defence University
> CodeSplash '26 Agentic AI Phase · Theme 3: AI Disaster Response Coordinator

ATLAS helps a disaster-management office run the first hours of a flood or landslide in Sri Lanka.
It watches river gauges, rainfall, soil moisture and field reports. When something significant
happens it opens an incident, works out who is at risk, plans evacuation routes and shelter places,
assigns rescue teams and vehicles, and drafts public alerts in Sinhala, Tamil and English.

It acts on its own for routine work and **asks a person before anything reaches the public or
commits scarce teams**. Every decision, by an agent or a person, is kept in a record that cannot be
altered.

It runs on a laptop with no internet, no hardware and no AI key. Real recorded disasters (Cyclone
Ditwah at Ratnapura, November 2025) can be replayed as practice drills.

---

## Try it in two minutes

```bash
pip install -r requirements.txt
uvicorn atlas.api.main:app            # then open http://127.0.0.1:8000/
```

1. Open **Practice drills** and start *Ratnapura, Cyclone Ditwah floods, November 2025*.
2. Watch the **Overview** and the **Map**: the river rises past the danger line and an incident opens.
3. Go to **Decisions**: ATLAS asks before sending the evacuation alert. Type your name and approve.
4. See the alert, in three languages, under **Alerts & reports**.

A step-by-step demo script, with what each screen shows, is in [docs/demo-guide.md](docs/demo-guide.md).

---

## The console

The console has two parts. The first is for the people running the response and is written in
plain language. The second is for the people building and checking the system.

**Operations** (what a district officer uses)

| Page | What it is for |
|---|---|
| **Overview** | What is happening now, and what needs you: open incidents, decisions waiting, missions, sensors against their danger lines, the latest situation report. |
| **Map** | A live 3D view of the area: streets or satellite, hills in 3D, live rain radar, roads coloured by how likely they are to be passable, evacuation routes, shelters, teams and sensors. Click anything to follow it. A guided tour flies through the situation, and a fly-along follows an evacuation route. |
| **Decisions** | Actions ATLAS will not take without a person: what it wants to do, why, and why it needs you. Approve or reject; your name is recorded. |
| **Alerts & reports** | Public alerts that went out (Sinhala, Tamil, English, and a standard CAP file for warning systems) and the situation reports for the operations room. |
| **Teams & missions** | Work sent to rescue teams and vehicles, with buttons to record what a team reports by radio. |
| **Ask ATLAS** | Questions in plain words. Answers come only from what the system has recorded; anything it cannot back up is struck out. |
| **Practice drills** | Replay a real past disaster, and a one-click test of the safety rules. |

**For technical staff** (collapsed by default): runs and traces, the event timeline, the agent graph,
the agents (with pause and resume), AI advice with evidence, automatic evaluations, ten benchmark
exercises, the playground (what-if and red team), metrics, the safety rules, the audit log, data
sources and AI model settings.

A command line drives the same API:

```bash
python -m atlas.manager.cli agents list
python -m atlas.manager.cli scenario start ratnapura_ditwah_2025 --speed 30
python -m atlas.manager.cli approvals list
python -m atlas.manager.cli eval run          # exits 2 on a critical safety failure, so CI can gate on it
```

---

## How it works

```
 Sensors (Pi / LoRa), weather and river feeds, field reports
        │
        ▼
 A1 Sentinel ── checks, de-duplicates, scores ──► trigger
        │
        ▼
 A2 Hazard & vulnerability ──► A3 Incident Commander (the only agent that delegates)
                                   │ asks, in order
                     ┌─────────────┼──────────────┬──────────────┐
                     ▼             ▼              ▼              ▼
              A5 Triage     A4 Evacuation   A7 Shelter    A6 Logistics
                     └─────────────┴──────┬───────┴──────────────┘
                                          ▼ one reconciled plan
                    ┌──────────── CONTROL BOUNDARY ─────────────┐
                    │ Policy engine → Action Gateway → a person │
                    │  (anything high-consequence waits here)   │
                    └──────────────────────┬────────────────────┘
                                           ▼
                        A8 Dispatch (missions)   A9 Public alerts and reports
                                           │
      Ledger (append-only, hash-chained) · Common operating picture · Knowledge graph
                                           │
                  Intelligence advisor (reads only; every claim checked against the records)
```

- **Agents never message each other directly.** They publish typed events on one bus; every event,
  its cause and its timing are recorded, so any decision can be traced back to the reading that
  started it.
- **Only A3 delegates; only A8 and A9 act, and only after the Gateway authorises.** Every action has
  an idempotency key, so a retry never sends the same order twice.
- **Numbers come from tools, not from a language model.** Routes (NetworkX on real OpenStreetMap
  roads with elevation), shelter placement (OR-Tools CP-SAT), priorities and risk levels are
  deterministic and auditable. The AI model, when one is configured, only advises and writes text,
  and runs outside the decision path.
- **The system replans** when new evidence arrives, within a bounded number of retries, then
  escalates to a person instead of looping.

**Autonomy: level 3.** Routine work runs and is logged. A person must approve every evacuation or
district-wide alert, any mission that commits more than two teams or vehicles, cancelling a
mission, and closing a major road. A missing or malformed detail counts as high-consequence.

---

## What is built

| Area | What it does | Read more |
|---|---|---|
| **Agents A1–A9** | The full loop above, persistent across restarts, with per-incident state and bounded replanning. | [orchestration](docs/orchestration.md) |
| **Control boundary** | Deny-by-default policy, per-agent identity, approvals, refusal of plans built on out-of-date information, idempotent execution, hash-chained ledger. | [control boundary](docs/control-boundary.md) |
| **Knowledge graph and ontology** | A typed world graph of roads, junctions, shelters, gauges, missions, groups and incidents. It rejects claims about things that do not exist, and answers "what depends on this road?". | [knowledge graph](docs/knowledge-graph.md) |
| **Intelligence** | Advice after each planning round, questions in plain words and field-report extraction. Every claim is checked against recorded evidence, and sentences naming unknown places are flagged. Works offline; OpenAI or Anthropic can be configured. | [intelligence](docs/workstreams/intelligence.md) |
| **Real data** | River, rainfall and soil sources with an offline cache, plus history for earthquakes, tsunami and drought. Two replays of Cyclone Ditwah at Ratnapura from real records. | [data and hazards](docs/data-and-hazards.md) |
| **Field sensors and LoRa** | A Raspberry Pi node over MQTT, and a LoRa radio link (SX1278) whose signed frames become the same sensor readings. Bad signatures and replays are dropped. A mock radio for demos. | [hardware](hardware/README.md), [LoRa](hardware/lora/README.md) |
| **Public alerts** | CAP 1.2 alerts from fixed, checked phrasing in Sinhala, Tamil and English. | `atlas/agents/a9_comms.py` |
| **Observability** | Every incident is a trace (OpenTelemetry model, OTLP export); an event timeline; about eighteen automatic evaluators, with the safety-critical ones gating CI. | [manager API](docs/manager_openapi.json) |
| **Testing tools** | A red-team suite (16 attempts to break the rules), what-if replays, ten seeded benchmark exercises, and the A/B/C routing evaluation. | [benchmarks](docs/simulation-benchmark.md) |
| **Access and audit** | Named permissions per route; roles admin, commander, developer, ai_lead, platform_engineer, viewer; every change is audited automatically, never with a secret. | `.env.example` |

### Results we can reproduce

| Check | Result | Command |
|---|---|---|
| Automated tests | 1,049 pass | `pytest` |
| Red team: attempts to bypass the rules | 16 of 16 handled correctly | Practice drills → Test the safety rules |
| Benchmark exercises (10 tasks × 3 seeds) | 30 of 30 pass | `python -m atlas.benchmark run --seeds 42,43,44` |
| Proposal's routing results table | 24 of 24 numbers reproduced | `python -m eval.run_abc` |
| Safety evaluators on a Ditwah replay | 34 of 34 checks pass, 0 critical failures | Technical → Evaluations |

Numbers on the benchmark and drills come from real agents running on synthetic or replayed data with
scripted human and field responses. They show the software behaves as designed; they are not a
field trial.

---

## Proposal and what we built

We built what the proposal describes: a perceive, reason, plan, validate and communicate loop on the
Ratnapura road network, with a live map, situation reports, evacuation alerts and a human-approval
gate. Where we changed the technology, this is why:

| Proposal | Built | Why |
|---|---|---|
| Six agents | Nine agents plus a control boundary | Planning split into evacuation, triage, logistics and shelter, each checkable on its own (the proposal's section 11 anticipated this). |
| Flask + Folium dashboard | FastAPI + a React console with a MapLibre 3D map; the Folium responder page is still at `/dashboard` | Typed API contracts, live updates and a map usable by non-technical staff. |
| RDFLib / OWL ontology | A YAML ontology validated at load time, over a NetworkX world graph | The same checks (types, relations, units, evidence) with fewer moving parts; migration to a graph store remains possible. |
| LangGraph orchestration | A small asyncio event bus with recorded causes | The proposal named a custom orchestrator as the fallback. Ours makes every hand-off persistent and replayable, which the audit trail needs. |
| LLM on the reasoning path | Deterministic tools decide; the AI model advises and writes text, checked against records | Works with no internet or key, and a model error cannot move a vehicle. |
| Vector database | Not needed yet | Reports are short and structured enough to extract directly. |
| — | LoRa field link, real Ditwah replays, red team, benchmarks, 3D map | Added during development. |

---

## Configuration

Copy `.env.example` to `.env`. Nothing is required for the demo. The main settings:

| Setting | Purpose |
|---|---|
| `ATLAS_SITE` | Which road network and shelters to load (`ratnapura`, `kandy`, `colombo_fort`). |
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_MODEL` | Optional AI model for advice and text; can also be set in the console (Technical → AI model settings). |
| `ATLAS_TOKENS` | Access codes and roles. With none set the console runs in open demo mode. |
| `ATLAS_OTLP_ENDPOINT` | Send traces to any OpenTelemetry collector. |
| `LORA_HMAC_KEY` | Shared key for signed LoRa frames. Never commit a real one. |

The map uses public, keyless services: OpenStreetMap tiles, Esri World Imagery, AWS open terrain
tiles and RainViewer radar. With no internet, the ATLAS layers (roads, routes, shelters, teams,
sensors) still draw.

---

## Project structure

```
atlas/
  core/          events (team contract), event bus, COP, config
  agents/        A1 Sentinel … A9 Comms
  orchestration/ A3's reconciliation and planning helpers
  control/       policy engine, Action Gateway, approvals
  knowledge/     ontology, world graph, entity guard, evidence index
  intelligence/  advisor, questions, extraction, providers and settings
  ingest/        sensors (MQTT/HTTP), LoRa gateway, scenario replayer
  datasets/      real data adapters and cache
  manager/       registry, traces, evaluations, access, audit, CLI
  benchmark/     seeded benchmark exercises
  api/           FastAPI routes
  dashboard/     developer views and the built console (static/manager)
frontend/        the console (React + TypeScript + MapLibre)
hardware/        Raspberry Pi node, LoRa node and gateway
config/          policy.yaml, ontology.yaml, sentinel.yaml, reasoning.yaml
data/            road networks, scenarios, reference data, benchmarks
eval/            A/B/C routing evaluation
docs/            design, contracts, workstreams, demo guide
tests/
```

## Where to find your work

| Workstream | Owner | Start here |
|---|---|---|
| Hardware and LoRa | Hasindu | [docs/workstreams/hardware-lora.md](docs/workstreams/hardware-lora.md), `hardware/lora/` |
| UI, data, control boundary and integration | Dinuka | [docs/workstreams/ui-and-data.md](docs/workstreams/ui-and-data.md), `frontend/`, `atlas/control/`, `atlas/datasets/` |
| Intelligence layer and platform | Aeshan | [docs/workstreams/intelligence.md](docs/workstreams/intelligence.md), `atlas/intelligence/`, `atlas/core/` |
| Ontology, graph and routing | Sumudu | [docs/workstreams/ontology-graph.md](docs/workstreams/ontology-graph.md), `atlas/knowledge/`, [routing](docs/routing-integration.md) |

The seams between workstreams are in [docs/contracts.md](docs/contracts.md); what is left is in
[docs/roadmap.md](docs/roadmap.md).

## Development

```bash
pytest                                        # the whole suite
ruff check atlas tests
cd frontend && npm install && npm run build   # rebuilds the console into atlas/dashboard/static/manager/
python -m atlas.manager.openapi               # after changing a manager endpoint (a test checks it)
```

- Branch as `<Name>/<Area>` (for example `Dinuka/Console-Polish`) and open a PR for review.
- Changes to `atlas/core/events.py`, the sensor format or `docs/contracts.md` need the whole team.
- Each owner writes the tests for their module. See [TEAM_PLAN.md](TEAM_PLAN.md).

## Team

Dinuka Wickramasinghe (lead) · Sumudu Ratnayake · Hasindu Viduranga · Aeshan Rosa
