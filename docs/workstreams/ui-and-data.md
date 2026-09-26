# UI, data and integration (Dinuka)

**Goal:** anyone can watch the agents work, see what each one does, and replay real disasters; and the
four workstreams join up cleanly.

## You own
`atlas/dashboard/` (responder dashboard and the new developer view), `atlas/api/`,
`atlas/datasets/`, `data/sources/`, `data/scenarios/`, the control boundary (`atlas/control/`, A8, A9),
Docker, CI, and merging the other three workstreams.

## Tasks, in order
UI
- [x] Bus records cause and effect: `event_causes` table in `atlas/core/event_bus.py` (Aeshan reviews).
- [x] `atlas/dashboard/dev/trace.py`: events with sender, receivers, cause, one-line summaries.
- [x] `atlas/dashboard/dev/catalog.py`: an agent card for every agent (purpose, reads, writes, LLM or not, last activity).
- [x] `/dev` page: agent map with live message counts between agents; message stream with filters; click an event to see its payload and its causal chain; incident timeline.
- [x] Replay scrubber (play a scenario, pause, step, speed).
- [x] Reasoning panel: Ask ATLAS and Technical → Intelligence show `Advice` with accepted and rejected claims; the entity guard flags prose naming unknown places.
- [x] Red-team panel (blocked actions) and latency meter (sensor to trigger to plan to alert).
- [x] Ask-the-system box (Ask ATLAS page, on Aeshan's `nlquery`).
- [x] Responder dashboard polish: hazards and route uncertainty on the map, mission board, approvals.
- [x] LoRa panel (Overview → Field radio): node online, last seen, signal strength, frames dropped (Hasindu's data).

Data
- [x] Fetch a real Ratnapura gauge history (`atlas/datasets/lk_irrigation.py`), check the format and units, cache it.
- [x] Open-Meteo rainfall and soil moisture; check the flood API date range.
- [x] `to_scenario` a real flood, scaled so it plays in minutes; keep a small committed fixture for tests.
- [x] Landslide and earthquake are live hazards (contract decision 3), with a landslide and an earthquake drill and a live USGS watcher (`python -m atlas.datasets.usgs --watch`). No real landslide or quake at Ratnapura to replay, so both drills are labelled made-up examples.
- [x] `data/sources/README.md`: every source with licence, what it drives, and whether it is verified.

Agent manager (added beyond the original list; merged in PRs #9 and #10)
- [x] Runs, traces and spans with the causal tree; agent graph replay; playground with what-if.
- [x] Registry with suspend and resume; OpenTelemetry-model traces with OTLP export.
- [x] Evaluations, monitors and experiments; guardrails view; role-based access; automatic audit trail.
- [x] `atlasctl` command line and a committed OpenAPI contract with a drift test.
- [x] Console sign-in (bottom of the menu).
- [x] OTLP export button on Run detail.
- [x] `docs/contracts.md` covers suspend and resume, roles, the `audit` ledger kind, `/am/v1` and `/ingest/link-status`.
- [x] Decision statistics on the Decisions page (autonomy rate, approvals and rejections, median time to decide).

Console for disaster managers
- [x] Plain-language operations pages: Overview, Map, Decisions, Alerts & reports, Teams & missions, Ask ATLAS, Practice drills; engineering pages under "For technical staff".
- [x] 3D situation map (MapLibre, keyless): satellite and streets, terrain, rain radar, click-to-follow cards, guided tour, route fly-along, nearby list, search, shareable views.
- [x] Demo guide: `docs/demo-guide.md`.

Platform
- [x] Dockerfile, `compose.yaml` (`docker compose up --build`), CI (`.github/workflows/ci.yml`), README setup guide.
- [x] The bus no longer scans the whole ledger on every publish: delivery reads only records after each subscriber's position (Aeshan, PR #13).

## Use meanwhile
`PermissiveOntology`, `advise_without_llm`, `MockLoraLink`, `data/scenarios/flood.json`.

## Done when
Replaying a real flood shows, live in `/dev`, each agent's messages and their causes, the plan, the
approvals and the resulting missions and alert, and every claim on screen links to its evidence.
