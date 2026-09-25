# Roadmap

State today: sensing (A1), routing and allocation (A4, A6, A7), orchestration (A2, A3, A5), the control
boundary (Gateway, policy, approvals), and execution (A8, A9) are merged and tested end to end on
replayed data. What is left is the field link, real data, the intelligence layer, the ontology and graph,
and a UI that shows the agents working.

## Workstreams

| Workstream | Owner | Doc | Owns |
|---|---|---|---|
| Hardware and LoRa | Hasindu | `docs/workstreams/hardware-lora.md` | `hardware/lora/`, `atlas/ingest/lora.py`, `atlas/ingest/field.py` |
| UI, data and integration | Dinuka | `docs/workstreams/ui-and-data.md` | `atlas/dashboard/`, `atlas/api/`, `atlas/datasets/`, `data/sources/`, `data/scenarios/` |
| Intelligence layer | Aeshan | `docs/workstreams/intelligence.md` | `atlas/intelligence/`, `atlas/reasoning/`, A2, A3, A5, `atlas/tools/llm.py` |
| Ontology and graph | Sumudu | `docs/workstreams/ontology-graph.md` | `atlas/knowledge/`, `config/ontology.yaml`, `atlas/tools/sensor_map.py` |

Everything else keeps its current owner (platform: Aeshan; routing: Sumudu; control boundary: Dinuka).

## Phases

1. **Scaffold (this branch).** Folders, contracts and stubs exist; everyone can find their files and
   code against interfaces. Nothing new works yet, everything imports, the test suite is green.
2. **Parallel build.** Each person works on their own branch (`<Name>/<area>`), small PRs, against the
   stubs and mocks in `docs/contracts.md`. Nobody waits for anybody.
3. **Integration.** Dinuka merges in this order, because each layer builds on the one before:
   ontology and graph, then the intelligence layer, then LoRa, then the UI on top of all of it. First
   goal: a replayed real flood runs through the agents and shows in the developer view.
4. **Polish and evaluation.** Metrics (sensor to trigger latency including LoRa), red-team tests,
   demo script, fallback drill (mock node and replay if hardware fails), Docker, reproducibility guide.

## Definition of done (for any piece)

Code, tests, a docstring saying inputs and outputs, runs from the scenario replayer (and from live
sensors where relevant), and the contract in `docs/contracts.md` still holds.
