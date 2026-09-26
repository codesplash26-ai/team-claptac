"""Ledger-ordered timeline with measured agent work, including incident input ancestors."""

import json

from atlas.dashboard.dev.trace import _lookup, get_event, node_of, summarise


def category(kind):
    if kind in {"observation", "field_report", "mission_ack"}:
        return "input"
    if kind == "reasoning_completed":
        return "reasoning"
    if kind in {"plan_proposed", "action_authorised", "action_escalated", "action_blocked"}:
        return "decision"
    if kind in {"mission_status", "alert_published"}:
        return "outcome"
    return "work"


def enrich(runtime, events):
    activity = {}
    ids = [e["id"] for e in events]
    for offset in range(0, len(ids), 400):
        chunk = ids[offset : offset + 400]
        rows = runtime.db.connection.execute(
            f"SELECT * FROM event_spans WHERE event_id IN ({','.join('?' * len(chunk))}) "
            "ORDER BY started_at, subscriber",
            chunk,
        )
        for row in rows:
            activity.setdefault(row["event_id"], []).append(
                {
                    "agent": row["subscriber"],
                    "kind": row["kind"],
                    "started_at": row["started_at"],
                    "duration_ms": row["duration_ms"],
                    "status": "completed" if row["ok"] else "error",
                    "error": row["error"],
                    "outputs": row["outputs"],
                }
            )
    return [
        {**e, "category": category(e["type"]), "activity": activity.get(e["id"], [])}
        for e in events
    ]


def event_detail(runtime, event_id):
    event = get_event(runtime, event_id)
    return enrich(runtime, [event.model_dump(mode="json")])[0] if event else None


def planning_detail(runtime, event_id):
    event = get_event(runtime, event_id)
    if event is None or event.type != "reasoning_completed":
        return None
    ref = event.payload.get("reasoning_id")
    if not isinstance(ref, str):
        return None
    row = runtime.db.connection.execute(
        "SELECT body FROM ledger WHERE kind='reasoning' AND record_id=?", ("reasoning:" + ref,)
    ).fetchone()
    if row is None:
        return None
    record = json.loads(row[0])
    return record if record.get("agent") == event.raw_source else None


def timeline(runtime, *, limit=200, before_seq=None, incident_id=None):
    limit = max(1, min(limit, 500))
    args = []
    sql = ""
    # Include originating inputs even if they were recorded before an incident id existed.
    if incident_id:
        sql = """WITH RECURSIVE related(id) AS (
            SELECT substr(record_id,7) FROM ledger WHERE kind='event'
            AND json_extract(body,'$.incident_id')=?
            UNION SELECT c.cause_id FROM event_causes c JOIN related r ON c.event_id=r.id
        ) """
        args.append(incident_id)
    sql += "SELECT seq,body FROM ledger WHERE kind='event'"
    if incident_id:
        sql += " AND substr(record_id,7) IN (SELECT id FROM related)"
    if before_seq is not None:
        sql += " AND seq<?"
        args.append(before_seq)
    sql += " ORDER BY seq DESC LIMIT ?"
    rows = runtime.db.connection.execute(sql, [*args, limit + 1]).fetchall()
    has_older = len(rows) > limit
    entries = [(r["seq"], json.loads(r["body"])) for r in reversed(rows[:limit])]
    handled, caused = _lookup(runtime, [e["id"] for _, e in entries])
    events = [
        {
            "seq": seq,
            "id": e["id"],
            "type": e["type"],
            "source": node_of(e["source"]),
            "raw_source": e["source"],
            "incident_id": e.get("incident_id"),
            "created_at": e["created_at"],
            "summary": summarise(e),
            "handled_by": sorted(handled.get(e["id"], [])),
            "caused_by": caused.get(e["id"]),
            "payload": e.get("payload", {}),
        }
        for seq, e in entries
    ]
    return {
        "events": enrich(runtime, events),
        "has_older": has_older,
        "oldest_seq": events[0]["seq"] if events else None,
    }
