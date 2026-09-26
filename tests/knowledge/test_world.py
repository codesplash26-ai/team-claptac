"""The world graph: what the projector builds from real data, and what it refuses to invent."""

from datetime import datetime, timedelta, timezone

import pytest

from atlas.agents.a8_dispatch import Mission, MissionStatus
from atlas.agents.wiring import attach_routing
from atlas.knowledge.ontology import YamlOntology
from atlas.knowledge.world import (
    WorldConfig,
    WorldProjector,
    WorldSources,
    build_world,
    graph_claims,
    load_areas,
)
from atlas.tools import geo
from atlas.tools.reference import load_resources, load_shelters
from tests.routing_helpers import observation

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
AREA = "area:ratnapura-demo-river"


@pytest.fixture(scope="module")
def rg():
    return geo.load_site("ratnapura")


@pytest.fixture(scope="module")
def definition():
    return YamlOntology.read()


def sources(rg, **overrides):
    base = dict(
        road_graph=rg,
        shelters=load_shelters("ratnapura"),
        resources=load_resources("ratnapura"),
        areas=load_areas(),
        now=NOW,
    )
    return WorldSources(**{**base, **overrides})


@pytest.fixture(scope="module")
def static(rg, definition):
    return build_world(sources(rg), definition)


def belief(subject, attribute, value, *, confidence=0.9, updated=NOW, provenance=("ev-1",)):
    return {
        "subject": subject,
        "attribute": attribute,
        "value": value,
        "confidence": confidence,
        "provenance": list(provenance),
        "updated_at": updated.isoformat(),
        "ttl_seconds": None,
    }


def snapshot(*beliefs, version=7):
    return {"version": version, "beliefs": list(beliefs), "taken_at": NOW.isoformat()}


def a_road(rg, index=0):
    u, v = sorted(rg.graph.edges)[index]
    return u, v, f"road:{u}-{v}"


def a_route(rg, hops=3):
    """A real drivable path of `hops` roads that never revisits a junction, as node ids."""
    path = [sorted(rg.graph.nodes)[0]]
    for _ in range(hops):
        options = [n for n in sorted(rg.graph.successors(path[-1])) if n not in path]
        path.append(options[0])
    return path


def incident_state(rg, *, missions, incident="incident:ratnapura-demo-river", hazard="flood"):
    return {
        "incident_id": incident,
        "area_id": "ratnapura-demo-river",
        "area_name": "Ratnapura riverside",
        "hazard": hazard,
        "state": "active",
        "cycle": 1,
        "opened_at": NOW.isoformat(),
        "needs_human": False,
        "missions": missions,
    }


def mission_info(mission_id, route, *, shelter="ratnapura-shelter-01", status="created", units=()):
    return {
        "mission_id": mission_id,
        "status": status,
        "shelter_id": shelter,
        "route": route,
        "people": 30,
        "units": list(units),
    }


# ============================================================================ the static world


def test_the_static_world_has_every_road_junction_shelter_and_unit(static, rg):
    assert len(static.entities("Road")) == rg.graph.number_of_edges()
    assert len(static.entities("Junction")) == rg.graph.number_of_nodes()
    assert len(static.entities("Shelter")) == 4 and len(static.entities("Resource")) == 7
    assert len(static.entities("Area")) == 3
    assert static.report["skipped"] == {}


def test_every_fact_in_the_projection_passes_the_ontology_that_describes_it(static):
    """A projection its own validator rejects is a bug in the projector; this is how we find it."""
    ontology = YamlOntology.load(graph=static)
    claims = list(graph_claims(static))
    assert len(claims) > 1500
    bad = [(c, v.code, v.message) for c in claims for v in ontology.validate(c)]
    assert bad == []


def test_the_projection_can_be_checked_one_entity_at_a_time(static):
    ontology = YamlOntology.load(graph=static)
    shelter = "shelter:ratnapura-shelter-01"
    claims = list(graph_claims(static, shelter))
    assert {c.predicate for c in claims} >= {"capacity", "elevation", "located_at", "located_in"}
    assert all(ontology.validate(c) == [] for c in claims)


