# Contracts between workstreams

Four people work in parallel. This page lists every seam between them so nobody waits for anybody:
each interface has a stub in the repo today, and you code against the stub.

Rules
- You own the files listed for you in `docs/workstreams/`. Do not edit another person's files: open a PR
  or message them. Small exceptions are named below.
- Changing a contract on this page, `atlas/core/events.py`, or the sensor message format needs the whole
  team's approval (tag everyone on the PR).
- Anything that is not built yet raises `NotImplementedError` with the owner's name in the message.
  If you need it before it exists, use the mock named in the table.

## Interfaces

| # | Seam | Producer | Consumer | Where it lives | Mock to use meanwhile |
|---|---|---|---|---|---|
| 1 | LoRa frame format | Hasindu | node firmware, gateway | `hardware/lora/protocol.md`, `atlas/ingest/lora.py` | `MockLoraLink` |
| 2 | Sensor readings from LoRa | Hasindu | A1 Sentinel (unchanged) | `SensorReading` in `atlas/ingest/sensors.py` | `hardware/pi/sensor_node.py` with `driver: mock` |
| 3 | Field reports and mission acks | Hasindu | A5 / A8 (via Dinuka's routes) | `atlas/ingest/field.py` | construct the models directly |
| 4 | Scenario files | Dinuka | replayer, Hasindu's mock node | `data/scenarios/*.json`, `atlas/datasets/to_scenario.py` | `data/scenarios/flood.json` |
| 5 | Trace and causation | Dinuka (bus table: Aeshan reviews) | Aeshan's traces, the UI | `atlas/dashboard/dev/`, `event_causes` table | read the ledger directly |
| 6 | Claims and validation | Sumudu | Aeshan | `atlas/knowledge/ontology.py` | `PermissiveOntology` |
| 7 | World graph queries | Sumudu | Aeshan, Dinuka | `atlas/knowledge/graph.py` | `InMemoryWorldGraph` |
| 8 | Entity linking | Sumudu | Aeshan | `atlas/knowledge/linking.py` | none: `NotImplementedError` |
| 9 | Advice (`REASONING_COMPLETED`) | Aeshan | Dinuka's UI | `atlas/intelligence/contracts.py` | `advise_without_llm` |
| 10 | LLM provider | Aeshan | Aeshan, Sumudu (extraction) | `atlas/intelligence/providers.py` | a fake provider in tests |

## Shared facts everyone needs

- Agents talk only through the bus and the COP. A handler returns events and never publishes; it must not
  call the network or a model (`docs/orchestration.md`).
- The LLM is never on the critical path and never computes routes, scores, allocations or policy.
- Sensor area ids must match across the node config, `data/scenarios/*.json` and
  `data/reference/sensor_map.yaml` (`ratnapura-demo-river`).
- Secrets live in `.env` (git-ignored), never in the repo or in chat.

## Open decisions (resolve before wiring; owner proposes, everyone agrees)

1. **Field messages onto the bus.** New `EventType`s for field reports and mission acks
   (touches `core/events.py`, needs everyone), or API routes that call A5 `record_need` and A8's field
   methods directly like `/missions/{id}/acknowledge` does today? *Proposal: direct calls first, events
   later if the trace view needs them.* (Hasindu and Dinuka)
2. **How a node names a mission and an area.** Radio frames are tiny: use a numeric area code table in
   the gateway config and the first 6 bytes of the mission id. (Hasindu)
3. **Earthquake and rainfall in the planners.** A4's sensor map only turns water level into road
   evidence. Rainfall and soil moisture as evidence, and earthquakes at all, are Sumudu's call and a
   contract change if a new `SensorType` is needed. (Sumudu and Dinuka)
   *Settled for now: the vocabulary knows the other hazards (`hazard_status`), the live loop does not;
   see `docs/knowledge-graph.md`.*
4. **Where the advisor runs.** A worker inside the API process (simple) or a separate process. *Proposal:
   inside the API process, started next to `ControlPlane.start_worker`.* (Aeshan)
5. **Ontology ids.** Entity ids follow `class:key` (`shelter:ratnapura-shelter-01`, `road:<u>-<v>`),
   matching COP subjects where they exist. (Sumudu)
   *Settled: `road:` is canonical, `edge:` is accepted and rewritten; full table in
   `docs/knowledge-graph.md`.*
6. **Bus cause-and-effect.** A small `event_causes` table written by the bus beside the ledger (not in
   event content, because identical events can be produced from different parents). Dinuka implements,
   Aeshan reviews.
