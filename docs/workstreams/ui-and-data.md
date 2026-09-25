# UI, data and integration (Dinuka)

**Goal:** anyone can watch the agents work, see what each one does, and replay real disasters; and the
four workstreams join up cleanly.

## You own
`atlas/dashboard/` (responder dashboard and the new developer view), `atlas/api/`,
`atlas/datasets/`, `data/sources/`, `data/scenarios/`, the control boundary (`atlas/control/`, A8, A9),
Docker, CI, and merging the other three workstreams.

## Tasks, in order
UI
- [ ] Bus records cause and effect: `event_causes` table in `atlas/core/event_bus.py` (Aeshan reviews).
- [ ] `atlas/dashboard/dev/trace.py`: events with sender, receivers, cause, one-line summaries.
- [ ] `atlas/dashboard/dev/catalog.py`: an agent card for every agent (purpose, reads, writes, LLM or not, last activity).
- [ ] `/dev` page: agent map with live message counts between agents; message stream with filters; click an event to see its payload and its causal chain; incident timeline.
- [ ] Replay scrubber (play a scenario, pause, step, speed).
- [ ] Reasoning panel: show Aeshan's `Advice` with accepted and rejected claims (a rejected claim struck out is the proof the guard works).
- [ ] Ask-the-system box (Aeshan's `nlquery`), red-team panel (blocked actions), latency meter (sensor to trigger to plan to alert).
- [ ] Responder dashboard polish: hazards and route uncertainty on the map, mission board, approvals.
- [ ] LoRa panel: node online, last seen, signal strength, frames dropped (Hasindu's data).

Data
- [ ] Fetch a real Ratnapura gauge history (`atlas/datasets/lk_irrigation.py`), check the format and units, cache it.
- [ ] Open-Meteo rainfall and soil moisture; check the flood API date range.
- [ ] `to_scenario` a real flood, scaled so it plays in minutes; keep a small committed fixture for tests.
- [ ] Landslide and earthquake scenarios (COOLR, USGS) once the contract decision in `docs/contracts.md` (#3) is made.
- [ ] `data/sources/README.md`: every source with licence, what it drives, and whether it is verified.

Platform
- [ ] Dockerfile, CI, reproducibility guide; cover the bus's full-ledger scan on every publish before large replays.

## Use meanwhile
`PermissiveOntology`, `advise_without_llm`, `MockLoraLink`, `data/scenarios/flood.json`.

## Done when
Replaying a real flood shows, live in `/dev`, each agent's messages and their causes, the plan, the
approvals and the resulting missions and alert, and every claim on screen links to its evidence.