def test_roads_join_junctions_and_carry_their_travel_time(static, rg):
    u, v, road = a_road(rg)
    assert static.neighbors(road, "from_junction") == [f"junction:{u}"]
    assert static.neighbors(road, "to_junction") == [f"junction:{v}"]
    minutes = static.attribute(road, "minutes")
    assert minutes.value == rg.graph[u][v]["travel_time"] and minutes.unit == "min"
    assert minutes.provenance == ("data/osm/ratnapura.json",)
    assert static.attribute(f"junction:{u}", "elevation").value == rg.elevation(u)


def test_reference_facts_say_where_they_came_from(static):
    capacity = static.attribute("shelter:ratnapura-shelter-01", "capacity")
    assert (capacity.value, capacity.unit, capacity.source) == (120, "people", "reference")
    assert capacity.provenance == ("data/reference/shelters.json",)
    population = static.attribute(AREA, "population")
    assert population.value == 4200 and "vulnerable_populations.json" in population.provenance[0]


def test_containment_is_a_stated_assumption_not_a_hidden_one(static, rg):
    _, _, road = a_road(rg)
    (fact,) = static.facts(road, "located_in")
    assert fact.object == AREA and fact.confidence == 0.7 and fact.source == "derived"
    assert fact.attrs["basis"] == "nearest area centre" and fact.attrs["distance_m"] >= 0
    for kind in ("Road", "Shelter"):
        assert all(len(static.facts(e, "located_in")) == 1 for e in static.entities(kind))


def test_an_area_off_the_loaded_network_owns_nothing(static):
    colombo = "area:colombo-fort-low"
    assert static.meta(colombo)["anchor_junction"] is None
    assert static.facts(colombo, "located_in", direction="in") == []
    assert static.facts(colombo, "reachable_from", direction="in") == []


def test_gauge_stations_come_from_the_dataset_registry(static):
    (a,) = static.facts("gauge:rathnapura", "monitors")[:1]
    assert {f.object for f in static.facts("gauge:rathnapura", "monitors")} == {
        AREA,
        "area:ratnapura-gn-12",
    }
    assert static.facts("gauge:hanwella", "monitors")[0].object == "area:colombo-fort-low"
    assert a.provenance == ("atlas/datasets/areas.py",)


def test_hazards_know_how_far_the_live_loop_supports_them(static):
    assert static.meta("hazard:flood")["status"] == "live"
    assert static.meta("hazard:earthquake")["status"] == "history_only"
    assert len(static.entities("Hazard")) == 6


def test_the_graph_still_speaks_the_original_worldgraph_protocol(static, rg):
    _, _, road = a_road(rg)
    assert static.has_entity(road) and static.entity_class(road) == "Road"
    assert static.neighbors(road, "located_in") == [AREA]
    assert static.path(road, AREA) == [road, AREA]
    assert not static.has_entity("shelter:ghost") and static.neighbors("shelter:ghost") == []


def test_reachable_from_is_a4s_answer_not_a_second_routing_engine(static, runtime):
    a4 = attach_routing(runtime).evacuation
    anchor = int(static.meta(AREA)["anchor_junction"].split(":")[1])
    facts = static.facts("shelter:ratnapura-shelter-01", "reachable_from")
    fact = next(f for f in facts if f.object == AREA)
    plan = a4.plan_evacuation(anchor, ["ratnapura-shelter-01"], k=1, now=NOW)
    assert fact.attrs["safe"] is (plan.status == "ok")
    assert fact.attrs["minutes"] == pytest.approx(round(plan.recommended.minutes, 2))
    assert fact.source == "derived" and fact.provenance == ("a4-routing@cop-v0",)


def test_serves_is_a_safe_reachable_shelter_within_the_time_limit(rg, definition):
    tight = build_world(sources(rg), definition, WorldConfig(serve_minutes=0.01))
    loose = build_world(sources(rg), definition, WorldConfig(serve_minutes=60))
    assert tight.facts("shelter:ratnapura-shelter-01", "serves") == []
    served = loose.facts("shelter:ratnapura-shelter-01", "serves")
    reachable = [
        f for f in loose.facts("shelter:ratnapura-shelter-01", "reachable_from") if f.attrs["safe"]
    ]
    assert {f.object for f in served} == {f.object for f in reachable} != set()


