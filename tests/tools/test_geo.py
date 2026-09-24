"""Road-graph geometry, cache I/O and elevation handling. Offline: no test touches the network."""

import logging

import networkx as nx
import pytest

from atlas.tools import geo
from atlas.tools.geo import RoadGraph, edge_id, haversine_m, load_site, parse_edge_id


def tiny() -> RoadGraph:
    """Three nodes in a line along a meridian, ~1.1 km apart, rising 0 -> 10 -> 20 m."""
    g = nx.DiGraph()
    for n, (lat, elev) in {1: (6.00, 0.0), 2: (6.01, 10.0), 3: (6.02, 20.0)}.items():
        g.add_node(n, lat=lat, lon=80.0, elevation=elev, is_shelter=False)
    for u, v in [(1, 2), (2, 1), (2, 3), (3, 2)]:
        g.add_edge(
            u,
            v,
            travel_time=2.0,
            length_m=1112.0,
            highway="residential",
            elev_u=g.nodes[u]["elevation"],
            elev_v=g.nodes[v]["elevation"],
        )
    return RoadGraph("tiny", g, (6.01, 80.0), 2000, "test")


# --------------------------------------------------------------- geometry


def test_haversine_known_distances():
    assert haversine_m(6.0, 80.0, 6.0, 80.0) == pytest.approx(0.0, abs=1e-6)
    assert haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(
        111_195, rel=1e-3
    )  # 1 degree of latitude
    assert haversine_m(6.0, 80.0, 7.0, 81.0) == pytest.approx(haversine_m(7.0, 81.0, 6.0, 80.0))


def test_edge_ids_round_trip():
    assert edge_id(12, 34) == "12-34"
    assert parse_edge_id("12-34") == (12, 34)
    for bad in ("1234", "a-b", ""):
        with pytest.raises(ValueError):
            parse_edge_id(bad)


def test_nearest_node_and_radius_queries():
    rg = tiny()
    assert rg.nearest_node(6.011, 80.0) == 2
    assert rg.nodes_within(6.01, 80.0, 500) == [2]
    assert rg.nodes_within(6.01, 80.0, 1500) == [1, 2, 3]
    # 6.005 is the midpoint of (1,2)/(2,1); the (2,3) midpoint is ~1.1 km away.
    assert rg.edges_within(6.005, 80.0, 100) == [(1, 2), (2, 1)]
    assert rg.edges_within(6.005, 80.0, 100) == rg.edges_within(6.005, 80.0, 100)


def test_edge_index_lists_every_directed_edge():
    assert tiny().edge_index() == {
        "1-2": (1, 2),
        "2-1": (2, 1),
        "2-3": (2, 3),
        "3-2": (3, 2),
    }


# ----------------------------------------------------------------- GeoJSON


def test_geojson_uses_lon_lat_order_and_carries_beliefs():
    rg = tiny()
    line = rg.route_geojson([1, 2, 3], {"minutes": 4})
    assert line["geometry"]["type"] == "LineString"
    assert line["geometry"]["coordinates"][0] == [80.0, 6.0]  # [lon, lat]
    assert line["properties"] == {"kind": "route", "minutes": 4}

    fc = rg.edges_geojson({(1, 2): 0.31234567})
    assert [f["properties"] for f in fc["features"]] == [{"edge_id": "1-2", "belief_open": 0.3123}]
    assert len(rg.edges_geojson()["features"]) == 4  # no belief filter -> all edges


# --------------------------------------------------------------- cache I/O


def test_cache_round_trip_preserves_the_graph(tmp_path):
    rg = tiny()
    geo.save_graph(rg, tmp_path)
    back = load_site("tiny", tmp_path)
    assert back.name == "tiny"
    assert set(back.graph.edges) == set(rg.graph.edges)
    assert back.graph[1][2]["elev_v"] == 10.0
    assert back.graph[1][2]["travel_time"] == 2.0
    assert back.elevation_source == "test"
    assert back.center == (6.01, 80.0)


def test_missing_cache_explains_how_to_build_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="python -m atlas.tools.geo build ghost"):
        load_site("ghost", tmp_path)


