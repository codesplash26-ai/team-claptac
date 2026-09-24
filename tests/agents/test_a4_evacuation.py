"""A4 Evacuation: sensor evidence -> road beliefs -> safe routes, on the real Ratnapura network."""

from datetime import timedelta
from hashlib import sha256

import networkx as nx
import pytest

from atlas.agents.a4_evacuation import Evacuation
from atlas.agents.contracts import CandidatePlanPayload, LatLon, RoutePlan
from atlas.agents.planning.commander import CommanderAdapter
from atlas.agents.wiring import attach_routing
from atlas.control.models import DecisionStatus
from atlas.control.wiring import attach_control
from atlas.core.config import planner_config_for
from atlas.core.events import Belief, Event, EventType, ProposedAction
from atlas.reasoning import Planner, PlanningRequest
from atlas.tools import geo
from atlas.tools.reference import Shelter, load_shelters
from atlas.tools.sensor_map import SensorMap
from tests.conftest import events
from tests.routing_helpers import (
    AREA,
    DEMO_LEVEL_CM,
    DEMO_ORIGIN,
    DEPOT,
    crossed,
    edge_beliefs,
    observation,
    wet,
)


@pytest.fixture(autouse=True)
def routing(runtime):
    """Every test here runs with A4 registered on the runtime's bus."""
    return attach_routing(runtime)


@pytest.fixture
def a4(routing):
    return routing.evacuation


@pytest.fixture(scope="module")
def mapping():
    return SensorMap.from_file().get(AREA)


@pytest.fixture(scope="module")
def rg():
    return geo.load_site("ratnapura")


def task(**overrides):
    payload = {
        "task": "evacuation",
        "assignee": "a4_evacuation",
        "task_id": "t-1",
        "origin": DEPOT,
        **overrides,
    }
    return Event(
        type=EventType.TASK_ASSIGNED,
        source="a3_incident_commander",
        incident_id="incident:x",
        payload=payload,
    )


# ------------------------------------------------------ evidence -> beliefs -> COP


async def test_a_reading_writes_road_beliefs_and_announces_closures(runtime, clock, mapping, rg):
    await runtime.bus.publish(observation(clock, 25))
    flooded = wet(mapping, rg, 25)

    beliefs = edge_beliefs(runtime)
    assert set(beliefs) == flooded  # dry roads barely move from the prior, so are not written
    assert all(b.value < 0.3 for b in beliefs.values())
    sample = next(iter(beliefs.values()))
    assert sample.provenance == ["ev-1", f"sensor_map:{AREA}"]
    assert sample.updated_at == clock.now

    (risk,) = events(runtime, "risk_update")
    assert risk["source"] == "a4_evacuation"
    assert set(risk["payload"]["newly_closed"]) == flooded
    assert risk["payload"]["newly_reopened"] == []
    assert risk["payload"]["snapshot_version"] == runtime.cop.version


async def test_a_steady_reading_writes_rarely_not_on_every_reading(runtime, clock):
    """Every write moves the COP version and can stale a plan at the Gateway, so a repeated reading
    is only written when its evidence needs refreshing (decay drift passes the write threshold)."""
    bumped = []
    for _ in range(12):
        clock.now += timedelta(minutes=1)
        before = runtime.cop.version
        await runtime.bus.publish(observation(clock, 25))
        bumped.append(runtime.cop.version != before)
    assert bumped[:2] == [True, True]  # first sighting, then saturation at the minimum belief
    assert sum(bumped) <= 5  # mostly quiet afterwards
    assert any(not a and not b for a, b in zip(bumped, bumped[1:]))  # with quiet stretches


async def test_falling_water_reopens_roads_and_says_so(runtime, clock, mapping, rg):
    await runtime.bus.publish(observation(clock, 25))
    recovering = sorted(wet(mapping, rg, 25) - wet(mapping, rg, 0))
    assert recovering
    for _ in range(8):
        clock.now += timedelta(minutes=1)
        await runtime.bus.publish(observation(clock, 0))
    beliefs = edge_beliefs(runtime)
    assert all(beliefs[e].value >= 0.5 for e in recovering)
    reopened = {e for r in events(runtime, "risk_update") for e in r["payload"]["newly_reopened"]}
    assert set(recovering) <= reopened


