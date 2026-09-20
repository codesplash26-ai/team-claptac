"""Unit tests for the cognitive map's belief updates and routing (hand-computed expectations)."""

import math

import networkx as nx
import pytest

from atlas.tools.cognitive_map.beliefs import CognitiveMap
from atlas.tools.cognitive_map.routing import alternatives, best_path, edge_cost, route_stats


def graph(edges, **attrs):
    g = nx.DiGraph()
    for u, v, t in edges:
        g.add_edge(u, v, travel_time=t, **attrs)
    return g


def diamond():
    #   1 --(5)--> 2 --(5)--> 4      short way via 2
    #   1 --(6)--> 3 --(6)--> 4      slightly longer way via 3
    return graph([(1, 2, 5), (2, 4, 5), (1, 3, 6), (3, 4, 6)])


# ------------------------------------------------------------------- cost


def test_edge_cost_matches_the_proposal_formula():
    cm = CognitiveMap(graph([(1, 2, 5)]), prior_open=0.9, alpha_risk=0.5)
    p, t, a = cm.map[1][2]["belief_open"], 5.0, 0.5
    assert p == pytest.approx(0.9)
    assert edge_cost(cm, 1, 2) == pytest.approx((1 - a) * (t / p) + a * (1 - p) * t)
    assert edge_cost(cm, 1, 2) == pytest.approx(3.02777, abs=1e-4)


def test_lower_belief_costs_more():
    cm = CognitiveMap(graph([(1, 2, 5)]))
    before = edge_cost(cm, 1, 2)
    cm.map[1][2]["belief_open"] = 0.4
    assert edge_cost(cm, 1, 2) > before


def test_uphill_edge_is_penalised_ten_percent_per_metre():
    flat = CognitiveMap(graph([(1, 2, 5)]))
    hill = CognitiveMap(graph([(1, 2, 5)], elev_u=0.0, elev_v=2.0))
    assert edge_cost(hill, 1, 2) == pytest.approx(edge_cost(flat, 1, 2) * 1.2)


def test_downhill_edge_is_not_rewarded():
    flat = CognitiveMap(graph([(1, 2, 5)]))
    down = CognitiveMap(graph([(1, 2, 5)], elev_u=5.0, elev_v=0.0))
    assert edge_cost(down, 1, 2) == pytest.approx(edge_cost(flat, 1, 2))


def test_missing_or_hard_closed_edge_costs_infinity():
    cm = CognitiveMap(graph([(1, 2, 5)]))
    assert edge_cost(cm, 2, 1) == math.inf
    cm.set_closed(1, 2)
    assert edge_cost(cm, 1, 2) == math.inf


# ---------------------------------------------------------------- updates


def test_traversal_success_and_failure_are_bayesian():
    cm = CognitiveMap(graph([(1, 2, 5), (2, 3, 5)]), prior_open=0.9)
    cm.update_from_traversal(1, 2, success=False, step=3)
    assert cm.map[1][2]["belief_open"] == pytest.approx(0.05 * 0.9 / (0.05 * 0.9 + 0.95 * 0.1))
    assert cm.map[1][2]["last_updated"] == 3
    cm.update_from_traversal(2, 3, success=True, step=4, actual_travel_time=9.0)
    assert cm.map[2][3]["belief_open"] == pytest.approx(0.95)  # clamped at MAX_BELIEF
    assert cm.map[2][3]["est_travel"] == pytest.approx(0.7 * 5 + 0.3 * 9)


def test_report_is_a_reliability_weighted_blend():
    cm = CognitiveMap(graph([(1, 2, 5)]), prior_open=0.9)
    cm.update_from_report(1, 2, reported_open=0.0, step=2, report_reliability=0.8)
    assert cm.map[1][2]["belief_open"] == pytest.approx(0.2 * 0.9)
    assert cm.map[1][2]["uncertainty"] == pytest.approx(1 - abs(0.18 - 0.5) * 2)


def test_beliefs_are_clamped():
    cm = CognitiveMap(graph([(1, 2, 5)]), prior_open=0.9)
    cm.update_from_report(1, 2, reported_open=0.0, step=1, report_reliability=1.0)
    assert cm.map[1][2]["belief_open"] == pytest.approx(0.05)


def test_decay_pulls_toward_one_half_with_age():
    cm = CognitiveMap(graph([(1, 2, 5)]), prior_open=0.9)
    cm.apply_time_decay(current_step=10, decay_rate=0.02)
    assert cm.map[1][2]["belief_open"] == pytest.approx(0.5 + 0.4 * math.exp(-0.2))


def test_updates_to_unknown_edges_are_ignored():
    cm = CognitiveMap(graph([(1, 2, 5)]))
    cm.update_from_traversal(9, 9, True, 1)
    cm.update_from_report(9, 9, 0.0, 1)
    assert list(cm.map.edges) == [(1, 2)]


# ---------------------------------------------------------------- routing


def test_best_path_prefers_the_cheaper_route():
    assert best_path(CognitiveMap(diamond()), 1, [4]) == [1, 2, 4]


def test_best_path_reroutes_when_belief_collapses():
    cm = CognitiveMap(diamond())
    cm.update_from_report(2, 4, reported_open=0.0, step=1, report_reliability=1.0)
    assert best_path(cm, 1, [4]) == [1, 3, 4]


