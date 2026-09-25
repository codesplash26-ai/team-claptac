"""The control-plane API: ``/am/v1``.  (Owner: Dinuka)

Controllers only: they validate the request, ask a service (the registry, the evaluation runner, the
observer) and map the result and its errors to HTTP. Every route names the permission it needs in its
declaration (``requires(...)``), and every mutating route is audited (``atlas/manager/audit.py``).

Resources: ``/am/v1/agents/{agent}``, ``/am/v1/traces/{trace_id}``, ``/am/v1/monitors``, ...
The committed contract is ``docs/manager_openapi.json``; a test fails if the running API drifts from it.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from atlas.dashboard.dev import playground
from atlas.manager import audit, observer, rbac
from atlas.manager.evaluation import runner
from atlas.manager.evaluation.base import Mode
from atlas.manager.experiments import run_experiment
from atlas.manager.rbac import Principal, authz, requires
from atlas.manager.registry import NotFound

router = APIRouter(prefix="/am/v1", tags=["agent-manager"])


class SuspendBody(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class EvaluateBody(BaseModel):
    evaluators: list[str] | None = None
    incident_ids: list[str] | None = None


class MonitorBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    evaluators: list[str] = Field(default_factory=lambda: ["*"])
    interval_seconds: float = Field(default=60, ge=5)
    enabled: bool = True


class MonitorPatch(BaseModel):
    enabled: bool


class ExperimentBody(BaseModel):
    scenario: str
    variant: playground.Variant
    evaluators: list[str] | None = None


def registry(request: Request):
    return request.app.state.registry


def store(request: Request):
    return request.app.state.eval_store


# ------------------------------------------------------------------------------- identity


@router.get("/me")
async def me(principal: Principal = Depends(authz(rbac.SETTINGS_READ))):
    return {
        "name": principal.name,
        "role": principal.role,
        "dev_mode": principal.dev_mode,
        "permissions": sorted(principal.permissions),
        "roles": sorted(rbac.ROLES),
    }


@router.get("/environments", dependencies=requires(rbac.AGENT_READ))
async def environments(request: Request):
    return registry(request).environments()


# --------------------------------------------------------------------------------- agents


@router.get("/agents", dependencies=requires(rbac.AGENT_READ))
async def agents(request: Request):
    reg = registry(request)
    return {"summary": reg.summary(), "agents": [a.model_dump(mode="json") for a in reg.agents()]}


@router.get("/agents/{agent}", dependencies=requires(rbac.AGENT_READ))
async def agent(agent: str, request: Request):
    try:
        return registry(request).agent(agent).model_dump(mode="json")
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/agents/{agent}/suspend", dependencies=requires(rbac.AGENT_SUSPEND))
async def suspend(agent: str, body: SuspendBody, request: Request):
    principal = request.state.principal
    try:
        return (
            registry(request)
            .suspend(agent, by=principal.name, reason=body.reason)
            .model_dump(mode="json")
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/agents/{agent}/resume", dependencies=requires(rbac.AGENT_RESUME))
async def resume(agent: str, request: Request):
    principal = request.state.principal
    try:
        record = await registry(request).resume(agent, by=principal.name)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return record.model_dump(mode="json")


@router.get("/guardrails", dependencies=requires(rbac.GUARDRAIL_READ))
async def guardrails(request: Request):
    """The rules every agent operates inside, and each agent's identity (what it may do)."""
    policy = request.app.state.system.control.policy.policy
    return {
        "proposers": policy.proposers,
        "identities": [
            {
                "agent": name,
                "allowed_actions": rule.allowed_actions,
                "high_consequence": [
                    a for a in rule.allowed_actions if policy.actions[a].consequence.value == "high"
                ],
            }
            for name, rule in policy.agents.items()
        ],
        "actions": {
            n: {"consequence": r.consequence.value, "when": r.when}
            for n, r in policy.actions.items()
        },
        "gateway": policy.gateway.model_dump(),
        "principles": [
            "Deny by default: an action not listed for an agent is refused.",
            "Only A3 may propose; only A8 and A9 may act, and only after the Gateway authorises.",
            "High-consequence actions always go to a human; a missing parameter counts as high.",
            "Every action, allowed or refused, is written to the append-only ledger.",
        ],
    }


# -------------------------------------------------------------------------------- traces


@router.get("/traces", dependencies=requires(rbac.TRACE_READ))
async def traces(request: Request):
    registry(request)
    return [t.model_dump(mode="json") for t in observer.summaries(request.app.state.runtime)]


def _find(request, trace_id):
    found = observer.find(request.app.state.runtime, trace_id)
    if found is None:
        raise HTTPException(404, "unknown trace")
    return found


@router.get("/traces/{trace_id}", dependencies=requires(rbac.TRACE_READ))
async def trace_detail(trace_id: str, request: Request):
    registry(request)
    summary, spans = _find(request, trace_id)
    return {
        "trace": summary.model_dump(mode="json"),
        "spans": [s.model_dump(mode="json") for s in spans],
    }