def test_unknown_cache_version_is_rejected():
    data = tiny().to_dict()
    data["version"] = 999
    with pytest.raises(ValueError, match="version"):
        RoadGraph.from_dict(data)


def test_synthetic_elevation_is_flagged_loudly(tmp_path, caplog):
    rg = tiny()
    rg.elevation_source = "synthetic"
    geo.save_graph(rg, tmp_path)
    with caplog.at_level(logging.WARNING):
        load_site("tiny", tmp_path)
    assert "SYNTHETIC elevation" in caplog.text


# --------------------------------------------------------------- elevation


class FakeResponse:
    status_code = 200

    def __init__(self, results):
        self._results = results

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": self._results}


def test_opentopodata_requests_are_batched_at_100(monkeypatch):
    monkeypatch.setattr(geo.time, "sleep", lambda s: None)
    calls = []

    class Session:
        def get(self, url, params, timeout):
            n = len(params["locations"].split("|"))
            calls.append(n)
            return FakeResponse([{"elevation": float(i)} for i in range(n)])

    points = [(6.0 + i * 1e-4, 80.0) for i in range(250)]
    assert len(geo._fetch_opentopodata(points, Session())) == 250
    assert calls == [100, 100, 50]


def test_missing_elevation_values_are_an_error_not_a_zero(monkeypatch):
    monkeypatch.setattr(geo.time, "sleep", lambda s: None)

    class Session:
        def get(self, url, params, timeout):
            return FakeResponse([{"elevation": None}])

    with pytest.raises(ValueError, match="missing"):
        geo._fetch_opentopodata([(6.0, 80.0)], Session())


def test_elevation_falls_through_providers_then_flags_synthetic(monkeypatch):
    def boom(points, session):
        raise RuntimeError("down")

    monkeypatch.setattr(geo, "_fetch_opentopodata", boom)
    monkeypatch.setattr(geo, "_fetch_open_elevation", lambda points, session: [7.0] * len(points))
    values, source = geo.fetch_elevations([(6.0, 80.0), (6.0, 80.1)])
    assert (values, source) == ([7.0, 7.0], "open-elevation")

    monkeypatch.setattr(geo, "_fetch_open_elevation", boom)
    values, source = geo.fetch_elevations([(6.0, 80.0), (6.0, 80.5), (6.0, 81.0)])
    assert source == "synthetic"
    assert values == [0.0, 5.0, 10.0]  # west -> east slope, clearly labelled as fake


def test_shelter_suggestions_are_high_ground_spaced_and_deterministic():
    rg = tiny()
    assert geo.suggest_shelter_nodes(rg, 2, min_separation_m=500) == [3, 2]
    assert geo.suggest_shelter_nodes(rg, 2, min_separation_m=5000) == [3]  # too close: only one
    assert geo.suggest_shelter_nodes(rg, 2, 500) == geo.suggest_shelter_nodes(rg, 2, 500)


# ------------------------------------------- the committed real-world caches


@pytest.mark.parametrize("site", sorted(geo.SITES))
def test_committed_site_graph_is_sound(site):
    rg = load_site(site)
    g = rg.graph
    assert rg.elevation_source != "synthetic", "committed graphs must use real elevation"
    assert g.number_of_nodes() > 100
    assert nx.is_strongly_connected(g), "every node must be able to reach every other"
    assert min(d["travel_time"] for *_, d in g.edges(data=True)) > 0
    for u, v, d in g.edges(data=True):
        assert d["elev_u"] == g.nodes[u]["elevation"]
        assert d["elev_v"] == g.nodes[v]["elevation"]
    lat, lon = rg.center
    far = max(haversine_m(lat, lon, *rg.pos(n)) for n in g)
    assert far < rg.radius_m * 1.5


def test_ratnapura_is_hilly_and_contains_the_demo_river_sensor():
    rg = load_site("ratnapura")
    elevations = [rg.elevation(n) for n in rg.graph]
    assert max(elevations) - min(elevations) > 30
    # The demo sensor (data/scenarios/flood.json) sits at the town centre, inside the graph.
    assert haversine_m(6.6828, 80.3992, *rg.pos(rg.nearest_node(6.6828, 80.3992))) < 200
