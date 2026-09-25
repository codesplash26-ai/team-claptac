"""The message log as data.  (Owner: Dinuka)

Each ``TraceEvent`` is one bus event with: its sequence number, type, sender, incident, a one-line
human summary, which subscribers handled it, and the event that caused it (when known). ``flow``
aggregates the same log into sender -> receiver counts for the agent-map diagram. All of it is
derived from the ledger, the bus's delivery records and its ``event_causes`` table; nothing is stored
separately, and nothing here can change system state.

Names on the diagram: agents keep their own names; every physical or replayed sensor is ``sensors``,
feed reports are ``feeds``, and people acting through the API are ``human``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

SENSORS, FEEDS, HUMAN, GATEWAY = "sensors", "feeds", "human", "action_gateway"
CHUNK = 500  # SQLite variable limit headroom


class TraceEvent(BaseModel):
    seq: int
    id: str
    type: str
    source: str  # the diagram node this came from
    raw_source: str  # the event's own ``source`` field
    incident_id: str | None = None
    created_at: datetime
    summary: str  # e.g. "A3 asks a4_evacuation for routes"
    handled_by: list[str] = Field(default_factory=list)
    caused_by: str | None = None
    payload: dict = Field(default_factory=dict)


class FlowEdge(BaseModel):
    source: str
    target: str
    kind: str  # "delivered" (bus), "execute" (Gateway to an executor), "review" (human decision)
    label: str  # event type or action type
    count: int
    last_seq: int = 0


def node_of(source: str) -> str:
    """Map an event's ``source`` field to a node on the diagram."""
    if source.startswith("sensor:"):
        return SENSORS
    if source in ("api", "human") or source.startswith("operator:"):
        return HUMAN
    if source.startswith(("demo:", "feed:", "official")):
        return FEEDS
    return source


def _chunks(items: list[str]):
    for i in range(0, len(items), CHUNK):
        yield items[i : i + CHUNK]


def _rows(runtime, *, after_seq: int, limit: int, incident_id: str | None):
    sql = "SELECT seq, body FROM ledger WHERE kind='event' AND seq>?"
    args: list[Any] = [after_seq]
    if incident_id:
        sql += " AND json_extract(body, '$.incident_id')=?"
        args.append(incident_id)
    sql += " ORDER BY seq LIMIT ?"
    args.append(limit)
    return runtime.db.connection.execute(sql, args).fetchall()


def _lookup(runtime, ids: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    handled: dict[str, list[str]] = {}
    caused: dict[str, str] = {}
    conn = runtime.db.connection
    for chunk in _chunks(ids):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT event_id, subscriber FROM deliveries WHERE event_id IN ({marks})", chunk
        ):
            handled.setdefault(row["event_id"], []).append(row["subscriber"])
        for row in conn.execute(
            f"SELECT event_id, cause_id FROM event_causes WHERE event_id IN ({marks})", chunk
        ):
            caused[row["event_id"]] = row["cause_id"]
    return handled, caused


def trace(
    runtime, *, after_seq: int = 0, limit: int = 200, incident_id: str | None = None
) -> list[TraceEvent]:
    """Events after ``after_seq`` in ledger order, with who handled them and what caused them."""
    limit = max(1, min(limit, 1000))
    rows = _rows(runtime, after_seq=after_seq, limit=limit, incident_id=incident_id)
    events = [(row["seq"], json.loads(row["body"])) for row in rows]
    handled, caused = _lookup(runtime, [e["id"] for _, e in events])
    return [
        TraceEvent(
            seq=seq,
            id=e["id"],
            type=e["type"],
            source=node_of(e["source"]),
            raw_source=e["source"],
            incident_id=e.get("incident_id"),
            created_at=e["created_at"],
            summary=summarise(e),
            handled_by=sorted(handled.get(e["id"], [])),
            caused_by=caused.get(e["id"]),
            payload=e.get("payload", {}),
        )
        for seq, e in events
    ]


