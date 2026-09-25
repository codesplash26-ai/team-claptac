"""Runs, spans and metrics: the agent manager's data.  (Owner: Dinuka)

The vocabulary follows tracing tools (LangSmith, OpenTelemetry) because that is what people already
read:

* a **run** is one incident, from the reading that started it to the last action it caused;
* a **span** is one unit of work inside it: an agent handling a message, the Gateway calling an executor
  (A8/A9), or a model call. It has a start, a duration, an input (the event it handled), outputs (the
  events it produced), a status, and a parent (the span that produced the event it is handling).

Everything is derived from the ledger, the bus's ``deliveries``, ``event_causes`` and ``event_spans``
tables, and the Gateway/approval tables. Read-only.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from pydantic import BaseModel, Field

from atlas.dashboard.dev.trace import GATEWAY, chain, node_of, summarise, trace

INPUT = "input"


class Span(BaseModel):
    id: str  # "<event id>:<who>:<kind>"
    parent_id: str | None = None
    name: str
    agent: str  # the diagram node that did the work
    kind: str  # handler | executor | llm | input
    event_id: str
    event_type: str
    incident_id: str | None = None
    start: float  # epoch seconds (wall clock)
    duration_ms: float
    status: str  # ok | error | escalated | blocked
    summary: str = ""
    error: str | None = None
    outputs: int = 0
    depth: int = 0


class RunSummary(BaseModel):
    incident_id: str
    area_id: str | None = None
    hazard: str | None = None
    state: str | None = None
    cycles: int = 0
    replans: int = 0
    needs_human: bool = False
    started: float
    ended: float
    duration_ms: float
    spans: int
    errors: int
    events: int
    agents: list[str] = Field(default_factory=list)
    summary: str = ""


def _incident_states(runtime) -> dict[str, dict]:
    try:
        rows = runtime.db.connection.execute("SELECT body FROM a3_incidents").fetchall()
    except Exception:  # A3 is not wired on this runtime
        return {}
    return {(b := json.loads(r["body"]))["incident_id"]: b for r in rows}


def list_runs(runtime) -> list[RunSummary]:
    conn = runtime.db.connection
    states = _incident_states(runtime)
    rows = conn.execute(
        "SELECT json_extract(body, '$.incident_id') AS incident, COUNT(*) AS events, "
        "MIN(seq) AS first FROM ledger WHERE kind='event' AND "
        "json_extract(body, '$.incident_id') IS NOT NULL GROUP BY incident ORDER BY MAX(seq) DESC"
    ).fetchall()
    out = []
    for row in rows:
        spans = run_spans(runtime, row["incident"])
        if not spans:
            continue
        state = states.get(row["incident"], {})
        start = min(s.start for s in spans)
        end = max(s.start + s.duration_ms / 1000 for s in spans)
        out.append(
            RunSummary(
                incident_id=row["incident"],
                area_id=state.get("area_id"),
                hazard=state.get("hazard"),
                state=state.get("state"),
                cycles=state.get("cycle", 0),
                replans=state.get("replans", 0),
                needs_human=bool(state.get("needs_human")),
                started=start,
                ended=end,
                duration_ms=round((end - start) * 1000, 1),
                spans=len(spans),
                errors=sum(1 for s in spans if s.status == "error"),
                events=row["events"],
                agents=sorted({s.agent for s in spans}),
                summary=state.get("summary", "")[:300],
            )
        )
    return out


def _event_ids_for_run(runtime, incident_id: str) -> list[str]:
    """The run's own events, everything that led to them, and everything they led to."""
    ids = [e.id for e in trace(runtime, incident_id=incident_id, limit=1000)]
    seen = dict.fromkeys(ids)
    conn = runtime.db.connection
    frontier = list(ids)
    while frontier:  # ancestors: the readings and triggers that started it
        marks = ",".join("?" * len(frontier))
        frontier = [
            r["cause_id"]
            for r in conn.execute(
                f"SELECT cause_id FROM event_causes WHERE event_id IN ({marks})", frontier
            )
            if r["cause_id"] not in seen
        ]
        seen.update(dict.fromkeys(frontier))
    frontier = list(seen)
    while frontier:  # descendants: what it caused, including executor output
        marks = ",".join("?" * len(frontier))
        frontier = [
            r["event_id"]
            for r in conn.execute(
                f"SELECT event_id FROM event_causes WHERE cause_id IN ({marks})", frontier
            )
            if r["event_id"] not in seen
        ]
        seen.update(dict.fromkeys(frontier))
    return list(seen)


