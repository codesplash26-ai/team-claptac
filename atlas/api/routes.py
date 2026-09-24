"""Control-boundary API: incidents, approvals, ledger, missions, alerts, dashboard.

Every mutating route is either a human decision (approve / edit / reject) or a field
update on an existing mission. There is deliberately no route that proposes or executes
an action: only A3 proposes, and only the Gateway executes.

Set ``ATLAS_OPERATOR_TOKEN`` to require an ``X-Operator-Token`` header on mutating
routes. Without it the API is open, which is a development default only.
"""

from __future__ import annotations

import hmac
import json
import os

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from atlas.agents.a8_dispatch import MissionError, MissionStatus
from atlas.control.approvals import ApprovalError
from atlas.control.models import ApprovalStatus
from atlas.dashboard import build_map_html, render_dashboard, sensor_panel

router = APIRouter()


def control_of(request: Request):
    return request.app.state.control


def require_operator(x_operator_token: str | None = Header(default=None)):
    expected = os.getenv("ATLAS_OPERATOR_TOKEN")
    if expected and not hmac.compare_digest(x_operator_token or "", expected):
        raise HTTPException(401, "operator token required")


operator = [Depends(require_operator)]


class Review(BaseModel):
    reviewer: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=500)


class EditReview(Review):
    params: dict


class Progress(BaseModel):
    percent: int = Field(ge=0, le=100)
    note: str = Field(default="", max_length=500)


class Ack(BaseModel):
    by: str = Field(default="", max_length=80)


class ExceptionReport(BaseModel):
    description: str = Field(min_length=1, max_length=500)
    fatal: bool = False


# ------------------------------------------------------------------ incidents


def _incident_ids(control) -> dict[str, dict]:
    incidents: dict[str, dict] = {}
    rows = control.runtime.db.connection.execute(
        "SELECT body FROM ledger WHERE kind='event' ORDER BY seq"
    )
    for row in rows:
        event = json.loads(row["body"])
        if not event.get("incident_id"):
            continue
        item = incidents.setdefault(
            event["incident_id"],
            {"incident_id": event["incident_id"], "first_seen": event["created_at"], "events": 0},
        )
        item["events"] += 1
        item["last_seen"] = event["created_at"]
    for entry in control.gateway.list_actions():  # incidents known only from actions
        incidents.setdefault(
            entry["action"]["incident_id"],
            {
                "incident_id": entry["action"]["incident_id"],
                "first_seen": entry["decision"]["decided_at"],
                "events": 0,
                "last_seen": entry["decision"]["updated_at"],
            },
        )
    return incidents


@router.get("/incidents")
async def list_incidents(request: Request):
    control = control_of(request)
    out = []
    for item in _incident_ids(control).values():
        iid = item["incident_id"]
        out.append(
            {
                **item,
                "pending_approvals": sum(
                    1 for a in control.approvals.pending() if a.incident_id == iid
                ),
                "active_missions": sum(
                    1
                    for m in control.dispatch.list(iid)
                    if m.status
                    in (
                        MissionStatus.CREATED,
                        MissionStatus.ACKNOWLEDGED,
                        MissionStatus.IN_PROGRESS,
                    )
                ),
            }
        )
    return sorted(out, key=lambda i: i["last_seen"], reverse=True)


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str, request: Request):
    control = control_of(request)
    known = _incident_ids(control).get(incident_id)
    if known is None:
        raise HTTPException(404, "unknown incident")
    return {
        **known,
        "actions": control.gateway.list_actions(incident_id),
        "approvals": [
            a.model_dump(mode="json")
            for a in control.approvals.list()
            if a.incident_id == incident_id
        ],
        "missions": [m.model_dump(mode="json") for m in control.dispatch.list(incident_id)],
        "alerts": [
            {k: v for k, v in a.items() if k != "cap_xml"}
            for a in control.comms.list_alerts(incident_id)
        ],
        "sitreps": control.comms.list_sitreps(incident_id),
    }


# ------------------------------------------------------------------ approvals


@router.get("/approvals")
async def list_approvals(request: Request, status: ApprovalStatus | None = None):
    control = control_of(request)
    items = control.approvals.list(status) if status else control.approvals.pending()
    return [i.model_dump(mode="json") for i in items]


async def _resolve(request, background, approval_id, call):
    control = control_of(request)
    try:
        decision = call(control.gateway)
    except ApprovalError as error:
        code = 404 if str(error).startswith("unknown") else 409
        raise HTTPException(code, str(error)) from error
    await control.gateway.emit(decision)
    background.add_task(control.gateway.run_pending)
    return decision.model_dump(mode="json")


@router.post("/approvals/{approval_id}/approve", dependencies=operator)
async def approve(approval_id: str, body: Review, request: Request, background: BackgroundTasks):
    return await _resolve(
        request, background, approval_id, lambda g: g.approve(approval_id, body.reviewer, body.note)
    )


