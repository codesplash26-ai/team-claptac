"""Evacuation episodes on the real networks: the flood mechanics, determinism, and the penalty finding."""

from dataclasses import asdict

import pytest

from atlas.tools import geo
from atlas.tools.reference import load_shelters
from eval import networks
from eval.episodes import aggregate
from eval.networks import (
    FLOOD_LEVELS_M,
    flood_hook,
    lowest_ground_m,
    road_network_from_graph,
    run_network_episode,
    run_network_experiment,
    start_candidates,
)


@pytest.fixture(scope="module")
def rg():
    return geo.load_site("ratnapura")


@pytest.fixture(scope="module")
def shelter_nodes():
    return [s.node for s in load_shelters("ratnapura")]


def make(rg, shelter_nodes, seed=1):
    return road_network_from_graph(rg, shelter_nodes, seed)


# ---------------------------------------------------------------- the network


def test_the_simulator_network_mirrors_the_road_graph(rg, shelter_nodes):
    rn = make(rg, shelter_nodes)
    assert rn.G.number_of_nodes() == rg.graph.number_of_nodes()
    assert rn.G.number_of_edges() == rg.graph.number_of_edges()  # directed, not doubled
    assert sorted(rn.shelters()) == sorted(shelter_nodes)
    u, v, real = next(iter(rg.graph.edges(data=True)))
    sim = rn.G[u][v]
    assert (sim["travel_time"], sim["elev_u"], sim["elev_v"]) == (
        real["travel_time"],
        real["elev_u"],
        real["elev_v"],
    )
    assert sim["true_open"] == 1.0 and rn.G.nodes[u]["elevation"] == rg.elevation(u)


def test_starts_are_dry_ground_that_is_not_a_shelter(rg, shelter_nodes):
    rn = make(rg, shelter_nodes)
    pool = start_candidates(rn)
    final_level = lowest_ground_m(rn) + max(FLOOD_LEVELS_M.values())
    assert pool == sorted(pool) and len(pool) > 20
    assert not set(pool) & set(shelter_nodes)
    assert all(rn.G.nodes[n]["elevation"] > final_level for n in pool)


# ---------------------------------------------------------------------- flood


def run_hook(rn, step):
    reported = []
    flood_hook()(rn, step, lambda u, v: reported.append((u, v)))
    return reported


def test_the_flood_does_nothing_except_at_its_two_stages(rg, shelter_nodes):
    rn = make(rg, shelter_nodes)
    for step in (1, 4, 6, 11, 13, 60):
        assert run_hook(rn, step) == []
    assert all(d["true_open"] == 1.0 for *_, d in rn.G.edges(data=True))


def test_the_flood_rises_in_two_stages_and_reports_what_it_changes(rg, shelter_nodes):
    rn = make(rg, shelter_nodes)
    low = lowest_ground_m(rn)

    first = run_hook(rn, 5)
    closed_first = {e for e in rn.G.edges if rn.G.edges[e]["true_open"] == 0.0}
    assert closed_first and set(first) >= closed_first  # every closed road is reported
    for u, v in closed_first:
        assert min(rn.G[u][v]["elev_u"], rn.G[u][v]["elev_v"]) <= low + FLOOD_LEVELS_M[5]

    second = run_hook(rn, 12)
    closed_second = {e for e in rn.G.edges if rn.G.edges[e]["true_open"] == 0.0}
    assert closed_first < closed_second  # the water rose: strictly more roads are shut
    assert set(second) >= closed_second - closed_first
    for u, v in closed_second:
        assert min(rn.G[u][v]["elev_u"], rn.G[u][v]["elev_v"]) <= low + FLOOD_LEVELS_M[12]


def test_roads_just_above_the_flood_line_are_slowed_not_closed(rg, shelter_nodes):
    rn = make(rg, shelter_nodes)
    before = {e: rn.G.edges[e]["travel_time"] for e in rn.G.edges}
    run_hook(rn, 5)
    level = lowest_ground_m(rn) + FLOOD_LEVELS_M[5]
    slowed = [e for e in rn.G.edges if rn.G.edges[e]["true_open"] == 0.5]
    assert slowed
    for e in slowed:
        d = rn.G.edges[e]
        assert level < min(d["elev_u"], d["elev_v"]) <= level + networks.PARTIAL_BAND_M
        assert d["travel_time"] == pytest.approx(before[e] * networks.PARTIAL_SLOWDOWN)
        assert d["condition"] == "partial"


