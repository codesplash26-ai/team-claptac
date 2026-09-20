# Routing, shelter, logistics and evaluation (Workstream C)

Workstream C is Sumudu's. This is the contract other workstreams integrate against, and the record of
what the evaluation found.

## Scope and status

Implemented and tested:

| Piece | Where | Notes |
|---|---|---|
| Belief-weighted cognitive map, ported from the original engine | `atlas/tools/cognitive_map/` | Reproduces the proposal's table exactly (see Evaluation) |
| Real road networks with elevation | `atlas/tools/geo.py`, `data/osm/*.json` | Colombo Fort, Ratnapura, Kandy; cached and committed, offline at runtime |
| Sensor reading to road evidence | `atlas/tools/sensor_map.py`, `data/reference/sensor_map.yaml` | Demo flood scale, not hydrology |
| **A4** Evacuation & Access | `atlas/agents/a4_evacuation.py` | Beliefs into the COP, ranked safe routes |
| **A6** Resource & Logistics | `atlas/agents/a6_logistics.py` | Teams and vehicles to jobs, CP-SAT |
| **A7** Shelter & Relief | `atlas/agents/a7_shelter.py` | People to shelters, CP-SAT |
| Optimisers | `atlas/tools/optimisation.py` | Deterministic, with a greedy fallback |
| Evaluation | `eval/` | Proposal-table gate, real networks, latency |

Not done, and not claimed: the Incident Commander (A3) and triage (A5) are Hasindu's; no reservation
accounting (see "Occupancy and availability"); reference data (shelters, fleets) is synthetic.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-c.txt
python -m pytest -q
python -m eval.run_abc                                 # reproduce the proposal's table (gate)
python -m eval.run_abc --site ratnapura                # real network, both uphill settings
python -m eval.run_abc --pipeline                      # compute latency of A4/A6/A7
python -m atlas.tools.geo build --all                  # rebuild the road graphs (needs network)
python -m atlas.tools.reference generate               # regenerate synthetic shelters/fleet
```

`requirements-c.txt` installs from prebuilt wheels on Python 3.14; the project minimum is 3.11.

## What the evaluation found

Three results the team should know before the pitch.

**1. The engine's published results reproduce exactly.** The proposal's table (section 7.1) is from the
synthetic grids. `tests/tools/test_cognitive_map_fidelity.py` replays all 60 original episodes
(3 scenarios x 20 seeds x 30 agents) against the old repo's `results.json` and requires every agent's
result to match. `python -m eval.run_abc` reproduces 24/24 published numbers and meets every section
8.1 target on the grid.

**2. On real hilly networks the original engine fails, and the cause is the uphill penalty.** The
original real-map code slows uphill roads by 10% per metre climbed. On the real networks that steers
evacuees down into the valley, away from the hill shelters, and into the flood. Controlled runs
(Ratnapura, flood scenario B, 20 seeded runs):

| Uphill penalty | Belief agent success | Failed routes per agent |
|---|---|---|
| 0.10 per m (original) | 61% | 18.3 |
| 0.05 | 83% | 8.4 |
| 0.02 | 100% | 0.23 |
| 0 | 100% | 0.17 |

Ruled out: compounding time decay (61% either way), and the cost formula's risk term scaling with edge
length (safe-first routing lifts 61% to 66% only). The proposal's 96% is unaffected: the grids have no
elevation. **ATLAS's planning agents default `RoutingConfig.slope_penalty_per_m` to 0**; the engine
itself keeps the original 0.10 so it stays reproducible. Elevation is still used, for flood mapping and
for choosing high-ground shelters.

**3. On real networks the belief agent matches the naive baseline but does not beat it.** On the
synthetic grid the naive agent (trusts every report) falls to 89% while the belief agent holds 96%.
On the real networks the naive agent stays at 98 to 100%. The proposal's "robust to misinformation
where naive fails" claim is supported by the grid results, not by these real-network scenarios.

Real-network results with ATLAS's setting (20 seeded runs x 10 agents per type; the original setting
is in the CLI output):

| Network | Scenario | Omniscient | Belief agent | Naive (Reactive) |
|---|---|---|---|---|
| Ratnapura | B flood | 100% / 0.04 failed | 100% / 0.17 | 100% / 0.14 |
| Ratnapura | C flood + misinformation | 100% / 0.12 | 100% / 0.16 | 100% / 0.07 |
| Kandy | B | 100% / 0.00 | 100% / 0.00 | 100% / 0.00 |
| Kandy | C | 100% / 0.00 | 100% / 0.00 | 100% / 0.00 |
| Colombo Fort | B | 98% / 0.02 | 98% / 1.12 | 98% / 0.08 |
| Colombo Fort | C | 98% / 0.02 | 98% / 1.12 | 98% / 0.34 |

With the original penalty: Ratnapura meets 1 of 4 section 8.1 targets, Kandy 3 of 4, Colombo Fort 4 of 4
(flat, so unaffected). These are illustrative scenarios on real geometry, not flood forecasts. All three
agent types share one simulated world and random stream, so differences of a few hundredths between rows
are noise.

**Compute latency** (`--pipeline`, Ratnapura): evidence to beliefs 9 ms, route plan 8 ms, shelter plan
25 ms, logistics plan 6 ms, about 47 ms end to end. This is a floor for the proposal's "under 2 minutes"
replanning target, not a measurement of it: it excludes A1 detection, A3's LLM, the human approval gate
and message delivery.

## Interfaces

Agents never call each other. They read and write the COP and exchange events. Only A3 delegates.

### Events

| Flow | Event type | Payload |
|---|---|---|
| A1 to A4 | `OBSERVATION_VALIDATED` | `Observation` (unchanged) |
| A4 to A3 (and A2) | `RISK_UPDATE` | `{kind: "road_access_changed", site, area_id, level_cm, newly_closed, newly_reopened, observation_id, snapshot_version, evidence_ids}` |
| A3 to A4 / A6 / A7 | `TASK_ASSIGNED` | An `EvacuationTask`, `LogisticsTask` or `ShelterTask`, addressed by `assignee` |
| A4 / A6 / A7 to A3 | `CANDIDATE_PLAN` | `CandidatePlanPayload` wrapping the plan |

The payload models are in `atlas/agents/contracts.py`. They are typed payloads for event types that
already exist; nothing in `core/events.py` changed. **They are a proposal for Hasindu to confirm.**
`TASK_ASSIGNED` reaches every subscriber; each planner acts only when `assignee` is its own name.

```python
Event(type=EventType.TASK_ASSIGNED, source="a3_incident_commander", incident_id=incident_id,
      payload={"assignee": "a4_evacuation", "task_id": "t-1",
               "origin": 5299475391,                    # node id, or {"lat": .., "lon": ..}
               "destinations": None,                    # None = every shelter; or ids / nodes
               "k": 3})
