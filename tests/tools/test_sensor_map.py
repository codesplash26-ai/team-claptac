"""Sensor-to-map mapping: rules, config alignment with the rest of the system, and demo viability."""

import json

import networkx as nx
import pytest
import yaml

from atlas.tools import geo
from atlas.tools.cognitive_map.beliefs import CognitiveMap
from atlas.tools.cognitive_map.cop_bridge import RoutingConfig
from atlas.tools.cognitive_map.routing import alternatives, best_path
from atlas.tools.reference import load_resources, load_shelters
from atlas.tools.sensor_map import (
    AreaMapping,
    Pinned,
    SensorMap,
    edges_in_range,
    inundated_edges,
    road_reports,
    surface_elevation_m,
)
from tests.routing_helpers import DEMO_LEVEL_CM, DEMO_ORIGIN

AREA = "ratnapura-demo-river"


@pytest.fixture(scope="module")
def rg():
    return geo.load_site("ratnapura")


@pytest.fixture(scope="module")
def mapping():
    return SensorMap.from_file().get(AREA)


# --------------------------------------------------- config alignment


def test_committed_config_loads_and_fits_the_road_graph(rg):
    sensor_map = SensorMap.from_file()
    sensor_map.validate({"ratnapura": rg})
    assert sensor_map.get(AREA).site == "ratnapura"


def test_area_id_matches_the_pi_config_and_replay_scenario():
    """A1 rejects readings whose area_id is unknown; these three must agree."""
    pi = yaml.safe_load((geo.ROOT / "hardware/pi/config.example.yaml").read_text())
    assert pi["location"]["area_id"] == AREA
    scenario = json.loads((geo.ROOT / "data/scenarios/flood.json").read_text())
    areas = {
        e["payload"].get("location", {}).get("area_id") or e["payload"].get("area_id")
        for e in scenario["events"]
    }
    assert areas == {AREA}


def test_datum_is_the_ground_elevation_at_the_gauge(rg, mapping):
    node = rg.nearest_node(mapping.gauge.lat, mapping.gauge.lon)
    assert rg.elevation(node) == mapping.datum_elevation_m, (
        "the road graph was rebuilt with different elevations; update datum_elevation_m"
    )


def test_unmapped_area_is_none():
    assert SensorMap.from_file().get("nowhere") is None


# ------------------------------------------------------------- the rule


def test_surface_elevation_is_datum_plus_scaled_level(mapping):
    assert surface_elevation_m(mapping, 0) == mapping.datum_elevation_m
    assert surface_elevation_m(mapping, 40) == pytest.approx(21.0 + 0.20 * 40)


def test_inundation_grows_monotonically_with_the_level(rg, mapping):
    previous = set()
    for level in range(0, 41):
        current = set(inundated_edges(mapping, rg, level))
        assert previous <= current, f"a road reopened as the water rose, at {level} cm"
        previous = current


def test_inundation_is_graduated_across_the_sensor_range(rg, mapping):
    in_range = len(edges_in_range(mapping, rg))
    counts = {c: len(inundated_edges(mapping, rg, c)) for c in (0, 10, 20, 25, 30, 40)}
    assert counts[0] <= 0.1 * in_range  # essentially dry when the container is empty
    assert counts[10] < counts[20] < counts[25] < counts[40]
    assert counts[40] > 0.5 * in_range  # a dramatic but not total flood at the top of the range


def test_only_roads_near_the_gauge_can_flood(rg, mapping):
    near = set(edges_in_range(mapping, rg))
    assert set(inundated_edges(mapping, rg, 1000)) <= near  # even an absurd level


def dry_shelters(rg, mapping, level_cm):
    """Shelters reachable from the depot without crossing flooded roads."""
    dry = nx.DiGraph(rg.graph)
    dry.remove_edges_from(inundated_edges(mapping, rg, level_cm))
    depot = load_resources("ratnapura")[0].node
    return [s for s in load_shelters("ratnapura") if nx.has_path(dry, depot, s.node)]


def atlas_map(rg):
    """The belief map as ATLAS's planning agents build it (no uphill penalty)."""
    slope = RoutingConfig().slope_penalty_per_m
    return CognitiveMap(rg.graph, prior_open=0.9, slope_penalty_per_m=slope)


def dry_route_from(rg, mapping, origin, level_cm):
    """Best route to any shelter avoiding roads that are under water at `level_cm`, or None."""
    cmap = atlas_map(rg)
    for u, v in inundated_edges(mapping, rg, level_cm):
        cmap.update_from_report(u, v, 0.0, 1, 0.9)
    shelters = [s.node for s in load_shelters("ratnapura")]
    found = alternatives(cmap, origin, shelters, 1, avoid_below=0.3)
    return found[0] if found else None


def test_at_the_alarm_level_the_shelter_behind_the_valley_is_lost_but_the_others_stay(rg, mapping):
    assert len(dry_shelters(rg, mapping, 20)) == 4
    reachable = {s.id for s in dry_shelters(rg, mapping, 25)}
    assert reachable == {"ratnapura-shelter-01", "ratnapura-shelter-03", "ratnapura-shelter-04"}
    depot = load_resources("ratnapura")[0].node
    flooded = set(inundated_edges(mapping, rg, 25))
    for node in [depot, *(s.node for s in load_shelters("ratnapura"))]:
        assert not any(node in edge for edge in flooded), f"node {node} is under water"


