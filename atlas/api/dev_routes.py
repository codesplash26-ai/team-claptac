"""Developer view API: watch the agents talk.  (Owner: Dinuka)

Read-only. Nothing under ``/dev`` can change system state or propose an action.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from atlas.dashboard.dev.catalog import cards
from atlas.dashboard.dev.geo_view import situation_map
from atlas.dashboard.dev.page import PAGE
from atlas.dashboard.dev.runs import list_runs, metrics, run_spans, span_detail
from atlas.dashboard.dev.timeline import event_detail, planning_detail, timeline
from atlas.dashboard.dev.trace import chain, flow, get_event, trace
from atlas.manager import rbac
from atlas.manager.rbac import requires

router = APIRouter(prefix="/dev")


def runtime_of(request: Request):
    return request.app.state.runtime


@router.get("/timeline", dependencies=requires(rbac.TRACE_READ))
async def dev_timeline(
    request: Request,
    limit: int = Query(200, ge=1, le=500),
    before_seq: int | None = Query(None, ge=1),
    incident_id: str | None = None,
):
    return timeline(
        runtime_of(request), limit=limit, before_seq=before_seq, incident_id=incident_id
    )


@router.get("/timeline/{event_id}", dependencies=requires(rbac.TRACE_READ))
async def dev_timeline_event(event_id: str, request: Request):
    item = event_detail(runtime_of(request), event_id)
    if item is None:
        raise HTTPException(404, "unknown event")
    return item


@router.get("/timeline/{event_id}/planning", dependencies=requires(rbac.TRACE_READ))
async def dev_timeline_planning(event_id: str, request: Request):
    item = planning_detail(runtime_of(request), event_id)
    if item is None:
        raise HTTPException(404, "No planner trace recorded for this event")
    return item


@router.get("", response_class=HTMLResponse, dependencies=requires(rbac.RUN_READ))
async def dev_page():
    return PAGE


@router.get("/trace", dependencies=requires(rbac.TRACE_READ))
async def dev_trace(
    request: Request,
    after_seq: int = 0,
    limit: int = 200,
    incident_id: str | None = None,
    tail: int | None = None,
):
    events = trace(
        runtime_of(request), after_seq=after_seq, limit=limit, incident_id=incident_id, tail=tail
    )
    return [e.model_dump(mode="json") for e in events]


@router.get("/flow", dependencies=requires(rbac.TRACE_READ))
async def dev_flow(request: Request, incident_id: str | None = None):
    return [e.model_dump(mode="json") for e in flow(runtime_of(request), incident_id=incident_id)]


@router.get("/agents", dependencies=requires(rbac.AGENT_READ))
async def dev_agents(request: Request):
    return [c.model_dump(mode="json") for c in cards(runtime_of(request))]


@router.get("/incidents", dependencies=requires(rbac.RUN_READ))
async def dev_incidents(request: Request):
    rows = runtime_of(request).db.connection.execute(
        "SELECT json_extract(body, '$.incident_id') AS incident, COUNT(*) AS events "
        "FROM ledger WHERE kind='event' AND json_extract(body, '$.incident_id') IS NOT NULL "
        "GROUP BY incident ORDER BY MAX(seq) DESC"
    )
    return [{"incident_id": r["incident"], "events": r["events"]} for r in rows]


@router.get("/chain/{event_id}", dependencies=requires(rbac.TRACE_READ))
async def dev_chain(event_id: str, request: Request):
    runtime = runtime_of(request)
    event = get_event(runtime, event_id)
    if event is None:
        raise HTTPException(404, "unknown event")
    linked = chain(runtime, event_id)
    return {
        "event": event.model_dump(mode="json"),
        "ancestors": [e.model_dump(mode="json") for e in linked["ancestors"]],
        "descendants": [e.model_dump(mode="json") for e in linked["descendants"]],
    }


@router.get("/runs", dependencies=requires(rbac.RUN_READ))
async def dev_runs(request: Request):
    return [r.model_dump(mode="json") for r in list_runs(runtime_of(request))]


@router.get("/runs/{incident_id}/spans", dependencies=requires(rbac.TRACE_READ))
async def dev_run_spans(incident_id: str, request: Request):
    spans = run_spans(runtime_of(request), incident_id)
    if not spans:
        raise HTTPException(404, "unknown run")
    return [s.model_dump(mode="json") for s in spans]


@router.get("/span", dependencies=requires(rbac.TRACE_READ))
async def dev_span(id: str, request: Request):
    detail = span_detail(runtime_of(request), id)
    if detail is None:
        raise HTTPException(404, "unknown span")
    return detail


@router.get("/metrics", dependencies=requires(rbac.RUN_READ))
async def dev_metrics(request: Request):
    return metrics(runtime_of(request))


@router.get("/map", dependencies=requires(rbac.RUN_READ))
async def dev_map(request: Request):
    return situation_map(request.app.state.system)


@router.get("/history", dependencies=requires(rbac.RUN_READ))
async def dev_history(hazard: str | None = None):
    """The committed hazard history (real events, with sources) for the timeline."""
    import json

    from atlas.datasets.catalog import HISTORY_PATH

    data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    if hazard:
        data["events"] = [e for e in data["events"] if e["hazard"] == hazard]
    return data
