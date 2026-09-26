"""Reconcile the specialists' candidate plans into one coordinated response.

Pure and deterministic: plans in, proposed actions out. No I/O, no LLM, no clock. This is where
"a route that leads to a full shelter" is caught, because A3 sees A4's routes, A7's placements and
A6's assignments together, and no specialist sees the others.

Every action carries the facts it rests on (``grounds``): plan ids and keys that A3's grounding check
and the dashboard can follow back to the specialist that produced them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

SEVERITY_OF_LEVEL = {"warning": "Moderate", "severe": "Severe"}
SEVERITY_RANK = {"Moderate": 1, "Severe": 2}


class ActionSpec(BaseModel):
    """What A3 wants done, before it is turned into a keyed ProposedAction."""

    slot: str  # stable identity within an incident, e.g. "mission:g1"; drives the idempotency key
    executor: str
    action_type: str
    params: dict[str, Any]
    rationale: str
    grounds: list[str] = Field(default_factory=list)


class Reconciled(BaseModel):
    actions: list[ActionSpec] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    escalate: bool = False  # a human must look at this incident
    reasons: list[str] = Field(default_factory=list)
    summary: str = ""
    missions: dict[str, dict[str, Any]] = Field(default_factory=dict)  # group_id -> mission facts


def _status(plan: dict | None) -> str:
    return (plan or {}).get("status", "missing")


def reconcile(
    *,
    incident_id: str,
    area_id: str,
    area_name: str,
    hazard: str,
    level: str,
    plans: dict[str, dict],
    previous_missions: dict[str, dict],
    cycle: int,
    alerted: list[str] | None = None,
) -> Reconciled:
    triage = plans.get("a5_triage") or {}
    route = plans.get("a4_evacuation") or {}
    shelter = plans.get("a7_shelter") or {}
    logistics = plans.get("a6_logistics") or {}
    out = Reconciled()

    def conflict(code: str, reason: str, *, escalate: bool = True) -> None:
        out.conflicts.append(code)
        out.reasons.append(reason)
        out.escalate = out.escalate or escalate

    # --- routes: A4 is the authority on access. No safe road means a human decides.
    if _status(route) == "no_safe_route":
        conflict("no_safe_route", "A4: every route crosses a road believed likely closed")
    elif _status(route) not in ("ok",):
        conflict(f"routes_{_status(route)}", f"A4 produced no usable route ({_status(route)})")
    recommended = (route.get("options") or [None])[0]

    # --- shelter placement: A7 knows capacity, A4 does not.
    placements = list(shelter.get("placements") or [])
    # A3 skips A7 and A6 when A4 finds nowhere safe to go, so "missing" is expected then.
    if _status(shelter) in ("no_shelter", "invalid_task", "error") or (
        _status(shelter) == "missing" and _status(route) == "ok"
    ):
        conflict(
            "no_shelter", f"A7 placed nobody ({_status(shelter)}): {shelter.get('reason', '')}"
        )
    for u in shelter.get("unplaced") or []:
        conflict(
            f"unplaced:{u['group_id']}",
            f"A7 could not place {u['people']} people in {u['group_id']} ({u['reason']})",
        )
    if recommended and placements:
        chosen = {p["shelter_id"] for p in placements}
        if recommended.get("shelter_id") not in chosen:
            # A4's favourite was skipped by A7, typically because it is full: A7 wins.
            out.reasons.append(
                f"A4 preferred {recommended.get('shelter_id')} but A7 placed groups at "
                f"{sorted(chosen)} (capacity); following A7"
            )
            out.conflicts.append("route_preference_overridden_by_capacity")
    for req in shelter.get("resupply_requests") or []:
        out.reasons.append(f"A7 asks for resupply at {req['shelter_id']} ({req['urgency']})")

    # --- units: one vehicle job per group.
    units: dict[str, list[dict]] = {}
    for a in logistics.get("assignments") or []:
        units.setdefault(a["job_id"], []).append(a)
    for u in logistics.get("unserved") or []:
        conflict(f"unserved:{u['job_id']}", f"A6 could not serve {u['job_id']} ({u['reason']})")
    for s in logistics.get("shortages") or []:
        out.reasons.append(
            f"A6 shortage of {s['kind']}: wanted {s['demanded']}, {s['available']} available"
        )

    # --- missions: one per placed group that also has units, plus standby and resupply runs.
    # Replans update existing missions, never duplicate them.
    grounds_base = [
        f"a5_triage:{triage.get('planned_at')}",
        f"a4_evacuation:{route.get('planned_at')}",
    ]
    intents: list[dict[str, Any]] = []
    for p in placements:
        gid = p["group_id"]
        assigned = units.get(gid, [])
        if not assigned:
            conflict(f"no_units:{gid}", f"no vehicle or team available for group {gid}")
            continue
        intents.append(
            {
                "gid": gid,
                "shelter_id": p["shelter_id"],
                "route": p["route_nodes"],
                "people": p["people"],
                "assigned": assigned,
                "title": f"Evacuate {p['people']} people from {area_name} to {p['shelter_id']}",
                "why": f"A7 placed {p['people']} people at {p['shelter_id']} ({p['minutes']:.0f} min, "
                f"weakest road belief {p['weakest_belief']:.2f}); A6 assigned {len(assigned)} unit(s).",
                "grounds": [
                    *grounds_base,
                    f"a7_shelter:{shelter.get('planned_at')}:{gid}",
                    f"a6_logistics:{logistics.get('planned_at')}:{gid}",
                ],
            }
        )
    for job_id, assigned in units.items():
        if job_id.startswith("rescue:"):
            intents.append(
                {
                    "gid": job_id,
                    "shelter_id": None,
                    "route": assigned[0]["route_nodes"],
                    "people": 0,
                    "assigned": assigned,
                    "title": f"Rescue team on standby at {area_name}",
                    "why": "A6 held a rescue team and vehicle at the incident.",
                    "grounds": [
                        *grounds_base,
                        f"a6_logistics:{logistics.get('planned_at')}:{job_id}",
                    ],
                }
            )
        elif job_id.startswith("resupply:"):
            shelter_id = job_id.split(":", 1)[1]
            intents.append(
                {
                    "gid": job_id,
                    "shelter_id": shelter_id,
                    "route": assigned[0]["route_nodes"],
                    "people": 0,
                    "assigned": assigned,
                    "title": f"Resupply {shelter_id}",
                    "why": f"A7 requested resupply at {shelter_id}; A6 assigned a vehicle.",
                    "grounds": [
                        f"a7_shelter:{shelter.get('planned_at')}:{shelter_id}",
                        f"a6_logistics:{logistics.get('planned_at')}:{job_id}",
                    ],
                }
            )
    seen = set()
    for m in intents:
        gid = m["gid"]
        seen.add(gid)
        prior = previous_missions.get(gid)
        out.missions[gid] = {
            "shelter_id": m["shelter_id"],
            "route": m["route"],
            "people": m["people"],
            "units": [u["unit_id"] for u in m["assigned"]],
            "mission_id": (prior or {}).get("mission_id"),
        }
        if prior is None:
            out.actions.append(
                ActionSpec(
                    slot=f"mission:{gid}",
                    executor="a8_dispatch",
                    action_type="create_mission",
                    params={
                        "title": m["title"],
                        "team": ",".join(out.missions[gid]["units"]),
                        "resources_committed": len(m["assigned"]),
                        "people": m["people"],
                        "destination": m["shelter_id"],
                        "route": m["route"],
                        "group_id": gid,
                        "priority": "high" if level == "severe" else "normal",
                    },
                    rationale=m["why"],
                    grounds=m["grounds"],
                )
            )
        elif prior["shelter_id"] != m["shelter_id"]:
            out.actions.append(
                ActionSpec(
                    slot=f"cancel:{gid}:{prior['mission_id']}",
                    executor="a8_dispatch",
                    action_type="cancel_mission",
                    params={
                        "mission_id": prior["mission_id"],
                        "reason": f"destination changed to {m['shelter_id']}",
                    },
                    rationale=f"Replan moved {gid} from {prior['shelter_id']} to {m['shelter_id']}; "
                    "the old mission must stop.",
                    grounds=m["grounds"],
                )
            )
            conflict(
                f"destination_changed:{gid}",
                f"{gid} changed shelter; the old mission is "
                "cancelled by a human before a new one starts",
                escalate=False,
            )
        elif prior["route"] != m["route"]:
            out.actions.append(
                ActionSpec(
                    slot=f"reroute:{gid}",
                    executor="a8_dispatch",
                    action_type="reroute_group",
                    params={"mission_id": prior["mission_id"], "route": m["route"]},
                    rationale=f"Roads changed; {gid} keeps its destination but takes a new route.",
                    grounds=m["grounds"],
                )
            )
    dropped = {g: m for g, m in previous_missions.items() if g not in seen}
    if dropped and _status(route) != "ok":
        # Evacuees are already moving. Do not cancel anything on our own: a human decides.
        conflict(
            "missions_without_safe_route",
            f"{len(dropped)} mission(s) in flight have no "
            "safe route any more; leaving them running for a human to decide",
        )
    else:
        for gid, prior in dropped.items():
            conflict(
                f"group_dropped:{gid}",
                f"{gid} is no longer placed; mission {prior['mission_id']} needs a human decision",
                escalate=True,
            )

    # --- public alert: only when the risk is real, and only as loud as the level.
    severity = SEVERITY_OF_LEVEL.get(level)
    # `alerted` holds "hazard:severity" (older records: just the severity, which counts for every
    # hazard, the safe reading). A landslide warning does not stop a later flood warning.
    already = max(
        (
            SEVERITY_RANK.get(a.rsplit(":", 1)[-1], 0)
            for a in alerted or []
            if ":" not in a or a.rsplit(":", 1)[0] == hazard
        ),
        default=0,
    )
    # Never repeat an alert, and never send a milder one after a stronger one: the public would
    # read a softer instruction as the situation improving.
    if severity and SEVERITY_RANK[severity] > already:
        out.actions.append(
            ActionSpec(
                slot=f"alert:{hazard}:{severity}:{'evacuate' if placements else 'avoid_area'}",
                executor="a9_comms",
                action_type="publish_alert",
                params={
                    "area": area_name,
                    "event": hazard,
                    "severity": severity,
                    "scope": "local",
                    "instruction": "evacuate" if placements else "avoid_area",
                },
                rationale=f"A2 rates {area_id} {level} for {hazard}.",
                grounds=[f"a2_hazard_intel:{area_id}:{level}"],
            )
        )

    # --- situation report, deterministic so it works without any LLM.
    lines = [f"{incident_id} (cycle {cycle}): {hazard} at {area_name}, level {level}."]
    if placements:
        lines.append(
            "Placed: " + "; ".join(f"{p['people']} to {p['shelter_id']}" for p in placements)
        )
    if out.reasons:
        lines.append("Notes: " + " | ".join(out.reasons))
    if out.escalate:
        lines.append("HUMAN DECISION NEEDED.")
    out.summary = " ".join(lines)
    out.actions.append(
        ActionSpec(
            slot="sitrep",
            executor="a9_comms",
            action_type="publish_sitrep",
            params={"summary": out.summary},
            rationale="Periodic situation report for the EOC.",
            grounds=grounds_base,
        )
    )
    return out