async def test_unmapped_areas_other_measurements_and_junk_are_ignored(runtime, clock):
    await runtime.bus.publish(observation(clock, 30, area="nowhere"))
    await runtime.bus.publish(observation(clock, 30, attribute="rainfall"))
    await runtime.bus.publish(
        Event(type=EventType.OBSERVATION_VALIDATED, source="a1_sentinel", payload={"junk": 1})
    )
    assert edge_beliefs(runtime) == {}
    assert events(runtime, "risk_update") == []


async def test_risk_update_ids_are_deterministic_so_a_retry_cannot_duplicate_it(runtime, clock):
    event = observation(clock, 25)
    await runtime.bus.publish(event)
    (risk,) = events(runtime, "risk_update")
    assert risk["id"] == sha256(f"{event.id}:risk_update:a4_evacuation".encode()).hexdigest()


# ------------------------------------------------------------------- planning


def test_baseline_plan_is_safe_ranked_and_reads_only(runtime, a4):
    version = runtime.cop.version
    plan = a4.plan_evacuation(DEPOT, now=runtime.clock())
    assert runtime.cop.version == version  # planning never writes to the COP
    assert plan.status == "ok" and plan.reason == ""
    assert plan.snapshot_version == version
    shelter_nodes = {s.node for s in load_shelters("ratnapura")}
    assert [o.rank for o in plan.options] == [1, 2, 3]
    assert len({o.route_id for o in plan.options}) == 3
    costs = [o.cost for o in plan.options]
    assert costs == sorted(costs)
    for o in plan.options:
        assert o.nodes[0] == DEPOT and o.destination in shelter_nodes
        assert o.safe and o.observed_edges == 0 and o.unobserved_edges == o.hops
        assert o.failure_probability == pytest.approx(1 - o.success_probability)
    assert plan.recommended is plan.options[0]
    assert plan == a4.plan_evacuation(DEPOT, now=runtime.clock())  # deterministic


async def test_after_a_flood_the_plan_avoids_flooded_roads(runtime, clock, a4, mapping, rg):
    before = a4.plan_evacuation(DEMO_ORIGIN, now=clock.now)
    flooded = wet(mapping, rg, DEMO_LEVEL_CM)
    assert crossed(before.recommended.nodes, flooded), "the usual route should be affected"

    await runtime.bus.publish(observation(clock, DEMO_LEVEL_CM))
    after = a4.plan_evacuation(DEMO_ORIGIN, now=clock.now)
    assert after.status == "ok"
    assert after.recommended.shelter_id != before.recommended.shelter_id  # a different shelter
    for option in after.options:
        assert option.safe and not crossed(option.nodes, flooded)
        assert option.weakest_belief >= a4.config.close_threshold
    assert after.snapshot_version == runtime.cop.version > before.snapshot_version


async def test_at_the_alarm_level_the_shelter_behind_the_valley_is_dropped_not_the_recommendation(
    runtime, clock, a4
):
    """At 25 cm only shelter-02, whose approach crosses the valley, becomes unreachable."""
    before = a4.plan_evacuation(DEPOT, ["ratnapura-shelter-02"], now=clock.now)
    assert before.status == "ok"
    await runtime.bus.publish(observation(clock, 25))
    after = a4.plan_evacuation(DEPOT, ["ratnapura-shelter-02"], now=clock.now)
    assert after.status == "no_safe_route"
    rest = a4.plan_evacuation(DEPOT, now=clock.now)
    assert rest.status == "ok" and "ratnapura-shelter-02" not in {
        o.shelter_id for o in rest.options
    }


async def test_when_every_route_is_cut_off_the_plan_says_so_and_flags_them_unsafe(
    runtime, clock, a4, mapping, rg
):
    await runtime.bus.publish(observation(clock, 35))
    plan = a4.plan_evacuation(DEPOT, now=clock.now)
    assert plan.status == "no_safe_route"
    assert "escalate" in plan.reason
    assert plan.options and all(not o.safe for o in plan.options)
    assert all(crossed(o.nodes, wet(mapping, rg, 35)) for o in plan.options)
    assert plan.evidence_ids and "ev-1" in plan.evidence_ids  # the evidence behind the risk
    assert plan.to_commander_state(20)["routes"] == {}
    assert plan.to_commander_state(20, include_unsafe=True)["routes"]


async def test_no_safe_route_is_a_cue_for_a3_to_do_nothing_and_escalate(runtime, clock, a4):
    await runtime.bus.publish(observation(clock, 35))
    plan = a4.plan_evacuation(DEPOT, now=clock.now)
    request = PlanningRequest(
        agent="a3_incident_commander",
        state=plan.to_commander_state(20),
        snapshot_version=plan.snapshot_version,
    )
    result = await Planner(planner_config_for(request.agent)).plan(request, CommanderAdapter())
    assert result.action is None


