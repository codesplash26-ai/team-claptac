# Orchestration and intelligence (Workstream B): A2, A3, A5, LLM client

This is Hasindu's workstream, ported onto the real platform. The PR that introduced it (#3) was built
against the original skeleton and carried its own in-memory bus, COP, ledger, Gateway and A4 stubs, so
it could not merge next to A, C and D. The agent logic and the LLM client are kept; the wiring is new.

## What changed from the original PR, and why

| Original | Now | Reason |
|---|---|---|
| Own `EventBus`, `CommonOperatingPicture`, `ActionLedger`, `Settings` | the platform's (`Runtime`) | one shared bus, COP and ledger; the stubs conflicted in six files |
| Stub `ActionGateway` that approved everything | the real Gateway | policy, approvals and the ledger are the safety story |
| Stub A4 `plan_evacuation` | Sumudu's A4, A6, A7 | real roads, capacity and units |
| A3 called `gateway.submit()` **and** emitted `PLAN_PROPOSED{action}` | A3 emits one `PLAN_PROPOSED{actions: [...]}` | the Gateway is a bus subscriber; the old shape was submitted twice or not at all |
| A3 handlers published events themselves | handlers return events | the bus holds a lock and a transaction; recursive publish deadlocks |
| A3 kept incidents in a Python dict | `a3_incidents` table | survives restarts; several incidents at once, one row each |
| A3 sent `TASK_ASSIGNED{task: "evacuate"}` | typed tasks with `assignee` (`atlas/agents/contracts.py`) | the specialists only act on tasks addressed to them |
| `create_mission` without `title` / `resources_committed` | full A8 contract | otherwise it escalated to a human and then failed |
| A2 read raw `OBSERVATION` | A2 reads `OBSERVATION_VALIDATED` | raw input bypasses the Sentinel's validation |
| A2 emitted a risk update per reading, wrote the COP every time | only on a level change | every COP write moves the version and can make an in-flight plan stale |
| A2 levels from fixed centimetre thresholds | ratios of A1's thresholds | A1 and A2 must agree on what is alarming |
| A5 emitted a plan on every risk update | plans only for tasks, plus one `rerank:` plan when the top priority moves | otherwise A3 is spammed |
| A5 also consumed A4's road updates as risk | `RISK_UPDATE` carries `kind`; A5 reads only `hazard_risk` | A4 uses the same event type for a different meaning |
| LLM orchestrated and could propose any action | LLM is not on the critical path | see below |

## The loop

```
TRIGGER (A1) --> A3 assessing   TASK_ASSIGNED a5_triage  --> CANDIDATE_PLAN
              --> A3 planning    TASK_ASSIGNED a4_evacuation --> CANDIDATE_PLAN (RoutePlan)
                                 TASK_ASSIGNED a7_shelter + a6_logistics (parallel) --> 2 plans
                                 TASK_ASSIGNED a6_logistics (resupply, only if A7 asks) --> plan
              --> reconcile --> PLAN_PROPOSED{actions} --> Gateway --> ACTION_* --> A3
              --> A8 / A9 execute --> MISSION_STATUS / ALERT_PUBLISHED --> A3
```

A3 only asks and reconciles. Routes, scores and allocations are computed by A4-A7. Reconciliation
(`atlas/orchestration/reconcile.py`) is a pure function, so it is unit-tested without a bus.

**Replans** happen when: A4 reports roads closing or reopening, A2 raises the level to severe, A5's top
priority moves, a mission fails, a trigger escalates, the Gateway reports `stale_snapshot`, or a plan's
inputs went stale before it could be proposed. They are bounded (`CommanderConfig.max_replans`); past the
bound `needs_human` is set instead of looping.

**What A3 will not do on its own:** cancel a mission in flight because routes vanished (evacuees are
moving; a human decides), send a softer public alert after a stronger one, or repeat an alert.

## Why the LLM is off the critical path

Aeshan's bus runs every handler inside one database transaction and one lock. A model call in a handler
blocks all ingestion for seconds, and a stalled call would freeze the whole system. The proposal also says
the LLM must not compute safety-critical values. So the deterministic core acts. The LLM client (`atlas/tools/llm.py`, unchanged in behaviour) is kept for an
advisory role outside the transaction (narrative, critique), which is the next piece of work and is not in
this PR. With no key, or the provider down, the loop is unchanged. `tests/orchestration/test_end_to_end.py::test_loop_needs_no_llm` pins this.

## Assumptions to check

- Evacuation waves: `group_size` 40, `max_groups` 3 per cycle (`CommanderConfig`). The remaining people
  wait for a later cycle. A real deployment would size groups from shelter capacity and vehicle seats.
- One incident per area (`incident:<area>`), as the Sentinel names them.
- A `rescue_standby` mission holds one rescue team and one vehicle at the incident.
- Vulnerable-population profiles (`data/reference/vulnerable_populations.json`) are demonstration data.
- Only A3 should delegate. This is followed by construction (A3 is the only emitter of `TASK_ASSIGNED`),
  and asserted in a test, but the specialists do not yet reject a `TASK_ASSIGNED` from another source.

## Wiring

```python
from atlas.system import attach_all
system = attach_all(runtime, site="ratnapura")   # before ``await runtime.start()``
system.control.start_worker()                    # inside a running event loop
```

`create_app` does this. `attach_orchestration` registers A2, A5 and A3; the others are
`attach_routing` and `attach_control`.