def get_event(runtime, event_id: str) -> TraceEvent | None:
    row = runtime.db.connection.execute(
        "SELECT seq FROM ledger WHERE kind='event' AND record_id=?", ("event:" + event_id,)
    ).fetchone()
    if row is None:
        return None
    found = trace(runtime, after_seq=row["seq"] - 1, limit=1)
    return found[0] if found and found[0].id == event_id else None


def chain(runtime, event_id: str, *, max_depth: int = 50) -> dict[str, list[TraceEvent]]:
    """Everything that led to this event (``ancestors``, oldest first) and everything it led to
    (``descendants``, in ledger order)."""
    conn = runtime.db.connection
    ancestors: list[str] = []
    current = event_id
    for _ in range(max_depth):
        row = conn.execute(
            "SELECT cause_id FROM event_causes WHERE event_id=?", (current,)
        ).fetchone()
        if row is None or row["cause_id"] in ancestors:
            break
        ancestors.append(row["cause_id"])
        current = row["cause_id"]
    descendants: list[str] = []
    frontier = [event_id]
    seen = {event_id}
    while frontier and len(descendants) < 500:
        marks = ",".join("?" * len(frontier))
        frontier = [
            r["event_id"]
            for r in conn.execute(
                f"SELECT event_id FROM event_causes WHERE cause_id IN ({marks})", frontier
            )
            if r["event_id"] not in seen
        ]
        seen.update(frontier)
        descendants.extend(frontier)
    resolve = lambda ids: [e for e in (get_event(runtime, i) for i in ids) if e]  # noqa: E731
    return {
        "ancestors": list(reversed(resolve(ancestors))),
        "descendants": sorted(resolve(descendants), key=lambda e: e.seq),
    }


def flow(runtime, *, incident_id: str | None = None) -> list[FlowEdge]:
    """Sender -> receiver counts for the agent map, from the same records as ``trace``."""
    edges: dict[tuple[str, str, str, str], FlowEdge] = {}

    def add(source: str, target: str, kind: str, label: str, seq: int) -> None:
        key = (source, target, kind, label)
        edge = edges.setdefault(
            key, FlowEdge(source=source, target=target, kind=kind, label=label, count=0)
        )
        edge.count += 1
        edge.last_seq = max(edge.last_seq, seq)

    conn = runtime.db.connection
    args: list[Any] = []
    where = ""
    if incident_id:
        where = " AND json_extract(e.body, '$.incident_id')=?"
        args.append(incident_id)
    for row in conn.execute(
        "SELECT e.seq, json_extract(e.body, '$.source') AS source, "
        "json_extract(e.body, '$.type') AS type, d.subscriber AS subscriber "
        "FROM ledger e JOIN deliveries d ON d.event_id = substr(e.record_id, 7) "
        f"WHERE e.kind='event'{where}",
        args,
    ):
        target = row["subscriber"]
        source = node_of(row["source"])
        if source != target:
            add(source, target, "delivered", row["type"], row["seq"])
    # A8 and A9 are not bus subscribers: the Gateway calls them once an action is authorised.
    for row in conn.execute(
        "SELECT json_extract(action, '$.executor') AS executor, "
        "json_extract(action, '$.action_type') AS action_type, "
        "json_extract(action, '$.incident_id') AS incident, status FROM gateway_actions"
    ):
        if incident_id and row["incident"] != incident_id:
            continue
        if row["status"] in ("executed", "compensated", "failed_uncompensated", "executing"):
            add(GATEWAY, row["executor"], "execute", row["action_type"], 0)
    for row in conn.execute(
        "SELECT json_extract(action, '$.incident_id') AS incident, status FROM approvals"
    ):
        if incident_id and row["incident"] != incident_id:
            continue
        add(GATEWAY, HUMAN, "review", "needs approval", 0)
        if row["status"] != "pending":
            add(HUMAN, GATEWAY, "review", row["status"], 0)
    return sorted(edges.values(), key=lambda e: (e.source, e.target, e.kind, e.label))


# --------------------------------------------------------------------------- summaries