def test_hard_closures_from_a8_are_never_routed_through(runtime, clock, a4, rg):
    plan = a4.plan_evacuation(DEPOT, now=clock.now)
    route_edges = list(zip(plan.recommended.nodes[:-1], plan.recommended.nodes[1:]))
    shelters = [s.node for s in load_shelters("ratnapura")]

    def has_detour(edge):
        remaining = nx.DiGraph(rg.graph)
        remaining.remove_edge(*edge)
        return any(nx.has_path(remaining, DEPOT, s) for s in shelters)

    # The depot has few exits, so close a road on the route that still leaves some other way out.
    u, v = next(edge for edge in route_edges if has_detour(edge))
    runtime.cop.put(
        Belief(
            subject=f"edge:{geo.edge_id(u, v)}",
            attribute="open",
            value=False,
            confidence=1.0,
            provenance=["action:close-1"],
            updated_at=clock.now,
        )
    )
    after = a4.plan_evacuation(DEPOT, now=clock.now)
    assert after.status == "ok"
    assert all((u, v) not in zip(o.nodes[:-1], o.nodes[1:]) for o in after.options)


def test_closing_the_only_exit_leaves_no_route_at_all(runtime, clock, a4, rg):
    exits = list(rg.graph.out_edges(DEPOT))
    for u, v in exits:
        runtime.cop.put(
            Belief(
                subject=f"edge:{geo.edge_id(u, v)}",
                attribute="open",
                value=False,
                confidence=1.0,
                updated_at=clock.now,
            )
        )
    plan = a4.plan_evacuation(DEPOT, now=clock.now)
    assert plan.status == "no_route" and plan.options == []


async def test_time_decay_is_applied_at_plan_time_and_never_persisted(runtime, clock, a4):
    await runtime.bus.publish(observation(clock, 35))
    version = runtime.cop.version
    now_plan = a4.plan_evacuation(DEPOT, now=clock.now)
    later_plan = a4.plan_evacuation(DEPOT, now=clock.now + timedelta(hours=3))
    assert runtime.cop.version == version
    assert min(o.weakest_belief for o in later_plan.options) > max(
        o.weakest_belief for o in now_plan.options
    )  # old evidence fades toward "unknown"


def test_origin_can_be_a_coordinate_and_destinations_shelters_or_nodes(a4, rg, clock):
    plan = a4.plan_evacuation(LatLon(lat=6.6828, lon=80.3992), now=clock.now)
    assert plan.origin == rg.nearest_node(6.6828, 80.3992)

    by_id = a4.plan_evacuation(DEPOT, ["ratnapura-shelter-03"], now=clock.now)
    target = next(s.node for s in load_shelters("ratnapura") if s.id == "ratnapura-shelter-03")
    assert {o.destination for o in by_id.options} == {target}
    assert by_id.recommended.shelter_id == "ratnapura-shelter-03"
    assert by_id.recommended.capacity == 60

    by_node = a4.plan_evacuation(DEPOT, [target], now=clock.now)
    assert by_node.recommended.nodes == by_id.recommended.nodes


@pytest.mark.parametrize(
    ("origin", "destinations"),
    [(999, None), (DEPOT, ["no-such-shelter"]), (DEPOT, [999]), (DEPOT, [])],
)
def test_bad_planning_input_is_a_value_error(a4, origin, destinations):
    with pytest.raises(ValueError):
        a4.plan_evacuation(origin, destinations)


def test_a_plan_feeds_a3s_planner_directly(runtime, a4):
    plan = a4.plan_evacuation(DEPOT, now=runtime.clock())
    state = plan.to_commander_state(people=20)
    assert {r["capacity"] for r in state["routes"].values()} <= {120, 80, 60, 40}
    request = PlanningRequest(
        agent="a3_incident_commander",
        state=state,
        snapshot_version=plan.snapshot_version,
        valid_until=plan.valid_until,
    )
    import asyncio

    result = asyncio.run(
        Planner(planner_config_for(request.agent)).plan(request, CommanderAdapter())
    )
    assert result.action is not None and result.action.params["route"] in state["routes"]
    result.require_current(runtime.cop.version)


# --------------------------------------------------------------- task events


