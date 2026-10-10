"""Payload contracts between A3 (Incident Commander) and the specialist planners (A4-A7).

Typed payloads for event types that already exist in `core/events.py` (TASK_ASSIGNED, CANDIDATE_PLAN); nothing in
the core contract changes.

    A3 --TASK_ASSIGNED{assignee: "a4_evacuation", ...EvacuationTask}--> A4
    A4 --CANDIDATE_PLAN{CandidatePlanPayload(plan=RoutePlan)}---------> A3

TASK_ASSIGNED is delivered to every subscriber, so each planner acts only on tasks whose `assignee`
is its own name. Only A3 delegates; specialists never message each other.

A3 can also call `Evacuation.plan_evacuation(...)` directly (it is synchronous and deterministic),
and `RoutePlan.to_commander_state()` reshapes a plan into the `routes` state that
`atlas.agents.planning.commander.CommanderAdapter` searches over.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EVACUATION_ASSIGNEE = "a4_evacuation"
# ok            at least one route avoids every road believed likely closed
# no_safe_route routes exist but each crosses a road believed likely closed: escalate to a human
# no_route      nothing is reachable at all
PlanStatus = Literal["ok", "no_safe_route", "no_route", "invalid_task", "error"]


class LatLon(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class EvacuationTask(BaseModel):
    """A3 -> A4. Plan routes from `origin` to a set of destinations."""

    task: Literal["evacuation"] = "evacuation"
    assignee: Literal["a4_evacuation"] = EVACUATION_ASSIGNEE
    task_id: str
    origin: int | LatLon  # OSM node id, or a coordinate snapped to the nearest node
    # Node ids or shelter ids. Omitted/None means every shelter at the agent's site. Shelter
    # capacity and assignment are A7's concern; A4 only reports what it can reach and how risky it is.
    destinations: list[int | str] | None = None
    k: int = Field(default=3, ge=1, le=10)  # how many alternative routes to return


class RouteOption(BaseModel):
    route_id: str  # unique within a plan, e.g. "ratnapura-shelter-02#1"
    rank: int  # 1 = recommended
    nodes: list[int]
    destination: int
    shelter_id: str | None = None
    capacity: int | None = None  # the shelter's *static* capacity; live occupancy is A7's job
    hops: int
    safe: bool  # crosses no road believed likely closed (belief below the close threshold)
    # Roads on the route with evidence in the COP, and roads still at the prior. Long routes look
    # risky mostly because of the unobserved ones (see success_probability).
    observed_edges: int
    unobserved_edges: int
    minutes: float  # expected travel time at free flow, incl. the uphill penalty
    cost: float  # belief-weighted routing cost (lower is better)
    # Product of edge beliefs, edges assumed independent. Unobserved roads sit at the 0.9 prior, so
    # for long routes this is dominated by the prior: compare routes with it, and prefer `safe` and
    # `weakest_belief` for go/no-go decisions.
    success_probability: float
    failure_probability: float
    weakest_edge: str | None = None  # "<u>-<v>" of the least trustworthy road on the route
    weakest_belief: float | None = None
    mean_uncertainty: float


class RoutePlan(BaseModel):
    status: PlanStatus
    reason: str = ""
    site: str
    origin: int | None = None
    destinations: list[int] = Field(default_factory=list)
    # Pass these to `atlas.reasoning.PlanningRequest` so a stale or expired plan is rejected.
    snapshot_version: int
    planned_at: datetime
    valid_until: datetime | None = None
    options: list[RouteOption] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    @property
    def recommended(self) -> RouteOption | None:
        return self.options[0] if self.options else None

    def to_commander_state(self, people: int, *, include_unsafe: bool = False) -> dict[str, Any]:
        """State for `CommanderAdapter`: {"people": n, "routes": {route_id: {...}}}.

        Routes that cross a road believed likely closed are left out unless `include_unsafe`, so
        with status "no_safe_route" the state has no routes and A3's planner falls back to
        "no action", which is the cue to escalate to the Human Incident Commander.
        `failure_probability` is 1 - success_probability. A route to a shelter of unknown capacity
        gets capacity == people (unconstrained), so unknown never silently excludes a route.
        """
        return {
            "people": people,
            "routes": {
                o.route_id: {
                    "minutes": o.minutes,
                    "failure_probability": o.failure_probability,
                    "capacity": o.capacity if o.capacity is not None else people,
                }
                for o in self.options
                if o.safe or include_unsafe
            },
        }


class CandidatePlanPayload(BaseModel):
    """A4..A7 -> A3. `plan` is the task-specific plan, e.g. a `RoutePlan` dump for A4."""

    task_id: str
    task: str
    agent: str
    status: str
    plan: dict[str, Any]


# --------------------------------------------------------------------------- A7 Shelter & Relief

SHELTER_ASSIGNEE = "a7_shelter"
ShelterPlanStatus = Literal["ok", "partial", "no_shelter", "invalid_task", "error"]


class EvacueeGroup(BaseModel):
    group_id: str
    people: int = Field(gt=0)
    origin: int | LatLon


class ShelterTask(BaseModel):
    """A3 -> A7. Decide which shelters take which groups, given current road access and capacity."""

    task: Literal["shelter"] = "shelter"
    assignee: Literal["a7_shelter"] = SHELTER_ASSIGNEE
    task_id: str
    groups: list[EvacueeGroup] = Field(min_length=1)
    shelter_ids: list[str] | None = None  # restrict to these shelters; None = all at the site
    allow_split: bool = True  # may a group be spread over several shelters?


class Placement(BaseModel):
    group_id: str
    shelter_id: str
    people: int
    route_nodes: list[int]  # the safe route, so A3 can hand it to A8 (create_mission `route`)
    minutes: float
    weakest_belief: float


class Unplaced(BaseModel):
    group_id: str
    people: int
    reason: Literal["no_safe_route", "no_capacity"]


class ShelterState(BaseModel):
    shelter_id: str
    capacity: int
    occupancy_before: int
    occupancy_after: int  # if this plan is carried out in full
    utilisation_after: float


class ResupplyRequest(BaseModel):
    """A7 -> A3, who may delegate it to A6. Only A3 delegates; A7 never messages A6 directly."""

    shelter_id: str
    reason: str
    urgency: Literal["routine", "high"]


class ShelterPlan(BaseModel):
    status: ShelterPlanStatus
    reason: str = ""
    site: str
    snapshot_version: int
    planned_at: datetime
    valid_until: datetime | None = None
    placements: list[Placement] = Field(default_factory=list)
    unplaced: list[Unplaced] = Field(default_factory=list)
    shelters: list[ShelterState] = Field(default_factory=list)
    resupply_requests: list[ResupplyRequest] = Field(default_factory=list)
    solver_status: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ A6 Resource & Logistics

LOGISTICS_ASSIGNEE = "a6_logistics"
ResourceKind = Literal["rescue_team", "vehicle"]
LogisticsPlanStatus = Literal["ok", "partial", "none_served", "invalid_task", "error"]


class RescueJob(BaseModel):
    """One piece of work that needs units on site: a rescue, or a resupply run to a shelter."""

    job_id: str
    priority: float = Field(gt=0)  # relative value, e.g. from A5's triage ranking; higher = sooner
    location: int | LatLon
    needs: dict[ResourceKind, int] = Field(min_length=1)  # ALL of these, or the job is not served


class LogisticsTask(BaseModel):
    """A3 -> A6. Which units go to which jobs? A3 turns A7's resupply requests into vehicle jobs."""

    task: Literal["logistics"] = "logistics"
    assignee: Literal["a6_logistics"] = LOGISTICS_ASSIGNEE
    task_id: str
    jobs: list[RescueJob] = Field(min_length=1)


