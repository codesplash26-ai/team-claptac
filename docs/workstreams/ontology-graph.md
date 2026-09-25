# Ontology and world graph (Sumudu)

**Goal:** a written vocabulary of what exists and how things relate, and a graph of the real world built
on it, so the intelligence layer can only assert things that are well-formed, known and supported.

## You own
`atlas/knowledge/` (`ontology.py`, `graph.py`, `linking.py`), `config/ontology.yaml`, and your existing
routing work (`atlas/tools/`, A4, A6, A7, `eval/`). Aeshan's `atlas/memory/knowledge_graph.py` is the
*audit* graph ("what depended on what"); yours models the *world*. Keep them separate.

## Tasks, in order
- [ ] Ontology: extend `config/ontology.yaml` (classes, relations with domain and range, hazards, units). Settle open decisions 3 and 5 in `docs/contracts.md`.
- [ ] `YamlOntology`: load the file; `validate(claim)` returns violations for unknown entity, unknown predicate, wrong type, wrong unit, missing or fake evidence.
- [ ] Tests: a table of good and bad claims (the bad ones are what a hallucinating model would write: invented shelters, roads that do not exist, a predicate used on the wrong class).
- [ ] World graph: build it from the road graph (`atlas/tools/geo.py`), `data/reference/` (shelters, resources, areas) and the COP; rebuild on demand, never a second source of truth.
- [ ] Queries: `neighbors`, `path`, `impact` ("what is affected if this road closes"), plus temporal and confidence attributes on facts.
- [ ] `linking.py`: resolve text and ids to entities, ambiguity returned not guessed.
- [ ] Evidence check: a helper that says whether an event id, COP belief or plan id exists, for Aeshan's grounding.
- [ ] Extend evidence and planners for other hazards: rainfall and soil moisture as evidence (landslide), and decide how an earthquake enters (`SensorType` change needs the whole team).
- [ ] Expose the graph to Dinuka's UI (a JSON view of an incident's neighbourhood).

## Use meanwhile
`PermissiveOntology` and `InMemoryWorldGraph` exist so Aeshan is never blocked; replace them, keep the
interfaces (`Ontology`, `WorldGraph`). Changing those signatures is a contract change.

## Done when
A claim naming a shelter that does not exist is rejected with a reason, a true claim about a real
shelter with evidence is accepted, and `impact("road:<u>-<v>")` lists the missions, shelters and groups
that depend on that road.