```

A3 may instead call the synchronous, read-only tools directly:
`Evacuation.plan_evacuation(origin, destinations, k=, now=)`, `ShelterRelief.assign(groups, ...)`,
`Logistics.assign(jobs)`. All are deterministic and safe inside a bus handler.

### Plan statuses

| Agent | Status | Meaning and what A3 should do |
|---|---|---|
| A4 | `ok` | At least one route avoids every road believed likely closed (belief below 0.3). Use `options[0]`. |
| A4 | `no_safe_route` | Routes exist but each crosses a likely-closed road. They are returned, flagged `safe: false`. **Escalate to a human.** |
| A4 | `no_route` | Nothing reachable at all. |
| A7 | `ok` / `partial` / `no_shelter` | All placed / some `unplaced` (reason `no_capacity` or `no_safe_route`) / none placed: escalate. |
| A6 | `ok` / `partial` / `none_served` | All jobs served / some `unserved` (reason `no_safe_route` or `not_enough_units`) / none: escalate. |
| all | `invalid_task`, `error` | Bad input, or a bug. Returned as a plan, never raised: the bus retries a failing handler forever. |

Every plan carries `snapshot_version` and `valid_until`. Put them in the `ProposedAction`
(`snapshot_version`) so the Gateway rejects a stale plan (`stale_snapshot`); replan on that. A6 shortage
alerts fire only when jobs are unserved for lack of units, never because roads are blocked, since more
units cannot fix a flooded road.

`RoutePlan.to_commander_state(people)` reshapes A4's plan for Aeshan's `CommanderAdapter`. It omits
unsafe routes, so under `no_safe_route` the adapter finds nothing to choose and returns no action, which
is the cue to escalate.

### COP schema

| Subject | Attribute | Value | Written by | Read by |
|---|---|---|---|---|
| `edge:<u>-<v>` | `belief_open` | float 0.05 to 0.95 | A4 | A4, A6, A7 |
| `edge:<u>-<v>` | `open` | `False` | A8 (`close_major_route`) | A4, A6, A7 as a hard closure |
| `shelter:<id>` | `occupancy` | int people | `ShelterRelief.record_occupancy` | A7 |
| `resource:<id>` | `available` | bool | `Logistics.record_availability` | A6 |

Edge ids are `<u>-<v>` with OSM node ids, matching the `road_id` A8 writes. Time decay is computed when
the map is read and is **never written**; otherwise every plan would see a moved COP version and be
rejected at the Gateway.

### Wiring

```python
runtime = Runtime(settings)
routing = attach_routing(runtime, site="ratnapura")     # atlas/agents/wiring.py
control = attach_control(runtime)
await runtime.start()
```

Register before `start()`: startup recovery redelivers pending events, including earlier observations,
to whoever is registered. A4's handler does database work only and never touches the network; graphs
load from the committed cache.

## Demo script (Ratnapura)

The tabletop container stands in for the Kalu Ganga. `data/reference/sensor_map.yaml` records the
calibration; `tests/tools/test_sensor_map.py` asserts it.

| Water level | What happens |
|---|---|
| 25 cm | The Sentinel's alarm. 51 roads flood; A4 announces them. `ratnapura-shelter-02` (a hill shelter at 90 m whose only approach crosses the valley) becomes unreachable and is dropped; the other three shelters stay reachable from the depot. |
| 30 cm | The valley floods. Residents such as `DEMO_ORIGIN` (`atlas/tools/demo.py`) must reroute from shelter-01 to shelter-03. The depot is cut off from every shelter: `no_safe_route`, escalate to the human commander. |

Keep the demo between about 20 and 30 cm; the fallback is the mock node's `flood_ramp`. The flood scale
(0.20 m of water per sensor cm) is a demo assumption. SRTM elevation is whole metres, so flooding moves
in steps.

## Occupancy and availability (open interface)

A plan reserves nothing. Shelter occupancy and unit availability change only through
`record_occupancy` and `record_availability`, so two plans made before anything is recorded see the same
free places. That is why A3 must submit one reconciled plan, and replan when the COP version moves.
Recording is not wired to anything yet: A8's `create_mission` carries `resources_committed` and a
destination but no headcount, so it cannot update occupancy. Agreeing that (a `people` parameter, and
who calls the recorders on `MISSION_STATUS`) is the main open item with Dinuka and Hasindu.

## Assumptions and limits

- Route `success_probability` multiplies edge beliefs, treating roads as independent. Unobserved roads
  sit at the 0.9 prior, so long routes look risky mostly because of them (`observed_edges` /
  `unobserved_edges` show this). Compare routes with it; use `safe` and `weakest_belief` to decide.
- A steady sensor reading is re-written to the COP about every four minutes as its evidence decays,
  which moves the version and can stale an in-flight plan. Replan on `stale_snapshot`.
- The engine's `apply_time_decay` compounds when called every step (kept for fidelity); the live path
  applies it once per read, where it is a single decay.
- The engine's risk term scales with a road's own length, so a short doubtful road costs little.
  A4 works around it with safe-first routing; the engine is unchanged.
- Shelters and fleets are synthetic, placed on real nodes from high ground. The Sinhala/Tamil and
  reference-data caveats in `docs/control-boundary.md` apply to anything built on them.
- The evaluation shares one random stream across agent types (inherited), so small differences
  between rows are noise.

## Deviations from the proposal (for the pitch)

- **Elevation source:** OpenTopoData SRTM 30 m replaces Open-Elevation. The Open-Elevation endpoint
  fails TLS verification (expired certificate); certificate checking is not disabled. Both are
  SRTM-derived.
- **Uphill penalty:** removed from ATLAS planning (section 5 lists "elevation-aware travel-time
  estimation"). Elevation is used for flooding and shelter choice instead.
- **Real-network claim:** see result 3 above.
- **Backend:** FastAPI (section 6.1) rather than Flask (6.2), unchanged from the team plan.

## Handoffs

- **Hasindu (A3, A5):** confirm the payloads in `atlas/agents/contracts.py`. A5's triage ranking maps to
  `RescueJob.priority` (a relative value, higher first). A7's `resupply_requests` become A6 vehicle jobs
  in your glue. Treat `no_safe_route` / `no_shelter` / `none_served` as escalate.
- **Dinuka:** who records occupancy and availability from missions (above); `RoutePlan` and
  `Placement.route_nodes` give A8 a route as node ids, and `RoadGraph.route_geojson` /
  `edges_geojson` (`atlas/tools/geo.py`) draw them and road beliefs on the Folium map.
- **Aeshan:** `area_id` `ratnapura-demo-river` must stay identical in the Pi config, `flood.json` and
  `sensor_map.yaml` (a test enforces it). Per-edge COP writes are fine at this size; a `COP.put_many`
  would only matter for much larger graphs.
