# Platform, A1 Sentinel and the shared planner

## Scope

The platform every agent runs on (event bus, common operating picture, ledger, replay), the structured
A1 Sentinel pipeline, the durable sensor outbox, and a reusable **PlanU-inspired** planner.

The planner uses stochastic action/outcome trees, empirical return distributions,
UCB exploration, bounded depth, and mean or lower-tail final selection. It is **not
a reproduction of PlanU**: it omits learned curiosity, embedding-based state
matching, and quantile-regression updates. Source inspiration:
https://arxiv.org/abs/2510.18442

## Quick start

From the repository root, using Python 3.11 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
python -m atlas.ingest.simulator data/scenarios/flood.json
python -m eval.run_reasoning --trials 30
uvicorn atlas.api.main:app --reload
```

No model key, OSM download, physical Pi, or
broker is needed for replay, evaluation, or unit tests. Replay uses an in-memory
database and a simulated clock; the API defaults to SQLite at `data/atlas.db`.

The flood replay should produce one trigger, one conflicting report, a reasoning
trace/information-gap recommendation, and a rejected stale backlog reading.
Evaluation reports actual simulated outcomes across paired seeded episodes; it
does not establish performance on real disasters. Scenario evaluation and MCTS
receive equal simulation-count limits, though tree search performs more transitions.
The scenario evaluator is the simple one-step baseline. Include measured latency
and model calls when comparing methods, not just success rate.

## Interfaces

- `Agent.handle(event) -> list[Event]` is unchanged. An agent returns events; it
  must not call `bus.publish()` recursively from its handler.
- Existing `OBSERVATION` remains **raw input** for backward compatibility.
  A1 emits `OBSERVATION_VALIDATED`. A2 and sensor-to-map consumers should subscribe
  to validated observations. This replaces the old ambiguous comment in events.py.
- New types: `OBSERVATION_REJECTED`, `INFORMATION_GAP`, `REASONING_COMPLETED`.
- Sensor readings optionally carry `measurement_id`. Old payloads remain valid;
  identical old payloads get a stable content-derived ID. Reusing a measurement ID
  with different content raises a conflict. Units, finite values, coordinates, and
  timezone-aware timestamps are now validated.
- Canonical `Observation` and `Trigger` schemas live in `core/events.py`.
- `ProposedAction` gains optional `snapshot_version` and `reasoning_id`. Existing
  callers need not supply them. The Gateway validates current state and authority at the
  actual execution boundary; the planner is not an authorization service.

## Runtime and persistence

`Runtime(settings)` owns one database, COP, ledger, bus and Sentinel. Register
additional agents with `runtime.register(agent)` **before** `await runtime.start()`.
Startup verifies the ledger and retries pending deliveries for registered agents.
Publish input with `await runtime.bus.publish(event)` and shut down with
`await runtime.close()`.

The bus serializes handlers. Each handler's SQLite mutations, emitted events, and
delivery receipt commit together. If a handler fails, its input stays in the
ledger while those changes roll back. Retrying or restarting can finish delivery.
Handlers must use the shared stores and perform no unprotected external effects.
This is not a distributed workflow engine. Run a **single API worker**; multiple
independent runtimes sharing a database are not supported.

The current bus scans the journal for undelivered events. This is adequate for the
demo; a delivery index/outbox worker is the upgrade path for high-volume feeds.
Do not hold the database transaction across long external work. Keep model calls
bounded; use a separate workflow for long-running execution.

COP snapshots are detached JSON values with a global version and TTL filtering.
They also expose the earliest `valid_until` time; pass it to `PlanningRequest` so
`require_current` rejects expired evidence even if no new observation changed the
version. Execution-time domain and policy validation is still required.
`cop.put()` rejects older timestamps. The version is deliberately conservative:
an unrelated belief change can invalidate a plan. Per-incident revisions can be
added later. TTL removes beliefs from fresh reads; it does not delete history.

The ledger allows multiple phases for the same action idempotency key. Each phase
needs its own record ID. Repeating an identical record is harmless; reusing its ID
with different content is rejected. SQLite triggers prevent normal update/delete,
and hash links detect chain inconsistencies. This is not protection against a
database administrator rewriting the database or truncating its tail; an external
signed checkpoint would be needed for that threat model.

Evidence is internal content-addressed JSON. Callers must supply a permitted role;
this is an in-process boundary, not user authentication or encryption. No public
evidence endpoint is exposed. Attachments/OCR/STT remain follow-up integrations.
The graph is a rebuildable audit/dependency projection, not a hazard simulator.
Call `runtime.graph.rebuild(runtime.ledger)` and `affected_by(subject)` to inspect
dependencies; agents must emit explicit `depends_on` identifiers for mission-to-road
impact analysis to work.

## Integrating a planner into any agent

Implement an `Adapter` beside the owning agent under `agents/planning/`:

| Method | Contract |
|---|---|
| `candidates(state, budget)` | Async, bounded list of uniquely identified `Action`s |
| `feasible(state, action)` | Fast local constraint check; no I/O |
| `simulate(state, action, rng, budget)` | Async hypothetical `Transition(state, reward, terminal)` |
| `terminal(state)` | Fast local completion check |
| `fallback(state)` | Fast existing policy, or `None` if no feasible action exists |

The engine copies state before adapter calls. Simulation code must never import
or call live resource, notification, reservation, Gateway, or database writers.
Use canonical structured state: timestamps or random IDs that change every rollout
prevent outcome branches from sharing learned values. Keep rewards finite and
set `reward_scale` to the approximate magnitude of returns so UCB exploration is
not overwhelmed by utility units.

Example using the provided A3 adapter (inside an async agent/workflow):

```python
from atlas.agents.planning.commander import CommanderAdapter
from atlas.core.config import planner_config_for
from atlas.reasoning import Planner, PlanningRequest
from atlas.reasoning.tracing import record_result