def test_the_demo_origin_must_reroute_at_30_cm_and_can(rg, mapping):
    """The demo's visible reroute: a valley resident's usual route floods; a dry one exists."""
    shelters = [s.node for s in load_shelters("ratnapura")]
    usual = alternatives(atlas_map(rg), DEMO_ORIGIN, shelters, 1, avoid_below=0.3)[0]
    flooded = set(inundated_edges(mapping, rg, DEMO_LEVEL_CM))
    used = set(zip(usual.path[:-1], usual.path[1:]))
    assert used & flooded, "the usual route is not cut, so nothing would change on screen"
    assert not any(DEMO_ORIGIN in edge for edge in flooded), "the origin itself is under water"
    detour = dry_route_from(rg, mapping, DEMO_ORIGIN, DEMO_LEVEL_CM)
    assert detour is not None and not (set(zip(detour.path[:-1], detour.path[1:])) & flooded)
    assert detour.path[-1] != usual.path[-1]  # and it ends at a different shelter


def test_beyond_the_alarm_band_the_depot_is_cut_off_from_every_shelter(rg, mapping):
    """Documents the calibration: past ~30 cm there is no safe route, which is a human decision."""
    assert dry_shelters(rg, mapping, 20)  # comfortably viable
    assert dry_shelters(rg, mapping, 30) == []


# ------------------------------------------------------------- pinned


def test_pinned_edges_close_from_their_level_regardless_of_elevation(rg, mapping):
    high = max(rg.graph.edges, key=lambda e: min(rg.elevation(e[0]), rg.elevation(e[1])))
    pinned = mapping.model_copy(
        update={"pinned": [Pinned(min_level_cm=15, edges=[geo.edge_id(*high)])]}
    )
    assert high not in inundated_edges(pinned, rg, 14)
    assert high in inundated_edges(pinned, rg, 15)
    assert high not in inundated_edges(mapping, rg, 15)  # only the pin explains it


def test_validate_rejects_pins_that_are_not_in_the_graph(rg, mapping):
    bad = mapping.model_copy(update={"pinned": [Pinned(min_level_cm=1, edges=["1-2"])]})
    with pytest.raises(ValueError, match="pinned edges not in graph"):
        SensorMap([bad]).validate({"ratnapura": rg})
    with pytest.raises(ValueError, match="no road graph loaded"):
        SensorMap([mapping]).validate({})


def test_duplicate_areas_are_rejected(mapping):
    with pytest.raises(ValueError, match="Duplicate"):
        SensorMap([mapping, mapping])


def test_area_mapping_validates_its_numbers():
    base = dict(
        area_id="a", site="s", gauge={"lat": 0, "lon": 0}, datum_elevation_m=0, radius_m=100
    )
    with pytest.raises(ValueError):
        AreaMapping(**base, metres_per_cm=0)
    with pytest.raises(ValueError):
        AreaMapping(**base, metres_per_cm=0.1, dry_weight=1.5)


# ------------------------------------------------------------- reports


def test_reports_mark_wet_roads_closed_and_dry_roads_open(rg, mapping):
    reports = road_reports(mapping, rg, level_cm=30, confidence=0.8)
    wet = set(inundated_edges(mapping, rg, 30))
    assert {(r.u, r.v) for r in reports} == wet | set(edges_in_range(mapping, rg))
    for r in reports:
        if (r.u, r.v) in wet:
            assert (r.reported_open, r.reliability) == (0.0, pytest.approx(0.8))
        else:
            assert (r.reported_open, r.reliability) == (1.0, pytest.approx(0.8 * 0.3))
    assert reports == road_reports(mapping, rg, 30, 0.8)  # deterministic


def test_report_reliability_is_capped(rg, mapping):
    assert max(r.reliability for r in road_reports(mapping, rg, 30, 0.99)) == pytest.approx(0.95)


# ------------------------------------------- with the cognitive map


def apply(cmap, reports, step):
    for r in reports:
        cmap.update_from_report(r.u, r.v, r.reported_open, step, r.reliability)


def test_a_rising_river_makes_the_map_route_around_flooded_roads(rg, mapping):
    cmap = CognitiveMap(rg.graph, prior_open=0.9)
    depot = load_resources("ratnapura")[0].node
    shelters = [s.node for s in load_shelters("ratnapura")]
    before = best_path(cmap, depot, shelters)

    apply(cmap, road_reports(mapping, rg, 25, confidence=0.9), step=1)
    wet = set(inundated_edges(mapping, rg, 25))
    for u, v in wet:
        assert cmap.map[u][v]["belief_open"] < 0.3
    after = best_path(cmap, depot, shelters)

    assert before is not None and after is not None
    assert after != before, "the route did not change"
    assert not any(edge in wet for edge in zip(after[:-1], after[1:])), "route enters flood water"


def test_roads_reopen_gradually_when_the_water_falls(rg, mapping):
    cmap = CognitiveMap(rg.graph, prior_open=0.9)
    apply(cmap, road_reports(mapping, rg, 30, 0.9), step=1)
    wet = inundated_edges(mapping, rg, 30)
    low = cmap.map[wet[0][0]][wet[0][1]]["belief_open"]
    for step in range(2, 8):  # six dry readings in a row
        apply(cmap, road_reports(mapping, rg, 0, 0.9), step=step)
    recovered = cmap.map[wet[0][0]][wet[0][1]]["belief_open"]
    assert recovered > low + 0.3
    assert recovered <= 0.95
