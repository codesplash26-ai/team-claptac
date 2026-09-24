"""A6 Resource & Logistics: matching teams and vehicles to jobs over safe roads."""

from datetime import timedelta
from hashlib import sha256

import pytest

from atlas.agents.a6_logistics import Logistics
from atlas.agents.contracts import (
    CandidatePlanPayload,
    EvacueeGroup,
    LatLon,
    LogisticsPlan,
    RescueJob,
)
from atlas.agents.wiring import attach_routing
from atlas.core.events import Event, EventType
from atlas.tools import geo
from atlas.tools.reference import load_resources, load_shelters
from atlas.tools.sensor_map import SensorMap
from tests.conftest import events
from tests.routing_helpers import AREA, DEPOT, crossed, observation, wet

SITES = {s.id: s.node for s in load_shelters("ratnapura")}  # high-ground job locations
SITE_1, SITE_2, SITE_3, SITE_4 = (SITES[f"ratnapura-shelter-0{i}"] for i in (1, 2, 3, 4))
TEAMS = sorted(r.id for r in load_resources("ratnapura") if r.kind == "rescue_team")
VANS = sorted(r.id for r in load_resources("ratnapura") if r.kind == "vehicle")


@pytest.fixture(autouse=True)
def routing(runtime):
    """Every test here runs with the routing agents registered on the runtime's bus."""
    return attach_routing(runtime)


@pytest.fixture
def a6(routing):
    return routing.logistics


@pytest.fixture(scope="module")
def mapping():
    return SensorMap.from_file().get(AREA)


@pytest.fixture(scope="module")
def rg():
    return geo.load_site("ratnapura")


def job(job_id, priority, location, **needs):
    return RescueJob(job_id=job_id, priority=priority, location=location, needs=needs)


def crew(plan, job_id):
    return sorted(a.unit_id for a in plan.assignments if a.job_id == job_id)


def task(**overrides):
    payload = {
        "task": "logistics",
        "assignee": "a6_logistics",
        "task_id": "t-6",
        "jobs": [{"job_id": "j1", "priority": 5, "location": SITE_1, "needs": {"rescue_team": 1}}],
        **overrides,
    }
    return Event(
        type=EventType.TASK_ASSIGNED,
        source="a3_incident_commander",
        incident_id="incident:x",
        payload=payload,
    )


# --------------------------------------------------------------- assignment


def test_a_job_gets_exactly_the_units_it_needs_over_a_safe_route(runtime, a6):
    plan = a6.assign([job("j1", 5, SITE_1, rescue_team=1, vehicle=1)], now=runtime.clock())
    assert plan.status == "ok" and plan.reason == "" and plan.unserved == []
    assert sorted(a.kind for a in plan.assignments) == ["rescue_team", "vehicle"]
    for a in plan.assignments:
        assert a.depot_node == DEPOT and a.route_nodes[0] == DEPOT and a.route_nodes[-1] == SITE_1
        assert a.weakest_belief >= a6.config.routing.close_threshold
    assert plan.fleet == {
        "rescue_team": {"total": 3, "available": 3},
        "vehicle": {"total": 4, "available": 4},
    }
    assert plan.snapshot_version == runtime.cop.version
    assert plan.solver_status in {"OPTIMAL", "FEASIBLE"}


def test_when_teams_are_short_the_most_valuable_jobs_are_served(runtime, a6):
    jobs = [
        job(f"j{i}", p, site, rescue_team=1)
        for i, (p, site) in enumerate([(9, SITE_1), (7, SITE_2), (5, SITE_3), (1, SITE_4)], start=1)
    ]
    plan = a6.assign(jobs, now=runtime.clock())
    assert plan.status == "partial" and "1 of 4" in plan.reason
    assert {a.job_id for a in plan.assignments} == {"j1", "j2", "j3"}
    (unserved,) = plan.unserved
    assert (unserved.job_id, unserved.reason) == ("j4", "not_enough_units")
    (alert,) = plan.shortages
    assert (alert.kind, alert.demanded, alert.available, alert.unmet_jobs) == (
        "rescue_team",
        4,
        3,
        ["j4"],
    )


def test_a_job_is_served_completely_or_not_at_all(runtime, a6):
    """Four teams wanted, three exist: nobody is sent, and the teams remain free for other work."""
    plan = a6.assign(
        [job("huge", 9, SITE_1, rescue_team=4), job("small", 1, SITE_2, rescue_team=1)],
        now=runtime.clock(),
    )
    assert crew(plan, "huge") == [] and len(crew(plan, "small")) == 1
    (unserved,) = plan.unserved
    assert (unserved.job_id, unserved.reason, unserved.missing) == (
        "huge",
        "not_enough_units",
        {"rescue_team": 1},
    )


