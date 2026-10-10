# Control boundary, A8 Dispatch, A9 Comms, API and dashboard

How ATLAS turns a plan into action safely: the policy engine and Action Gateway decide, a person approves anything high-consequence, and only then do A8 (missions) and A9 (public alerts and reports) act. Every phase is written to the ledger.

## The flow

```
A3 --PLAN_PROPOSED--> Gateway (decide) --ACTION_AUTHORISED / ESCALATED / BLOCKED--> bus
                          |                       |
                          |                       +--> human: approve / edit / reject  (API)
                          v
                     ledger (every phase)
Gateway (execute, outside the bus transaction) --> A8 / A9 executors --> events --> bus
```

The bus runs each handler inside one database transaction and holds a non-reentrant
lock. So the Gateway is split in two:

- **decide** (`Gateway.submit`, and the `PLAN_PROPOSED` subscriber): database and ledger
  writes only. Returns events for the handler to return; never publishes.
- **execute** (`Gateway.run_pending`): runs from a worker outside any handler, calls the
  executors with verify, retry and compensate, and publishes what they produce.

A8 and A9 are **not** bus subscribers. The Gateway calls them, so nothing can reach them
without passing policy.

## Proposing actions (A3)

A3 emits one `PLAN_PROPOSED` event per plan:

```python
Event(type=EventType.PLAN_PROPOSED, source="a3_incident_commander", incident_id=iid,
      payload={"actions": [ProposedAction(...).model_dump(mode="json"), ...]})
```

`Gateway.submit(action) -> Decision` is also available if you want the decision inline
(synchronous, database-only, safe inside a handler). Then return
`gateway.events_for(decision)` from your handler so the outcome reaches the bus.

Rules the Gateway enforces, all visible in the `Decision.reason`:

| reason | meaning |
|---|---|
| `unauthorised_proposer` | `proposed_by` is not in `proposers` (only A3 by default) |
| `unknown_executor` / `unknown_action_type` | not in `config/policy.yaml` |
| `action_not_permitted_for_executor` | e.g. A9 asked to create a mission |
| `high_consequence` | waits for the Human Incident Commander |
| `stale_snapshot` | `snapshot_version` no longer matches the COP: **replan** |
| `idempotency_key_reuse` | same key, different content |
| `missing_idempotency_key`, `invalid_params`, `missing_*` | preflight failures |

Things to know when writing plans:

- **Send every parameter the policy inspects.** `create_mission` needs an integer
  `resources_committed` (more than 2 goes to a human). `publish_alert` needs `scope` (`local` or
  `district`) and `instruction`: only a local `avoid_area` advisory with no free text runs on its
  own; every evacuation order and every district-wide alert goes to a human, as the proposal
  commits. A missing or malformed value counts as *high*, so it goes to a human. This is
  deliberate (fail closed).
- Use a stable `idempotency_key` per intended action, not per attempt. Re-submitting the
  same key and content returns the stored decision and does nothing twice.
- A failed execution comes back as `ACTION_BLOCKED` with `decision.status` of `compensated`
  or `failed_uncompensated`. Treat it as a signal to replan.

### Action parameters

| action | executor | params |
|---|---|---|
| `create_mission` | a8 | `title`, `resources_committed` (int), optional `team`, `priority`, `destination`, `route` |
| `update_mission` | a8 | `mission_id`, optional `team`, `priority`, `destination`, `note` |
| `reroute_group` | a8 | `mission_id`, `route` (list of `[lat, lon]`) |
| `cancel_mission` | a8 | `mission_id`, optional `reason` |
| `close_major_route` | a8 | `road_id` (writes `edge:<road_id>` `open=False` to the COP) |
| `publish_alert` | a9 | `area`, `event` (`flood`/`landslide`/`cyclone` or free text), `scope`, optional `severity`, `instruction`/`instruction_text`, `languages`, `expires_minutes`, `circle` |
| `cancel_alert` | a9 | `alert_id` |
| `publish_sitrep` | a9 | optional `summary` |

Mission ids are `mission_id(idempotency_key)` from `atlas.agents.a8_dispatch`, so A3 can
refer to a mission it created without waiting for the receipt.

## Integration notes

- Everything is written through `Runtime`'s `Database`, `Ledger` and `EventBus`; nothing
  here touches their internals. Ledger records use kind `action`, ids
  `action:<key>:<phase>`.
- `attach_control(runtime)` must run **before** `runtime.start()` (startup recovery
  redelivers pending events). `create_app` already does this.
- The sensor panel reads raw sensor `OBSERVATION` events from the ledger and
  `Sentinel.sensor_status()`.

## Human review

`GET /approvals`, `POST /approvals/{id}/approve|edit|reject` with `{"reviewer": ...}`.
An edit changes `params` only, and the policy is re-checked on the edited action. Every
step is a ledger record. Set `ATLAS_OPERATOR_TOKEN` to require `X-Operator-Token` on
mutating routes; otherwise the API is open (development only). Reviewer names are asserted,
not authenticated.

## Execution guarantees and limits

- Statuses: `blocked`, `escalated`, `rejected`, `authorised`, `executing`, `executed`,
  `compensated`, `failed_uncompensated`. An action left `executing` by a crash is resumed.
- Executors must be idempotent on the idempotency key. A8 and A9 are.
- A public alert cannot be unsent. If delivery fails after retries the alert is marked
  `delivery_failed` and the action `compensated`; a human decides what to do next.
- The Sinhala and Tamil phrasebook needs native-speaker review before real use.
- `LogChannel` records deliveries; no SMS/WhatsApp gateway is connected.
- Periodic SITREPs must be proposed by A3 like any other action; nothing here schedules them.
- Run a single API worker (same constraint as the runtime).