@router.post("/approvals/{approval_id}/edit", dependencies=operator)
async def edit(approval_id: str, body: EditReview, request: Request, background: BackgroundTasks):
    return await _resolve(
        request,
        background,
        approval_id,
        lambda g: g.edit(approval_id, body.reviewer, body.params, body.note),
    )


@router.post("/approvals/{approval_id}/reject", dependencies=operator)
async def reject(approval_id: str, body: Review, request: Request, background: BackgroundTasks):
    return await _resolve(
        request, background, approval_id, lambda g: g.reject(approval_id, body.reviewer, body.note)
    )


# --------------------------------------------------------------------- ledger


@router.get("/ledger")
async def ledger(request: Request, kind: str | None = None, after_seq: int = 0, limit: int = 100):
    limit = max(1, min(limit, 500))
    db = control_of(request).runtime.db
    rows = db.connection.execute(
        "SELECT seq, record_id, kind, body, hash FROM ledger WHERE seq>? AND (? IS NULL OR kind=?)"
        " ORDER BY seq LIMIT ?",
        (after_seq, kind, kind, limit),
    )
    return [
        {
            "seq": r["seq"],
            "record_id": r["record_id"],
            "kind": r["kind"],
            "hash": r["hash"],
            "payload": json.loads(r["body"]),
        }
        for r in rows
    ]


@router.get("/ledger/verify")
async def verify_ledger(request: Request):
    return {"valid": control_of(request).runtime.ledger.verify()}


# ------------------------------------------------------------------- missions


@router.get("/missions")
async def list_missions(
    request: Request, incident_id: str | None = None, status: MissionStatus | None = None
):
    return [
        m.model_dump(mode="json") for m in control_of(request).dispatch.list(incident_id, status)
    ]


@router.get("/missions/{mission_id}")
async def get_mission(mission_id: str, request: Request):
    dispatch = control_of(request).dispatch
    mission = dispatch.get(mission_id)
    if mission is None:
        raise HTTPException(404, "unknown mission")
    return {**mission.model_dump(mode="json"), "history": dispatch.history(mission_id)}


async def _field_update(request, mission_id, make_event):
    control = control_of(request)
    try:
        event = make_event(control.dispatch)
    except MissionError as error:
        code = 404 if str(error).startswith("unknown") else 409
        raise HTTPException(code, str(error)) from error
    await control.runtime.bus.publish(event)
    return event.payload


@router.post("/missions/{mission_id}/acknowledge", dependencies=operator)
async def acknowledge(mission_id: str, body: Ack, request: Request):
    return await _field_update(request, mission_id, lambda d: d.acknowledge(mission_id, body.by))


@router.post("/missions/{mission_id}/progress", dependencies=operator)
async def progress(mission_id: str, body: Progress, request: Request):
    return await _field_update(
        request, mission_id, lambda d: d.report_progress(mission_id, body.percent, body.note)
    )


@router.post("/missions/{mission_id}/exception", dependencies=operator)
async def exception(mission_id: str, body: ExceptionReport, request: Request):
    return await _field_update(
        request, mission_id, lambda d: d.report_exception(mission_id, body.description, body.fatal)
    )


@router.post("/missions/{mission_id}/complete", dependencies=operator)
async def complete(mission_id: str, request: Request):
    return await _field_update(request, mission_id, lambda d: d.complete(mission_id))


# --------------------------------------------------------------------- alerts


@router.get("/alerts")
async def list_alerts(request: Request, incident_id: str | None = None):
    return [
        {k: v for k, v in a.items() if k != "cap_xml"}
        for a in control_of(request).comms.list_alerts(incident_id)
    ]


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: str, request: Request):
    alert = control_of(request).comms.get_alert(alert_id)
    if alert is None:
        raise HTTPException(404, "unknown alert")
    return alert


@router.get("/alerts/{alert_id}/cap")
async def get_alert_cap(alert_id: str, request: Request):
    alert = control_of(request).comms.get_alert(alert_id)
    if alert is None:
        raise HTTPException(404, "unknown alert")
    return Response(alert["cap_xml"], media_type="application/cap+xml")


@router.get("/sitreps")
async def list_sitreps(request: Request, incident_id: str | None = None):
    return control_of(request).comms.list_sitreps(incident_id)


# ------------------------------------------------------------------ dashboard


@router.get("/sensors")
async def sensors(request: Request):
    return sensor_panel(control_of(request).runtime)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    control = control_of(request)
    return render_dashboard(control.runtime, control)


@router.get("/dashboard/map", response_class=HTMLResponse)
async def dashboard_map(request: Request):
    control = control_of(request)
    return build_map_html(control.runtime, control)
