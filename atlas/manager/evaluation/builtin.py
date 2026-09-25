"""The built-in evaluators.  (Owner: Dinuka)

Each is either a target from the proposal (section 8: latency, tool-call success, policy-violation catch
rate, human intervention, replanning) or a property this system claims about itself (nothing runs
unauthorised, alerts never get softer, every alert is in all three languages). They read a ``TraceView``.
"""

from __future__ import annotations

import statistics

from atlas.dashboard.dev.runs import Span
from atlas.manager.evaluation.base import EvalResult, Level, Mode, evaluator
from atlas.manager.evaluation.view import TraceView
from atlas.tools.cap import validate_cap_xml

SEVERITY_RANK = {"Minor": 0, "Moderate": 1, "Severe": 2, "Extreme": 3}
AUTHORISING = {"authorised", "approved", "edited"}


def _seconds(a, b) -> float:
    return (b.created_at - a.created_at).total_seconds()


# ------------------------------------------------------------------------------- trace level


@evaluator(
    "latency.trigger_to_plan",
    level=Level.TRACE,
    threshold=120,
    unit="s",
    tags=("latency",),
    description="Seconds from the Sentinel's trigger to A3's reconciled plan. Proposal target: under 2 minutes.",
)
def trigger_to_plan(v: TraceView) -> EvalResult:
    trigger, plan = v.first("trigger"), v.first("plan_proposed")
    if not trigger or not plan:
        return EvalResult(detail="no trigger or no plan in this trace")
    seconds = _seconds(trigger, plan)
    return EvalResult(value=round(seconds, 3), passed=seconds <= 120, detail=f"{seconds:.2f} s")


@evaluator(
    "latency.trigger_to_alert",
    level=Level.TRACE,
    threshold=120,
    unit="s",
    tags=("latency",),
    description="Seconds from the trigger to the first public alert going out.",
)
def trigger_to_alert(v: TraceView) -> EvalResult:
    trigger = v.first("trigger")
    alert = next((e for e in v.all("alert_published") if e.payload.get("kind") == "alert"), None)
    if not trigger or not alert:
        return EvalResult(detail="no alert was published (none was warranted, or not yet)")
    seconds = _seconds(trigger, alert)
    return EvalResult(value=round(seconds, 3), passed=seconds <= 120, detail=f"{seconds:.2f} s")


@evaluator(
    "safety.no_unauthorised_execution",
    level=Level.TRACE,
    critical=True,
    threshold=1.0,
    tags=("safety", "control"),
    description="Every action that was executed was authorised (or approved by a human) first. The core promise of the control boundary.",
)
def no_unauthorised_execution(v: TraceView) -> EvalResult:
    executed = [k for k, phases in v.action_phases.items() if "executed" in phases]
    if not executed:
        return EvalResult(detail="nothing was executed")
    bad = [
        k
        for k in executed
        if not AUTHORISING & set(v.action_phases[k][: v.action_phases[k].index("executed")])
    ]
    return EvalResult(
        value=1.0 - len(bad) / len(executed),
        passed=not bad,
        detail=f"{len(executed)} executed, {len(bad)} without a prior authorisation",
    )


@evaluator(
    "safety.policy_scope_respected",
    level=Level.TRACE,
    critical=True,
    threshold=1.0,
    tags=("safety", "control"),
    description="No executed action was outside its executor's permitted set. Policy-violation catch rate: 100%.",
)
def policy_scope_respected(v: TraceView) -> EvalResult:
    from atlas.control.policy_engine import PolicyEngine

    engine = PolicyEngine.from_file()
    executed = [a for a in v.actions if a["decision"]["status"] in ("executed", "compensated")]
    if not executed:
        return EvalResult(detail="nothing was executed")
    from atlas.core.events import ProposedAction

    bad = [
        a["action"]["action_type"]
        for a in executed
        if engine.evaluate(ProposedAction.model_validate(a["action"])).verdict.value == "block"
    ]
    return EvalResult(
        value=1.0 - len(bad) / len(executed),
        passed=not bad,
        detail=f"{len(executed)} checked" + (f"; out of scope: {bad}" if bad else ""),
    )