@router.get("/traces/{trace_id}/otlp", dependencies=requires(rbac.TRACE_EXPORT))
async def trace_otlp(trace_id: str, request: Request):
    """The trace as standard OTLP/JSON, for any OpenTelemetry backend."""
    registry(request)
    _, spans = _find(request, trace_id)
    return observer.to_otlp(spans, project=request.app.state.registry.site)


@router.post("/traces/{trace_id}/export", dependencies=requires(rbac.TRACE_EXPORT))
async def trace_export(trace_id: str, request: Request):
    """Push the trace to the configured OTLP endpoint (ATLAS_OTLP_ENDPOINT)."""
    registry(request)
    _, spans = _find(request, trace_id)
    exporter = request.app.state.exporter
    if not exporter.configured:
        raise HTTPException(409, "no OTLP endpoint configured (set ATLAS_OTLP_ENDPOINT)")
    try:
        status = await asyncio.to_thread(
            exporter.push, observer.to_otlp(spans, project=request.app.state.registry.site)
        )
    except observer.ExportError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"exported": len(spans), "endpoint_status": status}


# ------------------------------------------------------------------------------ evaluation


@router.get("/evaluators", dependencies=requires(rbac.EVAL_READ))
async def evaluators(request: Request):
    registry(request)
    return runner.catalogue()


@router.post("/evaluations", dependencies=requires(rbac.EVAL_RUN))
async def run_evaluation(body: EvaluateBody, request: Request):
    registry(request)
    try:
        result = runner.evaluate(
            request.app.state.system,
            store(request),
            evaluators=body.evaluators,
            incident_ids=body.incident_ids,
            mode=Mode.MONITOR,
            trigger="manual",
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "run_id": result["run_id"],
        "traces": result["traces"],
        "summary": result["summary"].model_dump(mode="json"),
    }


@router.get("/evaluations", dependencies=requires(rbac.EVAL_READ))
async def evaluation_runs(request: Request):
    registry(request)
    return store(request).runs()


@router.get("/evaluations/{run_id}/scores", dependencies=requires(rbac.EVAL_READ))
async def evaluation_scores(run_id: str, request: Request, failed_only: bool = False):
    registry(request)
    scores = store(request).scores(run_id, failed_only=failed_only)
    if not scores and not any(r["id"] == run_id for r in store(request).runs(500)):
        raise HTTPException(404, "unknown evaluation run")
    return scores


@router.get("/monitors", dependencies=requires(rbac.EVAL_READ))
async def monitors(request: Request):
    registry(request)
    return store(request).monitors()


@router.post("/monitors", dependencies=requires(rbac.MONITOR_MANAGE), status_code=201)
async def create_monitor(body: MonitorBody, request: Request):
    registry(request)
    try:
        return store(request).save_monitor(
            body.name, body.evaluators, body.interval_seconds, body.enabled
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/monitors/{monitor_id}", dependencies=requires(rbac.MONITOR_MANAGE))
async def patch_monitor(monitor_id: str, body: MonitorPatch, request: Request):
    registry(request)
    try:
        store(request).set_monitor(monitor_id, enabled=body.enabled)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return next(m for m in store(request).monitors() if m["id"] == monitor_id)


@router.delete("/monitors/{monitor_id}", dependencies=requires(rbac.MONITOR_MANAGE))
async def delete_monitor(monitor_id: str, request: Request):
    registry(request)
    try:
        store(request).delete_monitor(monitor_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": monitor_id}


@router.post("/experiments", dependencies=requires(rbac.EXPERIMENT_RUN))
async def create_experiment(body: ExperimentBody, request: Request):
    registry(request)
    try:
        scenario = playground.load_scenario(body.scenario)
    except KeyError as exc:
        raise HTTPException(404, f"unknown scenario {body.scenario!r}") from exc
    try:
        result = await asyncio.to_thread(run_experiment, scenario, body.variant, body.evaluators)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result["id"] = store(request).save_experiment(
        body.scenario, body.variant.model_dump(mode="json"), result
    )
    return result


@router.get("/experiments", dependencies=requires(rbac.EVAL_READ))
async def experiments(request: Request):
    registry(request)
    return [
        {
            "id": e["id"],
            "at": e["at"],
            "scenario": e["scenario"],
            "variant": e["variant"],
            "regressions": e["result"].get("regressions", []),
            "critical_regressions": e["result"].get("critical_regressions", []),
            "evaluators": e["result"].get("evaluators", []),
        }
        for e in store(request).experiments()
    ]


# ------------------------------------------------------------------------------------ audit


@router.get("/audit", dependencies=requires(rbac.AUDIT_READ))
async def audit_log(
    request: Request, limit: int = 200, actor: str | None = None
) -> list[dict[str, Any]]:
    return audit.entries(request.app.state.runtime, limit=min(limit, 1000), actor=actor)