def run_spans(runtime, incident_id: str) -> list[Span]:
    """Every span of a run, as a tree in start order (``parent_id`` and ``depth`` say where)."""
    conn = runtime.db.connection
    event_ids = _event_ids_for_run(runtime, incident_id)
    if not event_ids:
        return []
    events = {}
    for start in range(0, len(event_ids), 400):
        chunk = event_ids[start : start + 400]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT body FROM ledger WHERE kind='event' AND record_id IN ({marks})",
            ["event:" + i for i in chunk],
        ):
            body = json.loads(row["body"])
            events[body["id"]] = body
    causes = {
        r["event_id"]: (r["cause_id"], r["subscriber"])
        for start in range(0, len(event_ids), 400)
        for r in conn.execute(
            "SELECT event_id, cause_id, subscriber FROM event_causes WHERE event_id IN (%s)"
            % ",".join("?" * len(event_ids[start : start + 400])),
            event_ids[start : start + 400],
        )
    }
    spans: dict[str, Span] = {}
    for start in range(0, len(event_ids), 400):
        chunk = event_ids[start : start + 400]
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT * FROM event_spans WHERE event_id IN ({marks}) ORDER BY started_at", chunk
        ):
            event = events.get(row["event_id"])
            if event is None:
                continue
            span_id = f"{row['event_id']}:{row['subscriber']}:{row['kind']}"
            flag = {"action_blocked": "blocked", "action_escalated": "escalated"}.get(event["type"])
            spans[span_id] = Span(
                id=span_id,
                name=f"{row['subscriber']}  <-  {event['type']}",
                agent=row["subscriber"],
                kind=row["kind"],
                event_id=row["event_id"],
                event_type=event["type"],
                incident_id=event.get("incident_id"),
                start=row["started_at"],
                duration_ms=row["duration_ms"],
                status="error" if not row["ok"] else (flag or "ok"),
                summary=summarise(event),
                error=row["error"],
                outputs=row["outputs"],
            )
    # Input spans: events nothing in the system caused (a sensor reading, a report, a human action).
    for event_id, event in events.items():
        if event_id in causes:
            continue
        origin = node_of(event["source"])
        started = min((s.start for s in spans.values() if s.event_id == event_id), default=None)
        if started is None:
            continue
        span_id = f"{event_id}:{origin}:{INPUT}"
        spans[span_id] = Span(
            id=span_id,
            name=f"{origin}  ->  {event['type']}",
            agent=origin,
            kind=INPUT,
            event_id=event_id,
            event_type=event["type"],
            incident_id=event.get("incident_id"),
            start=started - 0.0005,
            duration_ms=0.5,
            status="ok",
            summary=summarise(event),
            outputs=1,
        )
    for span in spans.values():
        cause = causes.get(span.event_id)
        if span.kind == INPUT or cause is None:
            continue
        cause_event, producer = cause
        for candidate in (
            f"{cause_event}:{producer}:handler",
            f"{cause_event}:{node_of(events[span.event_id]['source'])}:executor",
        ):
            if candidate in spans:
                span.parent_id = candidate
                break
        if span.kind == "executor":  # an executor call hangs off the message that authorised it
            span.parent_id = span.parent_id or (
                f"{cause_event}:{producer}:handler"
                if f"{cause_event}:{producer}:handler" in spans
                else None
            )
    # A span whose event has an input-span origin: attach handlers of input events to that input span.
    for span in spans.values():
        if span.parent_id is None and span.kind != INPUT and span.event_id in events:
            origin = node_of(events[span.event_id]["source"])
            candidate = f"{span.event_id}:{origin}:{INPUT}"
            if candidate in spans:
                span.parent_id = candidate
    return _order(list(spans.values()))


