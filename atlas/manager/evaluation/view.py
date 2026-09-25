"""What an evaluator can see of one trace.  (Owner: Dinuka)"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from atlas.dashboard.dev.runs import RunSummary, Span, list_runs, run_spans
from atlas.dashboard.dev.trace import TraceEvent, trace
from atlas.manager.observer import trace_id_for


@dataclass
class TraceView:
    incident_id: str
    trace_id: str
    run: RunSummary
    spans: list[Span]
    events: list[TraceEvent]
    actions: list[dict[str, Any]]  # every Gateway decision for the incident, with its action
    action_phases: dict[str, list[str]]  # idempotency key -> ledger phases in order
    incident: dict[str, Any]  # A3's own record of the incident
    alerts: list[dict[str, Any]]
    missions: list[Any]
    max_replans: int = 4
    extra: dict[str, Any] = field(default_factory=dict)

    def first(self, event_type: str) -> TraceEvent | None:
        return next((e for e in self.events if e.type == event_type), None)

    def all(self, event_type: str) -> list[TraceEvent]:
        return [e for e in self.events if e.type == event_type]


def build_views(system, *, incident_ids: list[str] | None = None) -> list[TraceView]:
    runtime = system.runtime
    control = system.control
    commander = system.orchestration.commander
    # The ledger is the source of truth: read phases from it, not from the Gateway's own table, so an
    # action recorded as executed with no authorisation is seen even if the table says nothing.
    phases: dict[str, dict[str, list[str]]] = {}
    for record in runtime.ledger.records("action"):
        body = record["payload"]
        if body.get("idempotency_key") and body.get("incident_id"):
            phases.setdefault(body["incident_id"], {}).setdefault(
                body["idempotency_key"], []
            ).append(body["phase"])
    views = []
    for run in list_runs(runtime):
        if incident_ids is not None and run.incident_id not in incident_ids:
            continue
        actions = control.gateway.list_actions(run.incident_id)
        views.append(
            TraceView(
                incident_id=run.incident_id,
                trace_id=trace_id_for(run.incident_id),
                run=run,
                spans=run_spans(runtime, run.incident_id),
                events=trace(runtime, incident_id=run.incident_id, limit=1000),
                actions=actions,
                action_phases=phases.get(run.incident_id, {}),
                incident=commander.get_incident(run.incident_id) or {},
                alerts=control.comms.list_alerts(run.incident_id),
                missions=control.dispatch.list(run.incident_id),
                max_replans=commander.config.max_replans,
            )
        )
    return views


def pretty(value: Any) -> str:
    return json.dumps(value, default=str)
