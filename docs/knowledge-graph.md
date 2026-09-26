# Ontology and world knowledge graph

Owner: Sumudu. Code in `atlas/knowledge/`, vocabulary in `config/ontology.yaml`, routes in
`atlas/api/knowledge_routes.py`. Brief: `docs/workstreams/ontology-graph.md`.

The point: the intelligence layer may only say things that are well formed, about things that exist, and
backed by evidence that exists. Four pieces do that.

| Piece | Question it answers | Where |
|---|---|---|
| Vocabulary and validator | Is this claim made of known things, used the way the vocabulary allows? | `ontology.py`, `config/ontology.yaml` |
| World graph | What exists, and how is it connected right now? | `graph.py`, `world.py` |
| Queries | What does this closure affect? What is around this entity? | `queries.py` |
| Guard | Did the model invent an entity, in a claim or in its wording? | `linking.py`, `guard.py`, `evidence.py` |

## Ids

`class:key`, matching the COP where it has the subject (contracts decision 5).

| Class | Id | Example |
|---|---|---|
| Area | `area:<id>` | `area:ratnapura-demo-river` |
| Road | `road:<u>-<v>` (OSM node ids) | `road:10920593370-10920593400` |
| Junction | `junction:<osm node>` | `junction:5299475391` |
| Shelter | `shelter:<id>` | `shelter:ratnapura-shelter-01` |
| Gauge | `gauge:<node or station slug>` | `gauge:rtp-node-01`, `gauge:rathnapura` |
| Incident | `incident:<area>` | `incident:ratnapura-demo-river` |
| Mission | `mission:<id>` | `mission:m-1a2b3c4d5e6f` |
| Resource | `resource:<id>` | `resource:ratnapura-rescue-team-01` |
| Group | `group:<incident key>/<group id>` | `group:ratnapura-demo-river/g1` |
| Hazard | `hazard:<name>` | `hazard:flood` |

The COP writes roads as `edge:<u>-<v>`. `edge:` is accepted anywhere and rewritten to `road:`. A3's group
ids (`g1`, `g2`) repeat across incidents, so groups are scoped by the incident key. A3 keys such as
`rescue:<area>` and `resupply:<shelter>` are missions with no evacuee group, so no group is made for them.

## The vocabulary

`config/ontology.yaml` is the single source; `GET /knowledge/ontology` returns it as JSON. It holds classes
(with an id prefix and an optional key pattern), relations (each with a domain and a range), attributes (each
with its unit, bounds or allowed values), hazards and aliases. The file is checked when it is loaded:
duplicate prefixes, a relation naming a class that does not exist, a bad unit table, a hazard without a
status all fail with one error listing every problem.

Two relation flags drive the queries:

- `dependency: true`: the subject cannot work without the object (`uses`, `routed_over`, `destined_for`,
  `placed_at`, `assigned_to`). Impact flows from the object to the subject.
- `rollup: true` (`part_of`): what happens to a part happens to the whole.

The unit table equals the one in `SensorReading` (`atlas/ingest/sensors.py`); a test keeps them equal.

## The validator (rejecting unknown things)

`YamlOntology.validate(claim)` fails closed: anything not in the vocabulary is a violation, and one claim can
have several.

| Code | Means |
|---|---|
| `malformed_id` | not `class:key`, or the key does not match the class pattern |
| `unknown_entity` | well formed, but no such thing in the world graph |
| `unknown_predicate` | not in the vocabulary |
| `wrong_type` | the subject or object is the wrong class for that predicate (domain and range) |
| `wrong_unit` | a unit the attribute does not use, or a missing one where a unit is required |
| `bad_value` | out of bounds, wrong type, or not an allowed member (`belief_open` 1.4, `risk_level` "purple") |
| `fake_evidence` | a cited evidence id does not exist (only if `evidence_exists` is given) |
| `no_evidence` | none cited (only if `require_evidence` is set) |

`Claim` gained an optional `unit` (default `None`). Aeshan's `ground()` adds its own `no_evidence`, so
`YamlOntology` leaves evidence to it by default; pass `evidence_exists=` and `require_evidence=True` to
have the ontology check it instead (a claim could then get the same code twice).

