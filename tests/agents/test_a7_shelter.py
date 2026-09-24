"""A7 Shelter & Relief: capacity- and access-aware assignment on the real Ratnapura network."""

from datetime import timedelta
from hashlib import sha256

import pytest

from atlas.agents.a7_shelter import ShelterRelief
from atlas.agents.contracts import CandidatePlanPayload, EvacueeGroup, LatLon, ShelterPlan
from atlas.agents.wiring import attach_routing
from atlas.core.events import Event, EventType
from atlas.tools import geo
from atlas.tools.reference import load_shelters
from atlas.tools.sensor_map import SensorMap
from tests.conftest import events
from tests.routing_helpers import (
    AREA,
    DEMO_LEVEL_CM,
    DEMO_ORIGIN,
    DEPOT,
    crossed,
    observation,
    wet,
)

CAPACITY = {s.id: s.capacity for s in load_shelters("ratnapura")}
TOTAL_CAPACITY = sum(CAPACITY.values())


@pytest.fixture(autouse=True)
def routing(runtime):
    """Every test here runs with A4 and A7 registered on the runtime's bus."""
    return attach_routing(runtime)


@pytest.fixture
def a7(routing):
    return routing.shelter


@pytest.fixture(scope="module")
def mapping():
    return SensorMap.from_file().get(AREA)


@pytest.fixture(scope="module")
def rg():
    return geo.load_site("ratnapura")


def group(group_id, people, origin=DEPOT):
    return EvacueeGroup(group_id=group_id, people=people, origin=origin)


def housed_by_shelter(plan):
    out = {}
    for p in plan.placements:
        out[p.shelter_id] = out.get(p.shelter_id, 0) + p.people
    return out


def task(**overrides):
    payload = {
        "task": "shelter",
        "assignee": "a7_shelter",
        "task_id": "t-7",
        "groups": [{"group_id": "g1", "people": 30, "origin": DEPOT}],
        **overrides,
    }
    return Event(
        type=EventType.TASK_ASSIGNED,
        source="a3_incident_commander",
        incident_id="incident:x",
        payload=payload,
    )


# --------------------------------------------------------------- assignment


def test_a_group_is_placed_along_a_safe_route_within_capacity(runtime, a7):
    plan = a7.assign([group("g1", 40)], now=runtime.clock())
    assert plan.status == "ok" and plan.reason == "" and plan.unplaced == []
    assert sum(p.people for p in plan.placements) == 40
    for p in plan.placements:
        assert p.route_nodes[0] == DEPOT
        assert p.route_nodes[-1] == a7.shelters[p.shelter_id].node
        assert p.weakest_belief >= a7.config.routing.close_threshold
    assert all(housed_by_shelter(plan)[s] <= CAPACITY[s] for s in housed_by_shelter(plan))
    assert plan.snapshot_version == runtime.cop.version
    assert plan.solver_status in {"OPTIMAL", "FEASIBLE"}


def test_the_nearest_shelter_is_filled_first(runtime, a7, routing):
    a4 = routing.evacuation
    minutes = {
        sid: a4.plan_evacuation(DEPOT, [sid], k=1).recommended.minutes for sid in a7.shelters
    }
    nearest = min(minutes, key=lambda sid: (minutes[sid], sid))
    plan = a7.assign([group("g1", 30)], now=runtime.clock())
    assert {p.shelter_id for p in plan.placements} == {nearest}


def test_a_big_group_is_split_across_shelters_or_left_out_if_splitting_is_off(runtime, a7):
    people = max(CAPACITY.values()) + 30  # bigger than any single shelter
    split = a7.assign([group("big", people)], allow_split=True, now=runtime.clock())
    assert split.status == "ok" and len({p.shelter_id for p in split.placements}) >= 2
    whole = a7.assign([group("big", people)], allow_split=False, now=runtime.clock())
    assert whole.status == "no_shelter" and whole.placements == []
    assert [(u.group_id, u.people, u.reason) for u in whole.unplaced] == [
        ("big", people, "no_capacity")
    ]


def test_demand_beyond_total_capacity_is_partial_and_never_exceeds_a_shelter(runtime, a7):
    groups = [group(f"g{i}", 70) for i in range(5)]  # 350 people for 300 places
    plan = a7.assign(groups, now=runtime.clock())
    assert plan.status == "partial" and "50 of 350" in plan.reason
    assert sum(p.people for p in plan.placements) == TOTAL_CAPACITY
    assert sum(u.people for u in plan.unplaced) == 350 - TOTAL_CAPACITY
    assert {u.reason for u in plan.unplaced} == {"no_capacity"}
    for s in plan.shelters:
        assert s.occupancy_after <= s.capacity