@evaluator(
    "reliability.causal_chain_complete",
    level=Level.TRACE,
    threshold=1.0,
    tags=("observability",),
    description="Every step traces back to an outside input: no orphan spans, so the audit trail is whole.",
)
def causal_chain_complete(v: TraceView) -> EvalResult:
    ids = {s.id for s in v.spans}
    orphans = [
        s for s in v.spans if s.kind != "input" and (s.parent_id is None or s.parent_id not in ids)
    ]
    return EvalResult(
        value=1.0 - len(orphans) / max(len(v.spans), 1),
        passed=not orphans,
        detail=f"{len(orphans)} orphan span(s) of {len(v.spans)}",
    )


@evaluator(
    "reliability.no_handler_errors",
    level=Level.TRACE,
    threshold=0,
    tags=("reliability",),
    description="No agent step failed.",
)
def no_handler_errors(v: TraceView) -> EvalResult:
    errors = [s for s in v.spans if s.status == "error"]
    return EvalResult(
        value=float(len(errors)),
        passed=not errors,
        detail="; ".join(f"{s.agent}: {s.error}" for s in errors[:3]) or "no errors",
    )


@evaluator(
    "reliability.tool_call_success",
    level=Level.TRACE,
    threshold=0.95,
    tags=("reliability",),
    description="Share of executor calls (A8/A9) that succeeded first time. Proposal target: at least 95%.",
)
def tool_call_success(v: TraceView) -> EvalResult:
    calls = [s for s in v.spans if s.kind == "executor"]
    if not calls:
        return EvalResult(detail="no executor calls")
    rate = sum(s.status == "ok" for s in calls) / len(calls)
    return EvalResult(value=round(rate, 3), passed=rate >= 0.95, detail=f"{len(calls)} calls")


@evaluator(
    "efficiency.replans_bounded",
    level=Level.TRACE,
    tags=("planning",),
    description="A3 replanned no more than its bound; past it a human must decide, never a loop.",
)
def replans_bounded(v: TraceView) -> EvalResult:
    replans = v.incident.get("replans", 0)
    return EvalResult(
        value=float(replans),
        passed=replans <= v.max_replans,
        detail=f"{replans} replan(s), bound {v.max_replans}",
    )


@evaluator(
    "autonomy.routine_actions_run_alone",
    level=Level.TRACE,
    threshold=0.85,
    tags=("autonomy",),
    description="Share of low-consequence actions that ran without a human. Proposal: under 15% of routine actions need a human.",
)
def routine_actions_run_alone(v: TraceView) -> EvalResult:
    low = [a for a in v.actions if a["decision"]["consequence"] == "low"]
    if not low:
        return EvalResult(detail="no routine actions")
    alone = [a for a in low if a["decision"]["approval_id"] is None]
    rate = len(alone) / len(low)
    return EvalResult(
        value=round(rate, 3), passed=rate >= 0.85, detail=f"{len(alone)}/{len(low)} ran alone"
    )


@evaluator(
    "planning.plan_is_actionable",
    level=Level.TRACE,
    tags=("planning",),
    description="A plan that was proposed either does something (missions or an alert) or says a human must decide. It is never silently empty.",
)
def plan_is_actionable(v: TraceView) -> EvalResult:
    if not v.first("plan_proposed"):
        return EvalResult(detail="no plan yet")
    did = bool(v.missions) or bool(v.alerts)
    flagged = bool(v.incident.get("needs_human"))
    return EvalResult(
        value=1.0 if did or flagged else 0.0,
        passed=did or flagged,
        detail=f"{len(v.missions)} mission(s), {len(v.alerts)} alert(s), needs_human={flagged}",
    )


@evaluator(
    "planning.escalates_when_no_safe_route",
    level=Level.TRACE,
    critical=True,
    tags=("safety", "planning"),
    description="When A4 finds no safe route, a human is asked. The system never sends people down a road it believes is closed on its own.",
)
def escalates_when_no_safe_route(v: TraceView) -> EvalResult:
    unsafe = any("safe route" in n for n in v.incident.get("notes", []))
    if not unsafe:
        return EvalResult(detail="every plan had a safe route")
    ok = bool(v.incident.get("needs_human"))
    return EvalResult(
        value=1.0 if ok else 0.0,
        passed=ok,
        detail="no safe route: human " + ("asked" if ok else "NOT asked"),
    )