def _order(spans: list[Span]) -> list[Span]:
    """Depth-first, children by start time, so a table of spans reads as a tree."""
    by_parent: dict[str | None, list[Span]] = {}
    ids = {s.id for s in spans}
    for span in sorted(spans, key=lambda s: (s.start, s.id)):
        parent = span.parent_id if span.parent_id in ids else None
        by_parent.setdefault(parent, []).append(span)
    out: list[Span] = []

    def walk(parent: str | None, depth: int) -> None:
        for span in by_parent.get(parent, []):
            span.depth = depth
            out.append(span)
            walk(span.id, depth + 1)

    walk(None, 0)
    return out


def span_detail(runtime, span_id: str) -> dict[str, Any] | None:
    """The input event, the events this span produced, and its cause."""
    event_id, _, rest = span_id.partition(":")
    who, _, kind = rest.rpartition(":")
    conn = runtime.db.connection
    row = conn.execute(
        "SELECT * FROM event_spans WHERE event_id=? AND subscriber=? AND kind=?",
        (event_id, who, kind),
    ).fetchone()
    body = conn.execute(
        "SELECT body FROM ledger WHERE kind='event' AND record_id=?", ("event:" + event_id,)
    ).fetchone()
    if body is None or (row is None and kind != INPUT):
        return None
    event = json.loads(body["body"])
    produced_ids = [
        r["event_id"]
        for r in conn.execute(
            "SELECT event_id FROM event_causes WHERE cause_id=? AND subscriber=?", (event_id, who)
        )
    ]
    produced = []
    for pid in produced_ids:
        b = conn.execute(
            "SELECT seq, body FROM ledger WHERE kind='event' AND record_id=?", ("event:" + pid,)
        ).fetchone()
        if b:
            e = json.loads(b["body"])
            produced.append(
                {
                    "id": pid,
                    "type": e["type"],
                    "source": e["source"],
                    "summary": summarise(e),
                    "payload": e.get("payload", {}),
                    "seq": b["seq"],
                }
            )
    cause = conn.execute(
        "SELECT cause_id, subscriber FROM event_causes WHERE event_id=?", (event_id,)
    ).fetchone()
    return {
        "id": span_id,
        "agent": who,
        "kind": kind,
        "input": {
            "id": event_id,
            "type": event["type"],
            "source": event["source"],
            "incident_id": event.get("incident_id"),
            "created_at": event["created_at"],
            "summary": summarise(event),
            "payload": event.get("payload", {}),
        },
        "outputs": sorted(produced, key=lambda e: e["seq"]),
        "caused_by": {"event_id": cause["cause_id"], "by": cause["subscriber"]} if cause else None,
        "metadata": {
            "duration_ms": row["duration_ms"] if row else 0.0,
            "started_at": row["started_at"] if row else None,
            "ok": bool(row["ok"]) if row else True,
            "error": row["error"] if row else None,
            "chain": [e.summary for e in chain(runtime, event_id)["ancestors"]],
        },
    }


# ------------------------------------------------------------------------------- metrics


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 3)


def metrics(runtime) -> dict[str, Any]:
    conn = runtime.db.connection
    per: dict[tuple[str, str], list[float]] = {}
    errors: dict[tuple[str, str], int] = {}
    for r in conn.execute("SELECT subscriber, kind, duration_ms, ok FROM event_spans"):
        key = (r["subscriber"], r["kind"])
        per.setdefault(key, []).append(r["duration_ms"])
        errors[key] = errors.get(key, 0) + (0 if r["ok"] else 1)
    agents = [
        {
            "agent": a,
            "kind": k,
            "runs": len(v),
            "errors": errors.get((a, k), 0),
            "p50_ms": _pct(v, 0.5),
            "p95_ms": _pct(v, 0.95),
            "max_ms": round(max(v), 3),
            "total_ms": round(sum(v), 1),
        }
        for (a, k), v in sorted(per.items())
    ]
    by_type = {
        r["type"]: r["n"]
        for r in conn.execute(
            "SELECT json_extract(body, '$.type') AS type, COUNT(*) AS n FROM ledger "
            "WHERE kind='event' GROUP BY type ORDER BY n DESC"
        )
    }
    return {
        "agents": agents,
        "events_by_type": by_type,
        "latency": _latency(runtime),
        "control": _control(runtime),
        "llm": {
            "calls": sum(1 for a in agents if a["kind"] == "llm"),
            "tokens": 0,
            "cost_usd": 0.0,
        },
    }