snapshot = runtime.snapshot()
request = PlanningRequest(
    agent="a3_incident_commander",
    state=domain_state,  # routes, people, capacities and explicit scenario assumptions
    snapshot_version=snapshot["version"],
    valid_until=snapshot["valid_until"],
    evidence_ids=evidence_ids,
    assumptions=assumptions,
)
result = await Planner(planner_config_for(request.agent)).plan(request, CommanderAdapter())
result.require_current(runtime.cop.version)
audit_event = record_result(runtime.ledger, result, incident_id)
# Return audit_event through the existing handler interface.
# If result.action exists, A3 converts it into its reconciled ProposedAction.
# That proposal still needs the normal Gateway checks and execution-time validation.
```

Use `config/reasoning.yaml` defaults and per-agent overrides. `enabled` is a caller
switch, not permission to bypass policy. A1 consumes it automatically; other agents
must opt in. The engine works without an LLM. An LLM wrapper can
generate candidates, but every model call must use:

```python
answer = await budget.call_model(lambda: llm.generate_candidates(state))
```

The supplied callable must return an awaitable. Validate its output as `Action`s;
do not ask the LLM to invent physical transition probabilities or safety scores.
Adapters must cooperate with cancellation and avoid blocking synchronous I/O.
Deadlines cannot preempt arbitrary blocking Python or native code.

Timeout, exhausted model budget, adapter errors, insufficient root-action coverage,
or no feasible candidates return a checked fallback (possibly `None`). External
cancellation propagates; it does not create an action. Inspect `fallback_used` and
`stop_reason`. An expired search deliberately discards its partially searched winner.

`scenario` evaluates one step using common random seeds across candidates.
`mcts` searches multiple actions and sampled next states, backing up discounted
returns. Final selection can use the mean or the average of the worst configured
fraction of sampled returns. Quantiles are descriptive estimates, not calibrated
probabilities, confidence intervals, or safety guarantees. Search traces are
structured simulations, not hidden model reasoning or factual execution receipts.

## A1 behavior and limits

`config/sentinel.yaml` defines tabletop thresholds, source reliability, freshness,
rolling rainfall, rate-of-rise, hysteresis and escalation cooldown. These values
are not validated operational flood thresholds. Configure reliability server-side;
input reports cannot grant themselves a higher reliability weight.

Sources on the same sensor node are treated as one source for corroboration.
Independent matching sources above `corroboration_minimum` can increase confidence;
all evidence used in that increase is retained in provenance.
Fresh conflicting trusted sources produce a conflict flag and reduced confidence.
Only sufficiently confident, uncontradicted observations trigger the current
rules. Threshold alarms bypass reasoning. Persistent alarm state avoids repeated
triggers across readings and restarts; a falling score rearms it, or a sufficiently
larger score after cooldown escalates it. Incident IDs currently group by area;
the future A3 incident lifecycle should assign separate IDs to distinct episodes.

The optional Sentinel adapter compares verification recommendation, conflict flag,
and monitoring using an explicitly synthetic verification-success assumption.
A1 emits the recommendation to A3; it does not delegate tasks or dispatch resources.
The adapter demonstrates reuse and requires domain calibration before operational
use. Ordinary sensor ingestion is deterministic and requires no model.

Reports with only lat/lon but no mapped `area_id` are retained as rejected evidence
(`unmapped_location`) until C supplies area mapping. Stale input is retained without
updating current beliefs. Historical offline readings therefore cannot create new
current emergencies. Evidence and observation tables currently have no retention
job; plan retention before prolonged operation.

## Hardware and transport

Start Mosquitto **from the repository root** so `data/mqtt/` resolves. Start the
subscriber (`MQTT_ENABLED=true`) before the node's first transmission. The subscriber
uses a stable MQTT session and acknowledges valid messages after bus processing;
malformed messages are logged and acknowledged to avoid poison-message loops.

The Pi's outbox persists to `buffer_path`, retains stable measurement IDs across
retries. `runtime.sentinel.sensor_status()` supplies last-seen status for the console's
sensor panel and separates connection liveness from stale measurements arriving in a backlog.
The outbox retains readings across
retries, and removes a message after broker PUBACK or HTTP success. PUBACK means
broker acceptance, not end-to-end execution. Broker persistence and an established
subscriber session improve recovery but do not establish exactly-once delivery.
HTTP acceptance confirms durable ingestion, not that A1 considers the observation
fresh or actionable. Duplicates are expected and handled at ingestion.

When the bounded outbox fills, new readings are explicitly rejected/logged while
older buffered readings remain; provision capacity for the expected outage.
Test this policy, reconnect, calibration, systemd paths, and the physical rig on
the actual Pi. Hardware drivers and broker communication require bench validation.
The mock node's `flood_ramp` produces the same messages without hardware.