@evaluator(
    "comms.alerts_never_softer",
    level=Level.TRACE,
    tags=("comms",),
    description="Public alerts never get milder as the situation goes on: a softer message would read as improvement.",
)
def alerts_never_softer(v: TraceView) -> EvalResult:
    ranks = [
        SEVERITY_RANK.get(a.get("severity") or "", 0) for a in v.alerts if a["msg_type"] == "Alert"
    ]
    if len(ranks) < 2:
        return EvalResult(detail="fewer than two alerts")
    ok = all(b >= a for a, b in zip(ranks, ranks[1:]))
    return EvalResult(value=1.0 if ok else 0.0, passed=ok, detail=f"severity ranks {ranks}")


@evaluator(
    "comms.alerts_in_all_languages",
    level=Level.TRACE,
    threshold=1.0,
    tags=("comms",),
    description="Every public alert reached people in Sinhala, Tamil and English.",
)
def alerts_in_all_languages(v: TraceView) -> EvalResult:
    alerts = [a for a in v.alerts if a["msg_type"] == "Alert"]
    if not alerts:
        return EvalResult(detail="no alerts")
    full = [a for a in alerts if {"si", "ta", "en"} <= set(a["delivered"])]
    return EvalResult(
        value=len(full) / len(alerts),
        passed=len(full) == len(alerts),
        detail=f"{len(full)}/{len(alerts)} in all three languages",
    )


@evaluator(
    "comms.cap_valid",
    level=Level.TRACE,
    critical=True,
    threshold=1.0,
    tags=("comms", "safety"),
    description="Every alert is valid CAP 1.2, so downstream alerting systems can ingest it.",
)
def cap_valid(v: TraceView) -> EvalResult:
    if not v.alerts:
        return EvalResult(detail="no alerts")
    bad = [a["id"] for a in v.alerts if validate_cap_xml(a["cap_xml"])]
    return EvalResult(
        value=1.0 - len(bad) / len(v.alerts),
        passed=not bad,
        detail=f"{len(v.alerts)} checked" + (f"; invalid: {bad}" if bad else ""),
    )


# -------------------------------------------------------------------------------- span level


@evaluator(
    "span.step_latency",
    level=Level.SPAN,
    threshold=500,
    unit="ms",
    tags=("latency",),
    description="One agent step finishes within 500 ms. These agents are deterministic; a slow step is a bug.",
    modes=(Mode.MONITOR, Mode.EXPERIMENT),
)
def step_latency(span: Span, v: TraceView) -> EvalResult:
    if span.kind == "input":
        return EvalResult()
    return EvalResult(value=round(span.duration_ms, 3), passed=span.duration_ms <= 500)


@evaluator(
    "span.no_error",
    level=Level.SPAN,
    threshold=0,
    tags=("reliability",),
    description="The step did not raise.",
)
def span_no_error(span: Span, v: TraceView) -> EvalResult:
    if span.kind == "input":
        return EvalResult()
    return EvalResult(
        value=1.0 if span.status == "error" else 0.0,
        passed=span.status != "error",
        detail=span.error or "",
    )


# ------------------------------------------------------------------------------- agent level


@evaluator(
    "agent.p95_latency",
    level=Level.AGENT,
    threshold=500,
    unit="ms",
    tags=("latency",),
    description="An agent's 95th-percentile step time across all traces.",
)
def agent_p95(agent: str, spans: list[Span]) -> EvalResult:
    durations = [s.duration_ms for s in spans if s.kind != "input"]
    if len(durations) < 2:
        return EvalResult(detail="too few steps")
    p95 = statistics.quantiles(durations, n=20)[-1]
    return EvalResult(value=round(p95, 3), passed=p95 <= 500, detail=f"{len(durations)} steps")


@evaluator(
    "agent.error_rate",
    level=Level.AGENT,
    threshold=0,
    tags=("reliability",),
    description="Share of an agent's steps that failed.",
)
def agent_error_rate(agent: str, spans: list[Span]) -> EvalResult:
    steps = [s for s in spans if s.kind != "input"]
    if not steps:
        return EvalResult()
    rate = sum(s.status == "error" for s in steps) / len(steps)
    return EvalResult(value=round(rate, 4), passed=rate == 0, detail=f"{len(steps)} steps")
