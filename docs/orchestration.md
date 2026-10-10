# Orchestration: A2 Hazard, A3 Incident Commander, A5 Triage, and the LLM client

How an incident is run: A2 rates the risk, A3 opens an incident and asks the specialists in order, reconciles their answers into one plan and sends it to the Gateway, and A5 ranks who needs help first. The LLM only advises; the plan comes from deterministic tools.

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

The event bus runs every handler inside one database transaction and one lock. A model call in a handler
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