def test_building_is_deterministic(rg, definition):
    a, b = build_world(sources(rg), definition), build_world(sources(rg), definition)
    assert a.report == b.report
    assert sorted(a.graph.edges(keys=True)) == sorted(b.graph.edges(keys=True))


# ================================================================================= the COP


def test_road_beliefs_and_closures_become_attributes_with_their_provenance(rg, definition):
    u, v, road = a_road(rg)
    moment = NOW - timedelta(minutes=3)
    snap = snapshot(
        belief(f"edge:{u}-{v}", "belief_open", 0.09, confidence=0.82, updated=moment),
        belief(f"edge:{u}-{v}", "open", False, confidence=1.0, provenance=("action:close-1",)),
    )
    world = build_world(sources(rg, snapshot=snap), definition)
    b = world.attribute(road, "belief_open")
    assert (b.value, b.confidence, b.valid_from, b.source) == (0.09, 0.82, moment, "cop")
    assert b.provenance == ("ev-1", f"cop:edge:{u}-{v}/belief_open")
    closed = world.attribute(road, "closed")
    assert closed.value is True and "action:close-1" in closed.provenance


def test_an_open_road_is_not_recorded_as_closed(rg, definition):
    u, v, road = a_road(rg)
    world = build_world(
        sources(rg, snapshot=snapshot(belief(f"edge:{u}-{v}", "open", True))), definition
    )
    assert world.attribute(road, "closed") is None


def test_shelter_occupancy_unit_availability_and_area_state(rg, definition):
    unit = load_resources("ratnapura")[0].id
    snap = snapshot(
        belief("shelter:ratnapura-shelter-01", "occupancy", 34),
        belief(f"resource:{unit}", "available", False),
        belief(AREA.replace("area:", "area:"), "risk_level", "warning"),
        belief(AREA, "water_level", 26.0),
        belief(AREA, "rainfall", 12.5),
    )
    world = build_world(sources(rg, snapshot=snap), definition)
    assert world.attribute("shelter:ratnapura-shelter-01", "occupancy").unit == "people"
    assert world.attribute(f"resource:{unit}", "available").value is False
    assert world.attribute(AREA, "risk_level").value == "warning"
    assert (
        world.attribute(AREA, "water_level").value,
        world.attribute(AREA, "water_level").unit,
    ) == (26.0, "cm")
    assert world.attribute(AREA, "rainfall").unit == "mm"


def test_an_area_hazard_threatens_the_area_and_its_roads_believed_likely_closed(rg, definition):
    roads = [a_road(rg, i) for i in (0, 1, 2)]
    beliefs = [belief(AREA, "hazard", "flood")]
    beliefs += [
        belief(f"edge:{u}-{v}", "belief_open", b) for (u, v, _), b in zip(roads, (0.1, 0.9, 0.05))
    ]
    world = build_world(sources(rg, snapshot=snapshot(*beliefs, version=42)), definition)
    threatened = {f.object: f for f in world.facts("hazard:flood", "threatens")}
    assert set(threatened) == {AREA, roads[0][2], roads[2][2]}  # the 0.9 road is fine
    assert threatened[roads[0][2]].confidence == pytest.approx(0.9)
    assert threatened[roads[2][2]].confidence == pytest.approx(0.95)
    assert "cop-version:42" in threatened[roads[0][2]].provenance


def test_a_belief_about_something_the_world_does_not_know_is_counted_not_swallowed(rg, definition):
    snap = snapshot(
        belief("edge:99999-88888", "belief_open", 0.5),
        belief("shelter:ghost", "occupancy", 3),
        belief(AREA, "mood", "grim"),
        belief("area:ratnapura-demo-river", "hazard", "meteor"),
    )
    world = build_world(sources(rg, snapshot=snap), definition)
    assert world.report["skipped"] == {
        "cop_subject_not_in_world:edge": 1,
        "cop_subject_not_in_world:shelter": 1,
        "cop_attribute_not_modelled:area/mood": 1,
        "cop_hazard_not_in_vocabulary": 1,
    }


