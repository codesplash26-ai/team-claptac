"""Developer view API: watch the agents talk.  (Owner: Dinuka)

Read-only. Nothing under ``/dev`` can change system state or propose an action.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from atlas.dashboard.dev.catalog import cards
from atlas.dashboard.dev.page import PAGE
from atlas.dashboard.dev.trace import chain, flow, get_event, trace

router = APIRouter(prefix="/dev")


def runtime_of(request: Request):
    return request.app.state.runtime


@router.get("", response_class=HTMLResponse)
async def dev_page():
    return PAGE


@router.get("/trace")
async def dev_trace(
    request: Request, after_seq: int = 0, limit: int = 200, incident_id: str | None = None
):
    events = trace(runtime_of(request), after_seq=after_seq, limit=limit, incident_id=incident_id)
    return [e.model_dump(mode="json") for e in events]


@router.get("/flow")
async def dev_flow(request: Request, incident_id: str | None = None):
    return [e.model_dump(mode="json") for e in flow(runtime_of(request), incident_id=incident_id)]


@router.get("/agents")
async def dev_agents(request: Request):
    return [c.model_dump(mode="json") for c in cards(runtime_of(request))]


@router.get("/incidents")
async def dev_incidents(request: Request):
    rows = runtime_of(request).db.connection.execute(
        "SELECT json_extract(body, '$.incident_id') AS incident, COUNT(*) AS events "
        "FROM ledger WHERE kind='event' AND json_extract(body, '$.incident_id') IS NOT NULL "
        "GROUP BY incident ORDER BY MAX(seq) DESC"
    )
    return [{"incident_id": r["incident"], "events": r["events"]} for r in rows]


@router.get("/chain/{event_id}")
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