def test_best_path_never_uses_a_hard_closed_edge_even_if_it_is_the_only_way():
    cm = CognitiveMap(graph([(1, 2, 5), (2, 3, 5)]))
    cm.set_closed(2, 3)
    assert best_path(cm, 1, [3]) is None


def test_best_path_handles_trivial_and_unknown_sources():
    cm = CognitiveMap(diamond())
    assert best_path(cm, 4, [4]) == [4]
    assert best_path(cm, 99, [4]) is None


def test_best_path_picks_nearest_of_several_targets():
    cm = CognitiveMap(graph([(1, 2, 5), (1, 3, 20)]))
    assert best_path(cm, 1, [3, 2]) == [1, 2]


def test_alternatives_are_distinct_ranked_and_deterministic():
    cm = CognitiveMap(diamond())
    routes = alternatives(cm, 1, [4], k=3)
    assert [r.path for r in routes] == [(1, 2, 4), (1, 3, 4)]
    assert routes[0].cost <= routes[1].cost
    assert routes == alternatives(cm, 1, [4], k=3)
    assert len(alternatives(cm, 1, [4], k=1)) == 1


def test_alternatives_skip_closed_edges_and_report_no_route():
    cm = CognitiveMap(diamond())
    cm.set_closed(2, 4)
    assert [r.path for r in alternatives(cm, 1, [4])] == [(1, 3, 4)]
    cm.set_closed(3, 4)
    assert alternatives(cm, 1, [4]) == []


def test_alternatives_source_is_target():
    assert [r.path for r in alternatives(CognitiveMap(diamond()), 4, [4])] == [(4,)]


def test_route_stats_summarise_risk():
    cm = CognitiveMap(diamond(), prior_open=0.9)
    cm.update_from_report(2, 4, reported_open=0.5, step=1, report_reliability=0.5)  # -> 0.7
    stats = route_stats(cm, [1, 2, 4])
    assert stats.success_probability == pytest.approx(0.9 * 0.7)
    assert stats.weakest_edge == (2, 4)
    assert stats.weakest_belief == pytest.approx(0.7)
    assert stats.minutes == pytest.approx(10.0)
    assert 0 < stats.mean_uncertainty < 1


def test_route_stats_of_a_hard_closed_route_has_zero_success():
    cm = CognitiveMap(diamond())
    cm.set_closed(1, 2)
    assert route_stats(cm, [1, 2, 4]).success_probability == 0.0


# ------------------------------------------------- persistence bookkeeping


def test_only_materially_changed_edges_are_reported_for_writing():
    cm = CognitiveMap(diamond())
    cm.mark_persisted()
    assert cm.changed_edges() == []
    cm.update_from_report(1, 2, reported_open=0.0, step=1, report_reliability=0.5)
    cm.update_from_report(1, 3, reported_open=0.9, step=1, report_reliability=0.5)  # ~no change
    assert cm.changed_edges(epsilon=0.01) == [(1, 2)]


def test_set_belief_loads_stored_state_without_marking_it_changed():
    cm = CognitiveMap(diamond())
    cm.set_belief(1, 2, 0.3, last_updated=7)
    cm.mark_persisted()
    assert cm.map[1][2]["belief_open"] == pytest.approx(0.3)
    assert cm.map[1][2]["last_updated"] == 7
    assert cm.changed_edges() == []


# -------------------------------------------- alternatives: avoiding likely-closed roads


def long_detour():
    #   1 --(5)--> 2 --(5)--> 4       short way via 2
    #   1 --(30)-> 3 --(30)-> 4       very long way via 3
    return graph([(1, 2, 5), (2, 4, 5), (1, 3, 30), (3, 4, 30)])


def doubtful_short_road():
    cm = CognitiveMap(long_detour(), prior_open=0.9)
    cm.update_from_report(2, 4, reported_open=0.0, step=1, report_reliability=0.75)  # 0.9 -> 0.225
    return cm


def test_cost_alone_accepts_a_doubtful_road_when_the_detour_is_long():
    """The engine's risk/time trade-off, as evaluated: a 22%-open road still beats a 60-minute detour."""
    cm = doubtful_short_road()
    assert best_path(cm, 1, [4]) == [1, 2, 4]
    assert alternatives(cm, 1, [4], k=2)[0].path == (1, 2, 4)


def test_avoid_below_excludes_the_doubtful_road_and_takes_the_detour():
    routes = alternatives(doubtful_short_road(), 1, [4], k=2, avoid_below=0.3)
    assert [r.path for r in routes] == [(1, 3, 4)]
    assert all(r.weakest_belief >= 0.3 for r in routes)


def test_avoid_below_can_leave_no_route_at_all():
    cm = CognitiveMap(graph([(1, 2, 5)]), prior_open=0.9)
    cm.update_from_report(1, 2, reported_open=0.0, step=1, report_reliability=1.0)
    assert alternatives(cm, 1, [2], avoid_below=0.3) == []
    assert [r.path for r in alternatives(cm, 1, [2])] == [(1, 2)]  # still reachable, just unsafe


def test_avoid_below_none_leaves_alternatives_unchanged():
    cm = CognitiveMap(diamond(), prior_open=0.9)
    assert alternatives(cm, 1, [4], k=3, avoid_below=None) == alternatives(cm, 1, [4], k=3)