def _short(agent: str) -> str:
    return agent.split("_", 1)[0].upper() if agent[:1] == "a" and agent[1:2].isdigit() else agent


def summarise(event: dict) -> str:
    """One readable line per event, in the words a person would use."""
    kind, p, source = event["type"], event.get("payload") or {}, event.get("source", "")
    who = _short(node_of(source))
    if kind == "observation":
        if p.get("kind") == "sensor":
            return (
                f"sensor {p.get('node_id')} reads {p.get('type')} "
                f"{p.get('value')} {p.get('unit', '')}"
            ).strip()
        return f"report from {source}: {p.get('attribute')} {p.get('value')} {p.get('unit', '')}".strip()
    if kind == "observation_validated":
        return (
            f"{who} accepted {p.get('attribute')} {p.get('value')} {p.get('unit', '')} at "
            f"{p.get('area_id')} (confidence {p.get('confidence')})"
        )
    if kind == "observation_rejected":
        return f"{who} rejected a reading: {p.get('reason')}"
    if kind == "conflict_flag":
        return f"{who} found conflicting reports at {p.get('area_id')}"
    if kind == "information_gap":
        return f"{who}: information gap at {p.get('area_id')} ({p.get('recommendation')})"
    if kind == "trigger":
        return (
            f"{who} raised a trigger at {p.get('area_id')}: {p.get('reason')} "
            f"(score {p.get('score')})"
        )
    if kind == "risk_update":
        if p.get("kind") == "road_access_changed":
            return (
                f"{who}: {len(p.get('newly_closed', []))} road(s) closed, "
                f"{len(p.get('newly_reopened', []))} reopened near {p.get('area_id')}"
            )
        prev = p.get("previous_level") or "unknown"
        return f"{who}: {p.get('area_id')} {p.get('hazard')} risk {prev} -> {p.get('level')}"
    if kind == "task_assigned":
        return f"{who} asks {p.get('assignee')} for a {p.get('task')} plan"
    if kind == "candidate_plan":
        return _plan_summary(p)
    if kind == "plan_proposed":
        actions = p.get("actions", [])
        types = sorted({a.get("action_type", "?") for a in actions})
        flag = "; needs a human" if p.get("escalate") else ""
        return f"{who} proposes {len(actions)} action(s): {', '.join(types)}{flag}"
    if kind in ("action_authorised", "action_escalated", "action_blocked"):
        d, a = p.get("decision") or {}, p.get("action") or {}
        verb = kind.split("_", 1)[1]
        what = a.get("action_type") or p.get("reason") or "an action"
        return f"Gateway {verb}: {what} ({d.get('reason') or p.get('reason') or ''})".strip()
    if kind == "mission_status":
        return f"{who} mission '{p.get('title')}' is {p.get('status')} ({p.get('change')})"
    if kind == "alert_published":
        return f"{who} published a {p.get('kind')}" + (
            f" for {p.get('area')}" if p.get("area") else ""
        )
    if kind == "reasoning_completed":
        return f"{who} finished reasoning ({p.get('action') or p.get('summary') or ''})"
    return f"{who} {kind.replace('_', ' ')}"


def _plan_summary(p: dict) -> str:
    agent, status, plan = p.get("agent", "?"), p.get("status", "?"), p.get("plan") or {}
    detail = ""
    if agent == "a4_evacuation":
        detail = f", {len(plan.get('options', []))} route(s)"
    elif agent == "a7_shelter":
        detail = (
            f", {len(plan.get('placements', []))} placed, {len(plan.get('unplaced', []))} unplaced"
        )
    elif agent == "a6_logistics":
        detail = (
            f", {len(plan.get('assignments', []))} unit(s) assigned, "
            f"{len(plan.get('unserved', []))} unserved"
        )
    elif agent == "a5_triage":
        top = (plan.get("ranked_needs") or [{}])[0].get("area_id")
        detail = f", top priority {top}" if top else ""
        if str(p.get("task_id", "")).startswith("rerank:"):
            detail += " (unsolicited re-rank)"
    return f"{_short(agent)} plan {status}{detail}"