def test_priority_is_a_value_not_a_strict_rank(runtime, a6):
    """Two priority-3 jobs (6) beat one priority-5 job that would take every team."""
    plan = a6.assign(
        [
            job("big", 5, SITE_1, rescue_team=3),
            job("s1", 3, SITE_2, rescue_team=1),
            job("s2", 3, SITE_3, rescue_team=1),
        ],
        now=runtime.clock(),
    )
    assert {a.job_id for a in plan.assignments} == {"s1", "s2"}
    (unserved,) = plan.unserved
    # each need is satisfiable alone; the units went to worth more elsewhere
    assert (unserved.job_id, unserved.reason, unserved.missing) == (
        "big",
        "not_enough_units",
        {"rescue_team": 3},
    )


def test_units_marked_committed_are_not_sent_and_can_be_freed(runtime, a6):
    assert a6.record_availability(TEAMS[0], False, source="mission-1")
    plan = a6.assign([job("j1", 5, SITE_1, rescue_team=1)], now=runtime.clock())
    assert TEAMS[0] not in {a.unit_id for a in plan.assignments}
    assert plan.fleet["rescue_team"] == {"total": 3, "available": 2}
    assert a6.record_availability(TEAMS[0], True, source="mission-1-done")
    assert (
        a6.assign([job("j1", 5, SITE_1, rescue_team=1)], now=runtime.clock()).fleet["rescue_team"][
            "available"
        ]
        == 3
    )


def test_all_units_committed_means_nothing_can_be_served(runtime, a6):
    for unit in TEAMS:
        a6.record_availability(unit, False, source="busy")
    plan = a6.assign([job("j1", 5, SITE_1, rescue_team=1)], now=runtime.clock())
    assert plan.status == "none_served" and plan.assignments == []
    assert plan.unserved[0].reason == "not_enough_units"


def test_plans_are_deterministic_read_only_and_order_independent(runtime, a6):
    version = runtime.cop.version
    jobs = [
        job("a", 6, SITE_1, rescue_team=2, vehicle=1),
        job("b", 4, SITE_2, rescue_team=1),
        job("c", 2, SITE_3, vehicle=2),
    ]
    first = a6.assign(jobs, now=runtime.clock())
    assert first == a6.assign(list(reversed(jobs)), now=runtime.clock())
    assert runtime.cop.version == version


# ---------------------------------------------------------------- flooding


async def test_after_a_flood_units_take_dry_routes_and_a_cut_off_site_is_reported(
    runtime, clock, a6, mapping, rg
):
    """At 25 cm one job site's usual route is cut. The rest are unaffected; that one has no dry way."""
    flooded = wet(mapping, rg, 25)
    sites = {"j1": SITE_1, "j2": SITE_2, "j3": SITE_3, "j4": SITE_4}
    jobs = [job(j, 5, site, vehicle=1) for j, site in sites.items()]  # four vans, four jobs
    before = a6.assign(jobs, now=clock.now)
    assert before.status == "ok"
    assert any(crossed(a.route_nodes, flooded) for a in before.assignments), "nothing is affected"

    await runtime.bus.publish(observation(clock, 25))
    after = a6.assign(jobs, now=clock.now)
    assert after.status == "partial"
    assert not any(crossed(a.route_nodes, flooded) for a in after.assignments)
    (cut_off,) = after.unserved
    assert (cut_off.job_id, cut_off.reason) == ("j2", "no_safe_route")
    assert cut_off.missing == {"vehicle": 1}
    assert after.shortages == []  # more vans would not help: the road is the problem
    assert {a.job_id for a in after.assignments} == {"j1", "j3", "j4"}
    assert after.snapshot_version == runtime.cop.version > before.snapshot_version


async def test_when_the_depot_is_cut_off_no_unit_is_sent_and_a_human_is_asked(runtime, clock, a6):
    await runtime.bus.publish(observation(clock, 35))
    plan = a6.assign(
        [job("j1", 5, SITE_1, rescue_team=1), job("j2", 3, SITE_2, vehicle=1)], now=clock.now
    )
    assert plan.status == "none_served" and "escalate" in plan.reason
    assert plan.assignments == []
    assert {u.reason for u in plan.unserved} == {"no_safe_route"}  # units exist; roads stop them
    assert plan.shortages == []  # so asking for more units would be pointless


# -------------------------------------------------------- cooperation with A7


def test_a_shelter_resupply_request_becomes_a_vehicle_job(runtime, routing):
    """A3's glue: A7 asks for resupply in its plan; A3 hands A6 a job; A6 sends a vehicle."""
    a6, a7 = routing.logistics, routing.shelter
    shelter = "ratnapura-shelter-04"
    shelter_plan = a7.assign(
        [EvacueeGroup(group_id="g", people=36, origin=DEPOT)], [shelter], now=runtime.clock()
    )
    (request,) = shelter_plan.resupply_requests
    resupply = job(
        f"resupply:{request.shelter_id}", 2, a7.shelters[request.shelter_id].node, vehicle=1
    )
    plan = a6.assign([resupply], now=runtime.clock())
    assert plan.status == "ok"
    assert [a.kind for a in plan.assignments] == ["vehicle"]
    assert plan.assignments[0].route_nodes[-1] == a7.shelters[shelter].node


