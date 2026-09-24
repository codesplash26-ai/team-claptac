"""Belief-weighted shortest paths: cost = (1-a)(t/p) + a(1-p)t.

Ported from EvacuationAgent-using-Cognitivity/simulation_map/cognitive_map.py. `edge_cost` and
`best_path` reproduce the original behaviour exactly (including its slope penalty), because the
proposal's published results depend on them. `alternatives` and `route_stats` are ATLAS additions
for A4: ranked alternative routes with per-route risk and uncertainty.

Nothing here touches the COP, the bus or the network, so it is safe to call from a bus handler.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import networkx as nx

# 10% travel-time penalty per metre of positive elevation gain: the original engine's assumption,
# and the default here so the engine reproduces its published behaviour. ATLAS's planning agents set
# it to 0 (see RoutingConfig): with it on, evacuees are steered downhill, away from hill shelters and
# into the valleys that flood, and the belief agent fails to evacuate 39% of the time on the real
# Ratnapura network (eval/README in docs/routing-integration.md).
SLOPE_PENALTY_PER_M = 0.10


def slope_factor(edge: dict, per_m: float = SLOPE_PENALTY_PER_M) -> float:
    slope = edge.get("elev_v", 0.0) - edge.get("elev_u", 0.0)
    return 1.0 + max(0.0, slope * per_m)


def edge_cost(cmap, u, v) -> float:
    """Belief-weighted cost: balances expected travel time against risk of the road being shut."""
    if not cmap.map.has_edge(u, v):
        return math.inf
    d = cmap.map[u][v]
    if d.get("closed"):  # hard closure reported by A8 (close_major_route): never routable
        return math.inf
    p = d["belief_open"]
    t = d["est_travel"] * slope_factor(d, cmap.slope_penalty_per_m)
    expected_time = t / max(p, 0.01)
    risk_penalty = cmap.alpha * (1.0 - p) * t
    return (1 - cmap.alpha) * expected_time + risk_penalty


def best_path(cmap, source, targets) -> list | None:
    """Belief-weighted Dijkstra from `source` to the nearest of `targets`, or None."""
    if source not in cmap.map:
        return None
    goals = set(targets)
    dist = {n: math.inf for n in cmap.map.nodes()}
    prev = {n: None for n in cmap.map.nodes()}
    dist[source] = 0.0
    heap = [(0.0, source)]
    visited = set()

    while heap:
        d, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)

        if u in goals:
            path, cur = [], u
            while cur is not None:
                path.append(cur)
                cur = prev[cur]
            return path[::-1]

        for _, v in cmap.map.out_edges(u):
            nd = d + edge_cost(cmap, u, v)
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    return None


@dataclass(frozen=True)
class RouteStats:
    path: tuple
    cost: float
    minutes: float  # expected free-flow time incl. slope penalty
    success_probability: float  # product of edge beliefs; 0 if any edge is hard-closed
    weakest_edge: tuple | None
    weakest_belief: float | None
    mean_uncertainty: float


def route_stats(cmap, path) -> RouteStats:
    """Risk summary for a path. Belief products assume independent edges (a simplification)."""
    edges = list(zip(path[:-1], path[1:]))
    if not edges:
        return RouteStats(tuple(path), 0.0, 0.0, 1.0, None, None, 0.0)
    cost = minutes = 0.0
    success = 1.0
    uncertainties = []
    weakest, weakest_b = None, None
    for u, v in edges:
        d = cmap.map[u][v]
        cost += edge_cost(cmap, u, v)
        minutes += d["est_travel"] * slope_factor(d, cmap.slope_penalty_per_m)
        b = 0.0 if d.get("closed") else d["belief_open"]
        success *= b
        uncertainties.append(d["uncertainty"])
        if weakest_b is None or b < weakest_b:
            weakest, weakest_b = (u, v), b
    return RouteStats(
        tuple(path),
        cost,
        minutes,
        success,
        weakest,
        weakest_b,
        sum(uncertainties) / len(uncertainties),
    )


def route_cost(stats: RouteStats, minutes_weight: int, weakness_weight: int) -> int:
    """Integer per-person/per-unit cost of a route for the optimisers (lower is better).

    Travel minutes plus a penalty for how doubtful the weakest road is (belief 1.0 -> no penalty).
    Integer because CP-SAT needs it; rounded once here so every caller agrees.
    """
    weakest = stats.weakest_belief if stats.weakest_belief is not None else 1.0
    return round(minutes_weight * stats.minutes + weakness_weight * (1.0 - weakest))


def alternatives(
    cmap, source, targets, k: int = 3, *, avoid_below: float | None = None
) -> list[RouteStats]:
    """Up to `k` distinct loop-free routes to any target, cheapest first (Yen's algorithm).

    Deterministic: ties break on the path itself. Hard-closed edges are always excluded. With
    `avoid_below`, edges whose belief of being open is below that value are excluded too, so a
    returned route never crosses a road believed likely closed. (`best_path` and the simulation do
    not use this: the cost formula alone trades risk against detour length, as originally evaluated.)
    """

    def usable(u, v):
        d = cmap.map[u][v]
        return not d.get("closed") and (avoid_below is None or d["belief_open"] >= avoid_below)

    view = nx.subgraph_view(cmap.map, filter_edge=usable)

    def weight(u, v, _d):
        return edge_cost(cmap, u, v)

    found: dict[tuple, float] = {}
    for target in dict.fromkeys(targets):
        if target == source:
            found[(source,)] = 0.0
            continue
        if source not in view or target not in view:
            continue
        try:
            for i, path in enumerate(nx.shortest_simple_paths(view, source, target, weight=weight)):
                if i >= k:
                    break
                found[tuple(path)] = sum(weight(a, b, None) for a, b in zip(path[:-1], path[1:]))
        except nx.NetworkXNoPath:
            continue
    ranked = sorted(found.items(), key=lambda item: (item[1], item[0]))[:k]
    return [route_stats(cmap, list(path)) for path, _ in ranked]