def _latency(runtime) -> list[dict[str, Any]]:
    """Per run: how long from the reading to each milestone, in *simulated* seconds (the events' own
    timestamps) and in wall milliseconds (the spans), which differ under a replay."""
    out = []
    for run in list_runs(runtime):
        events = trace(runtime, incident_id=run.incident_id, limit=1000)
        firsts: dict[str, Any] = {}
        for e in events:
            firsts.setdefault(e.type, e)
        trigger = firsts.get("trigger")
        if trigger is None:
            continue
        observed = trigger.payload.get("observed_at")
        milestones = {}
        for name, kind in (
            ("trigger", "trigger"),
            ("plan_proposed", "plan_proposed"),
            ("authorised", "action_authorised"),
            ("alert_sent", "alert_published"),
            ("mission_created", "mission_status"),
        ):
            if kind in firsts:
                milestones[name] = firsts[kind]
        spans = run_spans(runtime, run.incident_id)
        wall0 = min(s.start for s in spans)
        item: dict[str, Any] = {
            "incident_id": run.incident_id,
            "detection_latency_seconds": trigger.payload.get("detection_latency_seconds"),
            "milestones": {},
        }
        for name, event in milestones.items():
            first_span = next((s for s in spans if s.event_id == event.id), None)
            item["milestones"][name] = {
                "sim_seconds_after_trigger": _seconds(trigger.created_at, event.created_at),
                "wall_ms_after_start": round((first_span.start - wall0) * 1000, 1)
                if first_span
                else None,
            }
        item["observed_at"] = observed
        out.append(item)
    return out


def _seconds(a, b) -> float:
    return round((b - a).total_seconds(), 3)


def _control(runtime) -> dict[str, Any]:
    conn = runtime.db.connection
    try:
        decisions = {
            r["status"]: r["n"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM gateway_actions GROUP BY status"
            )
        }
        approvals = [
            dict(r) for r in conn.execute("SELECT status, created_at, resolved_at FROM approvals")
        ]
        consequence = {
            r["c"] or "n/a": r["n"]
            for r in conn.execute(
                "SELECT consequence AS c, COUNT(*) AS n FROM gateway_actions GROUP BY c"
            )
        }
    except Exception:  # control plane not wired
        return {}
    from datetime import datetime

    waits = [
        (
            datetime.fromisoformat(a["resolved_at"]) - datetime.fromisoformat(a["created_at"])
        ).total_seconds()
        for a in approvals
        if a["resolved_at"]
    ]
    total = sum(decisions.values())
    resolved = {
        s: sum(1 for a in approvals if a["status"] == s) for s in ("approved", "edited", "rejected")
    }
    autonomous = total - len(approvals)
    return {
        "actions": total,
        "by_status": decisions,
        "by_consequence": consequence,
        "escalated": len(approvals),
        "pending_approvals": sum(1 for a in approvals if a["status"] == "pending"),
        "resolved": resolved,
        "autonomous": autonomous,
        "autonomy_rate": round(autonomous / total, 3) if total else None,
        "human_edit_or_reject_rate": round(
            (resolved["edited"] + resolved["rejected"]) / max(1, sum(resolved.values())), 3
        ),
        "median_decision_seconds": round(statistics.median(waits), 3) if waits else None,
        "blocked": decisions.get("blocked", 0),
        "gateway": GATEWAY,
    }