```python
world = WorldProjector.from_system(system).world()
ontology = YamlOntology.load(graph=world)
advice = ground(advice, ontology, EvidenceIndex(runtime).exists)   # Aeshan's function, unchanged
```

## The world graph

`WorldProjector.from_system(system).world()` builds a `ProjectedWorldGraph`. It is a projection: rebuilt
from the stores below when something changed (COP version, incidents, mission statuses, ledger tail),
otherwise cached. Nothing writes to it, so it is never a second source of truth.

| Source | Becomes |
|---|---|
| Road graph (`data/osm/`) | Roads, Junctions, `from_junction`, `to_junction`, travel time |
| `data/reference/` | Shelters (`located_at`, capacity), Resources, Areas (population, vulnerable fraction) |
| `atlas/datasets/areas.py` | Area centres, gauge stations |
| COP snapshot | `belief_open`, `closed`, occupancy, availability, `risk_level`; `threatens` for roads believed likely closed |
| A4's routing tools | `reachable_from`, `serves` (never a second routing engine) |
| A3 incidents | Incidents, `about`, `part_of`, Groups |
| A8 missions | Missions and status (A8 wins over A3), `uses`, `destined_for`, `assigned_to`, `routed_over` |
| Ledger | Gauges from validated observations, `monitors` |

Every fact carries `confidence`, `valid_from`, `provenance` (evidence ids, in the namespaces below) and
`source`. Finished, failed and cancelled missions stop depending on anything.

## Queries

```python
world.impact("road:<u>-<v>")          # flat sorted list of ids (the WorldGraph protocol)
world.impact_report("road:<u>-<v>")   # ImpactReport: the same, with reasons
world.neighbourhood("incident:ratnapura-demo-river", depth=2)   # JSON for the UI
world.facts("shelter:ratnapura-shelter-01", direction="both", as_of=None, min_confidence=0.5)
```

An impact report has three parts: **dependants** (walk dependency relations backwards, transitively:
missions using the road, their groups, their crews), **expecting** (shelters those missions are heading
for), and **access changes** (remove the road from the junction topology, recompute shortest times; shelters
and areas that lose or lengthen access are listed even though nothing "points at" the road). `rollups`
carry the incident. Every dependant has a plain reason (`"uses road:1-2"`).

## Stopping a model inventing entities

Three layers, each read-only. None of them repairs anything: a bad reference is reported, never silently
replaced by a "close" real one.

1. **Claims** (`YamlOntology`, above): an invented shelter is `unknown_entity`.
2. **Linking** (`link(text, graph, context=())`): an explicit id matches exactly or not at all. A name matches
   only if its digits are identical, its similarity is at least 0.86, and it beats the runner-up by 0.08.
   "Shelter 7" never becomes "shelter 1". If two things match ("shelter 1" at two sites) `entity_id` is
   `None` and `alternatives` lists both; `context` may choose between them but never adds a candidate.
3. **Prose** (`find_mentions(text, graph)`, `EntityGuard.check_text/check_advice`): scans wording for ids,
   names, and place-word patterns ("Shelter 7", "bridge at Kuruwita") and splits them into resolved,
   ambiguous and unresolved. `check_advice` reads the summary, concerns, questions and every claim's text and
   says where each problem is (`claims[0].text`).

## Evidence

`EvidenceIndex(runtime).exists(ref)` answers whether an evidence id is real, from one indexed lookup in the
store it came from. It reads only, and returns a yes or no plus a kind, never content, so it does not
bypass the evidence store's role check. Anything not in this table is not evidence.

| Form | Real if |
|---|---|
| 64 hex characters | a row in the evidence store, or an event id |
| 32 hex characters, `event:<id>` | a ledger record with that id |
| `action:<key>[:<phase>]` | a ledger record for that action |
| `reasoning:<id>` | a ledger record with that id |
| `cop:<subject>/<attribute>` | a belief in the COP, even if it has since expired |
| `cop-version:<n>`, `a4-routing@cop-v<n>` | n is not after the COP's current version |
| `mission:<id>`, `incident:<id>` | a row in A8's or A3's table |
| `plan:<task id>` | a `candidate_plan` event answering that task |
| `sensor_map:<area>` | an area in `data/reference/sensor_map.yaml` |
| `data/...`, `config/...`, `atlas/...` | a file under that folder (paths that leave it are refused) |