def test_road_beliefs_change_which_shelters_are_safely_reachable(rg, definition):
    """Close every road out of the area's anchor junction and its shelters stop being safe."""
    anchor = int(build_world(sources(rg), definition).meta(AREA)["anchor_junction"].split(":")[1])
    beliefs = [
        belief(f"edge:{anchor}-{v}", "belief_open", 0.05) for v in rg.graph.successors(anchor)
    ]
    world = build_world(sources(rg, snapshot=snapshot(*beliefs)), definition)
    facts = [
        f for f in world.facts("shelter:ratnapura-shelter-01", "reachable_from") if f.object == AREA
    ]
    assert facts and facts[0].attrs["safe"] is False
    served = {f.object for f in world.facts("shelter:ratnapura-shelter-01", "serves")}
    assert AREA not in served  # the area that was cut off is no longer served...
    assert "area:ratnapura-gn-12" in served  # ...but a different area with its own roads still is


# ============================================================ incidents, missions and groups


def test_an_incident_ties_together_its_hazard_area_missions_and_groups(rg, definition):
    route = a_route(rg)
    state = incident_state(
        rg, missions={"g1": mission_info("m-1", route, units=[load_resources("ratnapura")[0].id])}
    )
    world = build_world(sources(rg, incidents=[state]), definition)
    incident = "incident:ratnapura-demo-river"
    assert world.neighbors(incident, "about") == ["hazard:flood"]
    assert world.neighbors(AREA, "part_of") == [incident]
    assert world.neighbors("mission:m-1", "part_of") == [incident]
    assert world.neighbors("mission:m-1", "destined_for") == ["shelter:ratnapura-shelter-01"]
    assert world.neighbors("mission:m-1", "evacuates") == ["group:ratnapura-demo-river/g1"]
    assert world.neighbors("group:ratnapura-demo-river/g1", "placed_at") == [
        "shelter:ratnapura-shelter-01"
    ]
    assert world.attribute("group:ratnapura-demo-river/g1", "people").value == 30
    assert len(world.neighbors("mission:m-1", "uses")) == 3


def test_a_missions_roads_are_exactly_the_hops_of_its_route(rg, definition):
    route = a_route(rg, hops=4)
    world = build_world(
        sources(rg, incidents=[incident_state(rg, missions={"g1": mission_info("m-1", route)})]),
        definition,
    )
    expected = sorted(f"road:{a}-{b}" for a, b in zip(route[:-1], route[1:]))
    assert world.neighbors("mission:m-1", "uses") == expected
    assert world.neighbors("group:ratnapura-demo-river/g1", "routed_over") == expected


def test_units_assigned_to_a_mission_depend_on_it(rg, definition):
    unit = load_resources("ratnapura")[0].id
    state = incident_state(
        rg, missions={"g1": mission_info("m-1", a_route(rg), units=[unit, "ghost-unit"])}
    )
    world = build_world(sources(rg, incidents=[state]), definition)
    assert world.neighbors(f"resource:{unit}", "assigned_to") == ["mission:m-1"]
    assert world.report["skipped"]["assigned_unit_unknown"] == 1


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_a_finished_mission_no_longer_depends_on_its_road_shelter_or_units(rg, definition, status):
    unit = load_resources("ratnapura")[0].id
    state = incident_state(
        rg, missions={"g1": mission_info("m-1", a_route(rg), status=status, units=[unit])}
    )
    world = build_world(sources(rg, incidents=[state]), definition)
    assert world.has_entity("mission:m-1")  # it happened, so it is still part of the record
    assert world.neighbors("mission:m-1", "part_of") == ["incident:ratnapura-demo-river"]
    for predicate in ("uses", "destined_for"):
        assert world.neighbors("mission:m-1", predicate) == []
    assert world.neighbors(f"resource:{unit}", "assigned_to") == []
    assert world.neighbors("group:ratnapura-demo-river/g1", "routed_over") == []


def test_the_status_a8_holds_wins_over_the_one_a3_last_saw(rg, definition):
    a8 = Mission(
        id="m-1",
        incident_id="incident:ratnapura-demo-river",
        action_key="k",
        title="t",
        status=MissionStatus.COMPLETED,
        created_at=NOW,
        updated_at=NOW,
    )
    state = incident_state(rg, missions={"g1": mission_info("m-1", a_route(rg), status="created")})
    world = build_world(sources(rg, incidents=[state], missions=[a8]), definition)
    assert world.attribute("mission:m-1", "status").value == "completed"
    assert world.neighbors("mission:m-1", "uses") == []  # completed: no longer depends on the road