# ---------------------------------------------------------------- inputs


def test_availability_is_validated_and_versioned(runtime, a6):
    version = runtime.cop.version
    assert a6.record_availability(VANS[0], False, source="m")
    assert runtime.cop.version == version + 1
    belief = runtime.cop.get(f"resource:{VANS[0]}", "available")
    assert belief.value is False and belief.provenance == ["m"]
    older = runtime.clock() - timedelta(hours=1)
    assert a6.record_availability(VANS[0], True, source="stale", at=older) is False
    with pytest.raises(ValueError, match="Unknown unit"):
        a6.record_availability("nope", True, source="x")
    for bad in (1, "yes", None):
        with pytest.raises(ValueError, match="bool"):
            a6.record_availability(VANS[0], bad, source="x")


def test_locations_can_be_coordinates(runtime, a6, rg):
    plan = a6.assign(
        [job("j1", 5, LatLon(lat=6.6828, lon=80.3992), rescue_team=1)], now=runtime.clock()
    )
    assert plan.assignments[0].route_nodes[-1] == rg.nearest_node(6.6828, 80.3992)


@pytest.mark.parametrize("jobs", [[("dup", 1, SITE_1), ("dup", 2, SITE_2)], [("j", 1, 999)]])
def test_bad_planning_input_is_a_value_error(a6, jobs):
    with pytest.raises(ValueError):
        a6.assign([job(j, p, loc, rescue_team=1) for j, p, loc in jobs])


# ---------------------------------------------------------------- events


async def test_a_task_yields_an_addressed_candidate_plan(runtime):
    event = task()
    await runtime.bus.publish(event)
    (out,) = events(runtime, "candidate_plan")
    assert out["source"] == "a6_logistics" and out["incident_id"] == "incident:x"
    payload = CandidatePlanPayload.model_validate(out["payload"])
    assert (payload.task_id, payload.task, payload.agent, payload.status) == (
        "t-6",
        "logistics",
        "a6_logistics",
        "ok",
    )
    plan = LogisticsPlan.model_validate(payload.plan)
    assert plan.assignments and plan.snapshot_version == runtime.cop.version
    assert out["id"] == sha256(f"{event.id}:candidate_plan:a6_logistics".encode()).hexdigest()


async def test_a_task_for_another_planner_is_answered_by_that_planner_only(runtime):
    await runtime.bus.publish(
        Event(
            type=EventType.TASK_ASSIGNED,
            source="a3_incident_commander",
            payload={"assignee": "a4_evacuation", "task_id": "t-4", "origin": DEPOT},
        )
    )
    assert [e["source"] for e in events(runtime, "candidate_plan")] == ["a4_evacuation"]


@pytest.mark.parametrize(
    "bad",
    [
        {"jobs": []},
        {"jobs": [{"job_id": "j", "priority": 0, "location": SITE_1, "needs": {"vehicle": 1}}]},
        {"jobs": [{"job_id": "j", "priority": 1, "location": SITE_1, "needs": {}}]},
        {"jobs": [{"job_id": "j", "priority": 1, "location": SITE_1, "needs": {"drone": 1}}]},
        {"jobs": [{"job_id": "j", "priority": 1, "location": 999, "needs": {"vehicle": 1}}]},
    ],
)
async def test_a_bad_task_gets_an_error_plan_and_does_not_wedge_the_bus(runtime, bad):
    await runtime.bus.publish(task(**bad))
    await runtime.bus.publish(task(task_id="t-7"))  # the bus still delivers what follows
    first, second = [
        CandidatePlanPayload.model_validate(e["payload"]) for e in events(runtime, "candidate_plan")
    ]
    assert first.status == "invalid_task" and first.task_id == "t-6"
    assert LogisticsPlan.model_validate(first.plan).reason
    assert second.status == "ok"


async def test_an_unexpected_bug_is_reported_not_raised(runtime, a6, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(a6, "assign", boom)
    await runtime.bus.publish(task())
    (out,) = events(runtime, "candidate_plan")
    assert out["payload"]["status"] == "error"
    assert "boom" in out["payload"]["plan"]["reason"]


# ------------------------------------------------------------ construction


def test_construction_validates_the_fleet(runtime, rg):
    with pytest.raises(ValueError, match="No resources known"):
        Logistics(runtime.bus, runtime.cop, runtime.ledger, road_graph=rg, resources=[])
    off_graph = load_resources("ratnapura")[0].model_copy(update={"node": 1})
    with pytest.raises(ValueError, match="not on the ratnapura road graph"):
        Logistics(runtime.bus, runtime.cop, runtime.ledger, road_graph=rg, resources=[off_graph])


def test_attach_routing_registers_a6(runtime):
    types, _ = runtime.bus.handlers["a6_logistics"]
    assert types == {EventType.TASK_ASSIGNED}
