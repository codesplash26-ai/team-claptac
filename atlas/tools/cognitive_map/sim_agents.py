"""Simulated evacuees used to evaluate the cognitive map (not the ATLAS agents A1-A9).

Ported from EvacuationAgent-using-Cognitivity/simulation_map/agent.py:

* CognitiveAgent   - belief cognitive map + proactive replanning (the A4 core being evaluated)
* OmniscientAgent  - perfect-information baseline
* ReactiveDynAgent - naive baseline: applies reports as ground truth
"""

from __future__ import annotations

import heapq

import networkx as nx


def _dijkstra_perfect(G: nx.DiGraph, source, targets: list) -> list | None:
    dist = {n: float("inf") for n in G.nodes()}
    prev = {n: None for n in G.nodes()}
    dist[source] = 0.0
    heap = [(0.0, source)]
    visited = set()

    while heap:
        d, u = heapq.heappop(heap)
        if u in visited:
            continue
        visited.add(u)

        if u in targets:
            path, cur = [], u
            while cur is not None:
                path.append(cur)
                cur = prev[cur]
            return path[::-1]

        for _, v, data in G.out_edges(u, data=True):
            if data["true_open"] < 0.5:
                continue
            nd = d + data["travel_time"]
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd, v))

    return None


class _Evacuee:
    """State shared by all simulated agents."""

    def __init__(self, agent_id, start_node, road_network):
        self.id = agent_id
        self.current_node = start_node
        self.road_network = road_network
        self.path: list = []
        self.total_travel_time = 0.0
        self.failed_routes = 0
        self.risky_edges_hit = 0
        self.steps_taken = 0
        self.reached_shelter = False
        self.last_traversed_edge = None
        self.traversed_path = [start_node]

    def _advance(self, next_node, targets) -> str:
        """Try the next road; update counters. Shared by all three agent types."""
        success, travel_time = self.road_network.traverse(self.current_node, next_node)
        self._last_outcome = (success, travel_time)
        self.steps_taken += 1

        if success:
            self.last_traversed_edge = (self.current_node, next_node)
            self.total_travel_time += travel_time
            self.current_node = next_node
            self.traversed_path.append(next_node)
            self.path.pop(0)
            if self.current_node in targets:
                self.reached_shelter = True
                return "arrived"
            return "moving"

        self.failed_routes += 1
        self.path = []
        self.last_traversed_edge = None
        return "blocked"


class CognitiveAgent(_Evacuee):
    def __init__(
        self,
        agent_id,
        start_node,
        road_network,
        cognitive_map,
        report_reliability: float = 0.8,
    ):
        super().__init__(agent_id, start_node, road_network)
        self.cmap = cognitive_map
        self.report_reliability = report_reliability
        self.needs_replan = False

    def plan(self, targets: list):
        path = self.cmap.best_path(self.current_node, targets)
        self.path = path[1:] if path else []

    def receive_reports(self, reports: list, current_step: int):
        if reports:
            self.needs_replan = True
        for r in reports:
            self.cmap.update_from_report(
                r["u"],
                r["v"],
                r["reported_open"],
                current_step,
                self.report_reliability,
            )

    def step(self, targets: list, current_step: int) -> str:
        if self.reached_shelter:
            return "arrived"

        self.cmap.apply_time_decay(current_step)

        if self.needs_replan:
            self.plan(targets)
            self.needs_replan = False
            if not self.path:
                return "stuck"

        if not self.path:
            self.plan(targets)
            if not self.path:
                return "stuck"

        next_node = self.path[0]
        selected_edge_belief = None

        # Replan proactively if next edge belief is too risky
        if self.cmap.map.has_edge(self.current_node, next_node):
            b = self.cmap.map[self.current_node][next_node]["belief_open"]
            if b < 0.3:
                self.plan(targets)
                if not self.path:
                    return "stuck"
                next_node = self.path[0]
            if self.cmap.map.has_edge(self.current_node, next_node):
                selected_edge_belief = self.cmap.map[self.current_node][next_node]["belief_open"]

        if selected_edge_belief is not None and selected_edge_belief < 0.5:
            # Metric: chosen edge had low believed openness.
            self.risky_edges_hit += 1

        here = self.current_node
        outcome = self._advance(next_node, targets)
        success, travel_time = self._last_outcome
        self.cmap.update_from_traversal(
            here, next_node, success, current_step, actual_travel_time=travel_time
        )
        return outcome


class OmniscientAgent(_Evacuee):
    def plan(self, targets):
        path = _dijkstra_perfect(self.road_network.G, self.current_node, targets)
        self.path = path[1:] if path else []

    def step(self, targets: list, current_step: int) -> str:
        if self.reached_shelter:
            return "arrived"

        if not self.path:
            self.plan(targets)
            if not self.path:
                return "stuck"

        return self._advance(self.path[0], targets)


class ReactiveDynAgent(_Evacuee):
    def __init__(self, agent_id, start_node, road_network):
        super().__init__(agent_id, start_node, road_network)
        self.known_graph: nx.DiGraph = road_network.G.copy()
        self.needs_replan = False

    def receive_reports(self, reports: list, current_step: int):
        if reports:
            self.needs_replan = True
        for r in reports:
            u, v = r["u"], r["v"]
            if self.known_graph.has_edge(u, v):
                self.known_graph[u][v]["true_open"] = r["reported_open"]

    def plan(self, targets):
        path = _dijkstra_perfect(self.known_graph, self.current_node, targets)
        self.path = path[1:] if path else []

    def step(self, targets: list, current_step: int) -> str:
        if self.reached_shelter:
            return "arrived"

        if self.needs_replan:
            self.plan(targets)
            self.needs_replan = False
            if not self.path:
                return "stuck"

        if not self.path:
            self.plan(targets)
            if not self.path:
                return "stuck"

        return self._advance(self.path[0], targets)
