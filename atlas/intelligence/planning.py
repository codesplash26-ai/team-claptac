"""Optional shadow comparison of A4 routes with the existing PlanU-inspired engine.

This never edits A3's selected actions. Outcomes use the existing illustrative flood model,
not a calibrated disaster forecast; route priors and static shelter capacity remain assumptions.
"""

from atlas.agents.contracts import RoutePlan
from atlas.agents.planning.commander import CommanderAdapter
from atlas.core.config import planner_config_for
from atlas.reasoning import Planner, PlanningRequest


async def compare_routes(incident, runtime, *, group_size):
    raw = incident.get("plans", {}).get("a4_evacuation")
    if not raw:
        return {"status": "unavailable", "reason": "No A4 route plan"}
    plan = RoutePlan.model_validate(raw)
    needs = incident.get("plans", {}).get("a5_triage", {}).get("ranked_needs", [])
    need = next((n for n in needs if n["area_id"] == incident.get("top_priority")), None)
    if not need or not need.get("estimated_vulnerable"):
        return {"status": "unavailable", "reason": "No triage headcount for the priority area"}
    people = min(group_size, int(need["estimated_vulnerable"]))
    state = plan.to_commander_state(people)
    request = PlanningRequest(
        agent="intelligence_advisor",
        state=state,
        snapshot_version=plan.snapshot_version,
        valid_until=plan.valid_until,
        evidence_ids=plan.evidence_ids,
        assumptions=[
            *plan.assumptions,
            "Shadow simulation only: independent edge priors, synthetic failure penalty, static capacity.",
            f"Compare one configured evacuation group of {people} people, not an allocation plan.",
        ],
    )
    result = await Planner(planner_config_for("a3_incident_commander")).plan(
        request, CommanderAdapter()
    )
    try:
        result.require_current(runtime.cop.version, now=runtime.clock())
    except ValueError as exc:
        return {"status": "stale", "reason": str(exc)}
    feasible = [o for o in plan.options if o.safe and (o.capacity is None or o.capacity >= people)]
    baseline = min(feasible, key=lambda o: o.minutes).route_id if feasible else None
    return {
        "status": "shadow",
        "baseline_route": baseline,
        "planner_route": result.action.params["route"] if result.action else None,
        "result": result.model_dump(mode="json"),
    }