def test_standby_crews_and_supply_runs_are_missions_without_an_evacuee_group(rg, definition):
    missions = {
        "g1": mission_info("m-1", a_route(rg)),
        "rescue:ratnapura-demo-river": mission_info("m-2", a_route(rg, 1), shelter=None),
        "resupply:ratnapura-shelter-01": mission_info("m-3", a_route(rg, 2)),
    }
    world = build_world(sources(rg, incidents=[incident_state(rg, missions=missions)]), definition)
    assert world.entities("Group") == ["group:ratnapura-demo-river/g1"]
    assert len(world.entities("Mission")) == 3
    assert world.neighbors("mission:m-3", "destined_for") == ["shelter:ratnapura-shelter-01"]
    assert world.neighbors("mission:m-2", "destined_for") == []


def test_group_ids_repeat_across_incidents_so_they_are_scoped_by_incident(rg, definition):
    one = incident_state(
        rg, incident="incident:a", missions={"g0": mission_info("m-1", a_route(rg))}
    )
    two = incident_state(
        rg, incident="incident:b", missions={"g0": mission_info("m-2", a_route(rg))}
    )
    world = build_world(sources(rg, incidents=[one, two]), definition)
    assert world.entities("Group") == ["group:a/g0", "group:b/g0"]


def test_things_the_world_cannot_place_are_counted(rg, definition):
    state = incident_state(
        rg,
        hazard="meteor",
        missions={
            "g1": mission_info("m-1", [1, 2, 3], shelter="ratnapura-shelter-99"),
            "g2": {"status": "created", "route": []},
        },
    )
    state["area_id"] = "atlantis"
    world = build_world(sources(rg, incidents=[state, {"incident_id": ""}]), definition)
    assert world.report["skipped"] == {
        "incident_area_unknown": 1,
        "mission_shelter_unknown": 1,
        "route_hop_not_a_road": 2,
        "mission_without_id": 1,
        "incident_without_id": 1,
    }
    assert world.neighbors("incident:ratnapura-demo-river", "about") == []  # meteor is not a hazard


def test_a_mission_only_a8_knows_gets_its_route_from_coordinates(rg, definition):
    route = a_route(rg, hops=3)
    a8 = Mission(
        id="m-orphan",
        incident_id="incident:elsewhere",
        action_key="k",
        title="Made through the API",
        team=load_resources("ratnapura")[0].id,
        status=MissionStatus.CREATED,
        destination="ratnapura-shelter-02",
        route=[list(rg.pos(n)) for n in route],
        created_at=NOW,
        updated_at=NOW,
    )
    world = build_world(sources(rg, missions=[a8]), definition)
    assert world.neighbors("mission:m-orphan", "uses") == sorted(
        f"road:{a}-{b}" for a, b in zip(route[:-1], route[1:])
    )
    assert world.neighbors("mission:m-orphan", "destined_for") == ["shelter:ratnapura-shelter-02"]
    assert world.neighbors("mission:m-orphan", "part_of") == []  # its incident is not in the world


def test_a_coordinate_that_is_not_a_junction_is_skipped_not_snapped_to_something_else(
    rg, definition
):
    lat, lon = rg.pos(a_route(rg)[0])
    a8 = Mission(
        id="m-far",
        incident_id="i",
        action_key="k",
        title="t",
        status=MissionStatus.CREATED,
        route=[[lat, lon], [lat + 0.01, lon + 0.01]],
        created_at=NOW,
        updated_at=NOW,
    )
    world = build_world(sources(rg, missions=[a8]), definition)
    assert world.report["skipped"] == {"route_point_not_on_a_junction": 1}
    assert world.neighbors("mission:m-far", "uses") == []


# ================================================================================ the live system