# ------------------------------------------------------------------- episodes


def test_a_calm_day_everyone_reaches_a_shelter(rg, shelter_nodes):
    episode = run_network_episode(rg, "A", 0, 1000, shelter_nodes=shelter_nodes, n_agents=4)
    assert len(episode.agents) == 12 and all(a.reached for a in episode.agents)
    assert all(a.failed_routes == 0 for a in episode.agents)


def test_episodes_are_reproducible_and_seeds_matter(rg, shelter_nodes):
    a = run_network_episode(rg, "B", 0, 1000, shelter_nodes=shelter_nodes, n_agents=3)
    b = run_network_episode(rg, "B", 0, 1000, shelter_nodes=shelter_nodes, n_agents=3)
    other = run_network_episode(rg, "B", 0, 1001, shelter_nodes=shelter_nodes, n_agents=3)
    assert asdict(a) == asdict(b)
    assert asdict(a) != asdict(other)


def test_shelters_default_to_the_reference_data(rg, shelter_nodes):
    explicit = run_network_episode(rg, "A", 0, 1000, shelter_nodes=shelter_nodes, n_agents=2)
    implicit = run_network_episode(rg, "A", 0, 1000, n_agents=2)
    assert asdict(explicit) == asdict(implicit)


def test_an_unknown_scenario_is_rejected(rg):
    with pytest.raises(ValueError, match="Unknown scenario"):
        run_network_episode(rg, "Z", 0, 1000)


def test_high_misinformation_scenario_uses_the_higher_error_rate(rg, shelter_nodes, monkeypatch):
    seen = []
    real = networks.simulate

    def spy(*args, **kwargs):
        seen.append(kwargs["error_prob"])
        return real(*args, **kwargs)

    monkeypatch.setattr(networks, "simulate", spy)
    for scenario in "ABC":
        run_network_episode(rg, scenario, 0, 1000, shelter_nodes=shelter_nodes, n_agents=1)
    assert seen == [networks.REPORT_ERROR_PROB, networks.REPORT_ERROR_PROB, 0.40]


# ------------------------------------------------- the uphill-penalty finding


def test_the_original_uphill_penalty_strands_evacuees_and_atlas_default_does_not():
    """The finding behind ATLAS's routing default, kept as a regression test.

    With the engine's original 10%-per-metre uphill penalty the belief agent is steered into the
    Ratnapura valley and traps itself when it floods; with the penalty off it matches the baselines.
    Four seeded runs are enough: the gap is 0.60 against 1.00.
    """
    original = run_network_experiment("ratnapura", "B", 4, slope_penalty_per_m=0.10)
    atlas = run_network_experiment("ratnapura", "B", 4, slope_penalty_per_m=0.0)
    assert aggregate(original, "Cognitive")["success_rate"] <= 0.8
    assert aggregate(original, "Cognitive")["avg_failed_routes"] > 5
    assert aggregate(atlas, "Cognitive")["success_rate"] >= 0.95
    assert aggregate(atlas, "Cognitive")["avg_failed_routes"] < 1.5
    # The baselines do not use the penalty and stay fine either way. (They are not bit-identical
    # between the two runs: all three agent types share one world and one random stream, so the
    # belief agent's different path shifts the coin flips the baselines see.)
    for kind in ("Omniscient", "Reactive"):
        for experiment in (original, atlas):
            assert aggregate(experiment, kind)["success_rate"] >= 0.95


def test_atlas_routing_default_is_the_setting_the_evaluation_recommends():
    from atlas.tools.cognitive_map.cop_bridge import RoutingConfig
    from eval.run_abc import ATLAS_SLOPE_PENALTY

    assert RoutingConfig().slope_penalty_per_m == ATLAS_SLOPE_PENALTY == 0.0