def test_recorded_occupancy_reduces_the_free_places(runtime, a7):
    fullest = max(CAPACITY, key=CAPACITY.get)
    assert a7.record_occupancy(fullest, CAPACITY[fullest] - 10, source="field-report-1")
    plan = a7.assign([group("g1", 100)], now=runtime.clock())
    state = {s.shelter_id: s for s in plan.shelters}[fullest]
    assert state.occupancy_before == CAPACITY[fullest] - 10
    assert housed_by_shelter(plan).get(fullest, 0) <= 10
    assert plan.status == "ok"


def test_a_full_shelter_is_never_used(runtime, a7):
    nearest_full = "ratnapura-shelter-02"
    a7.record_occupancy(nearest_full, CAPACITY[nearest_full], source="field-report-2")
    plan = a7.assign([group("g1", 60)], now=runtime.clock())
    assert nearest_full not in housed_by_shelter(plan)
    assert plan.status == "ok"


def test_plans_are_deterministic_and_read_only(runtime, a7):
    version = runtime.cop.version
    groups = [group("a", 45), group("b", 80), group("c", 25)]
    first = a7.assign(groups, now=runtime.clock())
    assert first == a7.assign(list(reversed(groups)), now=runtime.clock())
    assert runtime.cop.version == version


# --------------------------------------------------------------- flooding


async def test_after_a_flood_routes_avoid_flooded_roads(runtime, clock, a7, mapping, rg):
    before = a7.assign([group("g1", 40, origin=DEMO_ORIGIN)], now=clock.now)
    flooded = wet(mapping, rg, DEMO_LEVEL_CM)
    assert any(crossed(p.route_nodes, flooded) for p in before.placements)

    await runtime.bus.publish(observation(clock, DEMO_LEVEL_CM))
    after = a7.assign([group("g1", 40, origin=DEMO_ORIGIN)], now=clock.now)
    assert after.status == "ok"
    assert not any(crossed(p.route_nodes, flooded) for p in after.placements)
    assert {p.shelter_id for p in after.placements} != {p.shelter_id for p in before.placements}
    assert after.snapshot_version == runtime.cop.version > before.snapshot_version


async def test_when_the_depot_is_cut_off_nobody_is_placed_and_a_human_is_asked(runtime, clock, a7):
    await runtime.bus.publish(observation(clock, 35))
    plan = a7.assign([group("g1", 40)], now=clock.now)
    assert plan.status == "no_shelter" and "escalate" in plan.reason
    assert [(u.group_id, u.reason) for u in plan.unplaced] == [("g1", "no_safe_route")]
    assert plan.placements == []


async def test_groups_are_judged_from_where_they_are(runtime, clock, a7, rg):
    """A group already on high ground still reaches a shelter while the depot is cut off."""
    await runtime.bus.publish(observation(clock, 35))
    hill = a7.shelters["ratnapura-shelter-01"].node
    neighbour = next(iter(rg.graph.successors(hill)))
    plan = a7.assign([group("cut_off", 20), group("safe", 20, origin=neighbour)], now=clock.now)
    assert plan.status == "partial"
    assert {p.group_id for p in plan.placements} == {"safe"}
    assert [(u.group_id, u.reason) for u in plan.unplaced] == [("cut_off", "no_safe_route")]


# --------------------------------------------------------------- resupply


def test_resupply_is_requested_only_for_shelters_this_plan_fills_up(runtime, a7):
    small = "ratnapura-shelter-04"  # capacity 40
    plan = a7.assign([group("g1", 34)], ["ratnapura-shelter-04"], now=runtime.clock())
    (request,) = plan.resupply_requests
    assert (request.shelter_id, request.urgency) == (small, "routine")  # 34/40 = 85%
    assert "34/40" in request.reason

    urgent = a7.assign([group("g1", 39)], [small], now=runtime.clock())
    assert urgent.resupply_requests[0].urgency == "high"  # 97.5%

    quiet = a7.assign([group("g1", 10)], [small], now=runtime.clock())
    assert quiet.resupply_requests == []


def test_a_shelter_that_is_already_full_but_gets_nobody_new_asks_for_nothing(runtime, a7):
    a7.record_occupancy("ratnapura-shelter-04", 40, source="field")
    plan = a7.assign([group("g1", 30)], now=runtime.clock())
    assert "ratnapura-shelter-04" not in housed_by_shelter(plan)
    assert all(r.shelter_id != "ratnapura-shelter-04" for r in plan.resupply_requests)