class UnitAssignment(BaseModel):
    job_id: str
    unit_id: str
    kind: ResourceKind
    depot_node: int
    route_nodes: list[int]  # safe route depot -> job, for A8's mission `route`
    minutes: float
    weakest_belief: float


class UnservedJob(BaseModel):
    job_id: str
    priority: float
    reason: Literal["no_safe_route", "not_enough_units"]
    missing: dict[str, int]  # units short, by kind, as far as A6 can tell


class ShortageAlert(BaseModel):
    """A6 -> A3: demand for a kind of unit exceeds what can be used."""

    kind: str
    demanded: int  # units wanted across all jobs
    available: int  # units not already committed
    unmet_jobs: list[str]


class LogisticsPlan(BaseModel):
    status: LogisticsPlanStatus
    reason: str = ""
    site: str
    snapshot_version: int
    planned_at: datetime
    valid_until: datetime | None = None
    assignments: list[UnitAssignment] = Field(default_factory=list)
    unserved: list[UnservedJob] = Field(default_factory=list)
    shortages: list[ShortageAlert] = Field(default_factory=list)
    fleet: dict[str, dict[str, int]] = Field(default_factory=dict)  # kind -> {total, available}
    solver_status: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- A5 Rescue & Needs Triage

TRIAGE_ASSIGNEE = "a5_triage"
TriagePlanStatus = Literal["ok", "empty", "invalid_task", "error"]


class TriageTask(BaseModel):
    """A3 -> A5. Rank who needs help first, optionally only among these areas."""

    task: Literal["triage"] = "triage"
    assignee: Literal["a5_triage"] = TRIAGE_ASSIGNEE
    task_id: str
    area_ids: list[str] | None = None  # None = every area A5 knows about


class RankedNeed(BaseModel):
    area_id: str
    name: str | None = None
    rank: int  # 1 = help first
    hazard: str | None = None
    level: str
    urgency: float = Field(ge=0, le=1)
    vulnerability: float = Field(ge=0, le=1)
    estimated_vulnerable: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=1)  # transparent: urgency x vulnerability with mild boosts
    explanation: str
    information_gaps: list[str] = Field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class TriagePlan(BaseModel):
    status: TriagePlanStatus
    reason: str = ""
    planned_at: datetime
    snapshot_version: int
    ranked_needs: list[RankedNeed] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    @property
    def top_priority(self) -> str | None:
        return self.ranked_needs[0].area_id if self.ranked_needs else None