async def test_a_task_yields_an_addressed_candidate_plan(runtime, routing):
    event = task(k=2)
    await runtime.bus.publish(event)
    (out,) = events(runtime, "candidate_plan")
    assert out["source"] == "a4_evacuation" and out["incident_id"] == "incident:x"
    payload = CandidatePlanPayload.model_validate(out["payload"])
    assert (payload.task_id, payload.task, payload.agent, payload.status) == (
        "t-1",
        "evacuation",
        "a4_evacuation",
        "ok",
    )
    plan = RoutePlan.model_validate(payload.plan)
    assert len(plan.options) == 2 and plan.snapshot_version == runtime.cop.version
    assert out["id"] == sha256(f"{event.id}:candidate_plan:a4_evacuation".encode()).hexdigest()


async def test_tasks_for_other_planners_are_ignored(runtime, routing):
    await runtime.bus.publish(task(assignee="a5_triage"))  # A5 is not built yet
    await runtime.bus.publish(Event(type=EventType.TASK_ASSIGNED, source="a3", payload={}))
    assert events(runtime, "candidate_plan") == []


@pytest.mark.parametrize(
    "bad", [{"origin": None}, {"origin": 999}, {"destinations": ["nope"]}, {"k": 0}]
)
async def test_a_bad_task_gets_an_error_plan_and_does_not_wedge_the_bus(runtime, routing, bad):
    await runtime.bus.publish(task(**bad))
    await runtime.bus.publish(task(task_id="t-2"))  # the bus still delivers what follows
    first, second = [
        CandidatePlanPayload.model_validate(e["payload"]) for e in events(runtime, "candidate_plan")
    ]
    assert first.status == "invalid_task" and first.task_id == "t-1"
    assert RoutePlan.model_validate(first.plan).reason
    assert second.status == "ok"


async def test_an_unexpected_bug_is_reported_not_raised(runtime, a4, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(a4, "plan_evacuation", boom)
    await runtime.bus.publish(task())
    (out,) = events(runtime, "candidate_plan")
    assert out["payload"]["status"] == "error"
    assert "boom" in out["payload"]["plan"]["reason"]


# ------------------------------------------------------ construction and wiring


def test_construction_validates_shelters_and_ignores_other_sites_areas(runtime, mapping, rg):
    off_graph = Shelter(id="s", site="ratnapura", node=1, lat=0, lon=0, elevation_m=0, capacity=5)
    with pytest.raises(ValueError, match="not on the ratnapura road graph"):
        Evacuation(
            runtime.bus,
            runtime.cop,
            runtime.ledger,
            road_graph=rg,
            sensor_map=SensorMap([mapping]),
            shelters=[off_graph],
        )

    elsewhere = mapping.model_copy(update={"area_id": "kandy-river", "site": "kandy"})
    agent = Evacuation(
        runtime.bus,
        runtime.cop,
        runtime.ledger,
        road_graph=rg,
        sensor_map=SensorMap([mapping, elsewhere]),
    )
    assert set(agent.areas) == {AREA}


def test_attach_routing_registers_a4_on_the_bus(runtime, routing):
    assert "a4_evacuation" in runtime.bus.handlers
    types, _ = runtime.bus.handlers["a4_evacuation"]
    assert types == {EventType.OBSERVATION_VALIDATED, EventType.TASK_ASSIGNED}


# ------------------------------------------------------- with the control boundary


async def test_a_plans_snapshot_satisfies_the_gateway_until_the_cop_moves(runtime, clock, routing):
    control = attach_control(runtime)
    a4, rg = routing.evacuation, routing.road_graph

    def proposal(key, plan):
        route = plan.recommended
        return ProposedAction(
            idempotency_key=key,
            incident_id="inc-1",
            proposed_by="a3_incident_commander",
            executor="a8_dispatch",
            action_type="create_mission",
            params={
                "title": f"Evacuate to {route.shelter_id}",
                "resources_committed": 2,
                "destination": route.shelter_id,
                "route": [list(rg.pos(n)) for n in route.nodes],
            },
            snapshot_version=plan.snapshot_version,
        )

    plan = a4.plan_evacuation(DEPOT, now=clock.now)
    assert control.gateway.submit(proposal("k1", plan)).status is DecisionStatus.AUTHORISED

    await runtime.bus.publish(observation(clock, 25))  # the river rises; A4 updates the COP
    stale = control.gateway.submit(proposal("k2", plan))
    assert (stale.status, stale.reason) == (DecisionStatus.BLOCKED, "stale_snapshot")

    replanned = a4.plan_evacuation(DEPOT, now=clock.now)  # replan on the new snapshot
    assert control.gateway.submit(proposal("k3", replanned)).status is DecisionStatus.AUTHORISED