# --------------------------------------------------------------- inputs


def test_occupancy_is_validated_and_written_to_the_cop(runtime, a7):
    version = runtime.cop.version
    assert a7.record_occupancy("ratnapura-shelter-01", 12, source="field-1")
    assert runtime.cop.version == version + 1
    belief = runtime.cop.get("shelter:ratnapura-shelter-01", "occupancy")
    assert belief.value == 12 and belief.provenance == ["field-1"]
    older = runtime.clock() - timedelta(hours=1)
    assert a7.record_occupancy("ratnapura-shelter-01", 99, source="stale", at=older) is False
    for people in (-1, 121, True, 1.5):
        with pytest.raises(ValueError, match="Occupancy"):
            a7.record_occupancy("ratnapura-shelter-01", people, source="x")
    with pytest.raises(ValueError, match="Unknown shelter"):
        a7.record_occupancy("nowhere", 1, source="x")


def test_origins_and_shelter_subsets(runtime, a7, rg):
    plan = a7.assign(
        [group("g1", 10, origin=LatLon(lat=6.6828, lon=80.3992))],
        ["ratnapura-shelter-03"],
        now=runtime.clock(),
    )
    assert {p.shelter_id for p in plan.placements} == {"ratnapura-shelter-03"}
    assert plan.placements[0].route_nodes[0] == rg.nearest_node(6.6828, 80.3992)
    assert [s.shelter_id for s in plan.shelters] == ["ratnapura-shelter-03"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"groups": [group("dup", 5), group("dup", 6)]},
        {"groups": [group("g", 5)], "shelter_ids": ["nope"]},
        {"groups": [group("g", 5)], "shelter_ids": []},
        {"groups": [group("g", 5, origin=999)]},
    ],
)
def test_bad_planning_input_is_a_value_error(a7, kwargs):
    with pytest.raises(ValueError):
        a7.assign(kwargs.pop("groups"), **kwargs)


# --------------------------------------------------------------- events


async def test_a_task_yields_an_addressed_candidate_plan(runtime):
    event = task()
    await runtime.bus.publish(event)
    (out,) = events(runtime, "candidate_plan")
    assert out["source"] == "a7_shelter" and out["incident_id"] == "incident:x"
    payload = CandidatePlanPayload.model_validate(out["payload"])
    assert (payload.task_id, payload.task, payload.agent, payload.status) == (
        "t-7",
        "shelter",
        "a7_shelter",
        "ok",
    )
    plan = ShelterPlan.model_validate(payload.plan)
    assert plan.placements and plan.snapshot_version == runtime.cop.version
    assert out["id"] == sha256(f"{event.id}:candidate_plan:a7_shelter".encode()).hexdigest()


async def test_a_task_for_a4_is_answered_by_a4_only(runtime):
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
        {"groups": []},
        {"groups": [{"group_id": "g", "people": 0, "origin": DEPOT}]},
        {"groups": [{"group_id": "g", "people": 5, "origin": 999}]},
        {"shelter_ids": ["nope"]},
    ],
)
async def test_a_bad_task_gets_an_error_plan_and_does_not_wedge_the_bus(runtime, bad):
    await runtime.bus.publish(task(**bad))
    await runtime.bus.publish(task(task_id="t-8"))  # the bus still delivers what follows
    first, second = [
        CandidatePlanPayload.model_validate(e["payload"]) for e in events(runtime, "candidate_plan")
    ]
    assert first.status == "invalid_task" and first.task_id == "t-7"
    assert ShelterPlan.model_validate(first.plan).reason
    assert second.status == "ok"


async def test_an_unexpected_bug_is_reported_not_raised(runtime, a7, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(a7, "assign", boom)
    await runtime.bus.publish(task())
    (out,) = events(runtime, "candidate_plan")
    assert out["payload"]["status"] == "error"
    assert "boom" in out["payload"]["plan"]["reason"]


# ------------------------------------------------------------ construction


def test_construction_validates_shelters(runtime, rg):
    with pytest.raises(ValueError, match="No shelters known"):
        ShelterRelief(runtime.bus, runtime.cop, runtime.ledger, road_graph=rg, shelters=[])
    off_graph = load_shelters("ratnapura")[0].model_copy(update={"node": 1})
    with pytest.raises(ValueError, match="not on the ratnapura road graph"):
        ShelterRelief(runtime.bus, runtime.cop, runtime.ledger, road_graph=rg, shelters=[off_graph])


def test_attach_routing_registers_a7(runtime):
    types, _ = runtime.bus.handlers["a7_shelter"]
    assert types == {EventType.TASK_ASSIGNED}