## API

All under `/knowledge`, read-only, permission `mission:read` (the closest existing read permission; add a
`knowledge:read` to `atlas/manager/rbac.py` if you want it grantable alone). Ids are query parameters
because they contain `:` and `/`. Unknown ids answer 404.

| Route | Returns |
|---|---|
| `GET /ontology` | classes, relations, attributes, hazards |
| `GET /summary` | counts per class, fact count, anything the projector could not place |
| `GET /entities?class=&limit=` | ids, classes and labels |
| `GET /entity?id=` | attributes (value, unit, confidence, source, provenance) and facts in and out |
| `GET /neighbourhood?entity=&depth=&predicate=&topology=&max_nodes=&min_confidence=&as_of=` | `{root, nodes, edges, truncated}` |
| `GET /impact?entity=` | dependants by class, reasons, expecting, access changes |
| `GET /link?text=&context=` | one `Link`: `entity_id`, `confidence`, `reason`, `alternatives` |
| `POST /validate` `{claims, check_evidence}` | per-claim `accepted` and violations |
| `POST /check` `{text, context, strict_ambiguity}` | `{ok, problems, mentions}` |
| `POST /mentions` `{text, context}` | resolved, ambiguous, unresolved |

The two POST routes only read; they are POSTs because they take a body, and are audited like other POSTs.

## Known limits

- **Area containment is a stated assumption.** Areas have a centre, not a polygon, so a road, shelter or
  gauge belongs to the nearest centre within 2.5 km, at confidence 0.7, and the fact says so.
- **The prose guard is heuristic.** It cannot verify an arbitrary proper noun without a gazetteer, so it
  reports "does not match anything known" instead of guessing. It may be too cautious ("area of
  Ratnapura" is flagged).
- **Access times in impact are free-flow minutes.** They approximate A4; they are not its exact answer.
- **`reachable_from` and `serves` are derived** from A4's tools at the COP version they were built at.
- **English names only.** Sinhala and Tamil aliases need native-speaker review before they are added.
- **The world is rebuilt, not updated.** Fine for a few thousand entities; the fingerprint keeps repeat
  reads cheap.

## Decision record: other hazards (contracts decision 3)

Earthquake, tsunami and drought are in the vocabulary (`hazards`, `hazard_status: history_only`) and **not**
in the live loop. Decided while planning this work: vocabulary only.

- **Why.** The Sentinel understands water level, rainfall and soil moisture. A quake or tsunami replayed
  today would trigger a flood-shaped plan (`docs/data-and-hazards.md`), which would mislead an operator.
- **What this means.** A claim or an incident can name `hazard:earthquake` without being rejected as
  unknown, and the world graph says the planners cannot act on it (`hazard_status`).
- **What would change it.** A `Trigger` hazard field, new `SensorType`s with an A1 rule each, and A2 and A4
  mappings. Each is a contract change for the whole team. Rainfall and soil moisture already reach A2
  (landslide is `partial`); A4 still maps roads from water level only.
- **Decision 5 (ids)** is settled by the table above.

**Update: landslide and earthquake are now live** (`hazard_status: live`). The Sentinel tags triggers
with a hazard, A2 tracks the dominant hazard per area, and A4 maps slope hazards onto steep roads. See
`docs/data-and-hazards.md`. Tsunami and drought remain `history_only`.

## Contract changes to tell everyone about

1. `Claim` gains an optional `unit: str | None = None`. Existing callers are unaffected.
2. `road:<u>-<v>` is the canonical road id; `edge:<u>-<v>` is accepted and rewritten. A4 and A8 still write
   what they wrote.
3. `link()` takes an optional `context`; the graph gains additive methods (`entities`, `facts`, `impact_report`,
   `neighbourhood`). The `Ontology` and `WorldGraph` protocols are unchanged.
4. One line and one import in `atlas/api/main.py` register the router (Dinuka's file).

## Try it

```bash
python -m pytest tests/knowledge -q
```

With the API running (`uvicorn atlas.api.main:app`):
`/knowledge/neighbourhood?entity=incident:ratnapura-demo-river` and
`/knowledge/impact?entity=road:<u>-<v>`.