async def test_the_live_world_matches_what_the_agents_decided(live, runtime):
    projector = WorldProjector.from_system(live)
    world = projector.world()
    (state,) = live.orchestration.commander.incidents()
    incident = state["incident_id"]
    assert world.entities("Incident") == [incident]
    assert world.neighbors(incident, "about") == ["hazard:flood"]
    assert len(world.entities("Mission")) == 5 and len(world.entities("Group")) == 3
    for info in state["missions"].values():
        mission = f"mission:{info['mission_id']}"
        route = [n for n in info["route"] if isinstance(n, int)]
        assert world.neighbors(mission, "uses") == sorted(
            f"road:{a}-{b}" for a, b in zip(route[:-1], route[1:])
        )
    a8 = {m.id: m for m in live.control.dispatch.list()}
    for mission in world.entities("Mission"):
        assert world.attribute(mission, "status").value == a8[mission.split(":", 1)[1]].status.value
    assert world.report["skipped"] == {}


async def test_the_live_projection_is_consistent_with_its_own_ontology(live):
    world = WorldProjector.from_system(live).world()
    ontology = YamlOntology.load(graph=world)
    bad = [
        (c.subject, c.predicate, v.code, v.message)
        for c in graph_claims(world)
        for v in ontology.validate(c)
    ]
    assert bad == []


async def test_the_live_world_knows_which_roads_flooded_and_from_what_evidence(live, runtime):
    world = WorldProjector.from_system(live).world()
    flooded = [
        r
        for r in world.entities("Road")
        if (b := world.attribute(r, "belief_open")) and b.value < 0.3
    ]
    assert len(flooded) == 51  # what a 25 cm reading floods in the sensor map
    belief = world.attribute(flooded[0], "belief_open")
    assert belief.source == "cop" and belief.valid_from is not None
    assert any(ref.startswith("cop:edge:") for ref in belief.provenance)
    threatened = {f.object for f in world.facts("hazard:flood", "threatens")}
    assert set(flooded) <= threatened


async def test_the_sensor_node_that_reported_is_a_gauge_watching_the_area(live, runtime):
    world = WorldProjector.from_system(live).world()
    (fact,) = world.facts("gauge:rtp-node-01", "monitors")
    assert fact.object == AREA and fact.valid_from is not None and fact.source == "ledger"
    ledger_ids = {r["payload"]["id"] for r in runtime.ledger.records("event")}
    assert fact.provenance and all(ref.split(":", 1)[1] in ledger_ids for ref in fact.provenance)


async def test_building_the_world_changes_nothing(live, runtime):
    version = runtime.cop.version
    tail = runtime.db.connection.execute("SELECT MAX(seq) FROM ledger").fetchone()[0]
    WorldProjector.from_system(live).world(force=True)
    assert runtime.cop.version == version
    assert runtime.db.connection.execute("SELECT MAX(seq) FROM ledger").fetchone()[0] == tail


async def test_the_world_is_rebuilt_only_when_something_changed(live, runtime, clock):
    projector = WorldProjector.from_system(live)
    first = projector.world()
    assert projector.world() is first  # nothing changed: same graph, no rebuild
    assert projector.world(force=True) is not first

    cached = projector.world()
    await runtime.bus.publish(observation(clock, 35, evidence="ev-new", offset=5))
    assert projector.world() is not cached  # the COP moved


async def test_a_mission_changing_status_rebuilds_the_world(live):
    projector = WorldProjector.from_system(live)
    before = projector.world()
    mission = live.control.dispatch.list()[0]
    live.control.dispatch.acknowledge(mission.id, "team-lead")
    after = projector.world()
    assert after is not before
    assert after.attribute(f"mission:{mission.id}", "status").value == "acknowledged"


async def test_a_new_sensor_node_that_only_the_ledger_knows_about_rebuilds_the_world(
    live, runtime, clock
):
    """A change with no COP, incident or mission movement must still be noticed (the ledger tail)."""
    from atlas.core.events import Event, EventType

    projector = WorldProjector.from_system(live)
    before = projector.world()
    assert not before.has_entity("gauge:rtp-node-02")

    payload = dict(observation(clock, 12, offset=9).payload, source="sensor:rtp-node-02")
    event = Event(type=EventType.OBSERVATION_VALIDATED, source="a1_sentinel", payload=payload)
    runtime.ledger.append(f"event:{event.id}", "event", event.model_dump(mode="json"))

    after = projector.world()
    assert after is not before
    assert after.neighbors("gauge:rtp-node-02", "monitors") == [AREA]
