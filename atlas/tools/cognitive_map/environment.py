"""Road-network environment for the evacuation simulation (ground truth + noisy reports).

Ported from EvacuationAgent-using-Cognitivity/simulation_map/environment.py. Only flood-style
closures are kept: the original earthquake and wildfire helpers drew from the global, unseeded
`random` module (so runs were not reproducible) and are outside ATLAS's scope.

Nodes = intersections/shelters. Edges = roads with travel_time, true_open (ground truth) and
condition.
"""

from __future__ import annotations

import random

import networkx as nx
import numpy as np


class RoadNetwork:
    def __init__(self, seed: int = 42):
        self.rng = random.Random(seed)
        self.np_rng = np.random.default_rng(seed)
        self.G: nx.DiGraph = nx.DiGraph()
        self._pending_reports: list[dict] = []

    def add_node(self, node_id, is_shelter: bool = False, **attrs):
        self.G.add_node(node_id, is_shelter=is_shelter, **attrs)

    def add_road(
        self,
        u,
        v,
        travel_time: float,
        bidirectional: bool = True,
        elev_u: float = 0.0,
        elev_v: float = 0.0,
    ):
        self.G.add_edge(
            u,
            v,
            travel_time=travel_time,
            true_open=1.0,
            condition="open",
            closure_time=None,
            elev_u=elev_u,
            elev_v=elev_v,
        )
        if bidirectional:
            self.G.add_edge(
                v,
                u,
                travel_time=travel_time,
                true_open=1.0,
                condition="open",
                closure_time=None,
                elev_u=elev_v,
                elev_v=elev_u,
            )

    def apply_disaster(self, closed_edges: list[tuple], step: int):
        for u, v in closed_edges:
            if self.G.has_edge(u, v):
                self.G[u][v]["true_open"] = 0.0
                self.G[u][v]["condition"] = "closed"
                self.G[u][v]["closure_time"] = step

    def apply_flood(self, water_level: float, step: int) -> list[tuple]:
        """Close every road with an endpoint at or below `water_level` (metres)."""
        closed_edges = []
        for u, v, data in self.G.edges(data=True):
            if data["true_open"] == 0.0:
                continue
            if data.get("elev_u", 0.0) <= water_level or data.get("elev_v", 0.0) <= water_level:
                data["true_open"] = 0.0
                data["condition"] = "closed"
                data["closure_time"] = step
                closed_edges.append((u, v))
        return closed_edges

    def partial_closure(self, edges: list[tuple], slowdown: float = 2.0):
        for u, v in edges:
            if self.G.has_edge(u, v):
                self.G[u][v]["travel_time"] *= slowdown
                self.G[u][v]["condition"] = "partial"
                self.G[u][v]["true_open"] = 0.5

    def traverse(self, u, v) -> tuple[bool, float]:
        """Returns (success, actual_travel_time)."""
        if not self.G.has_edge(u, v):
            return False, float("inf")

        data = self.G[u][v]
        if data["true_open"] >= 0.7:
            return True, data["travel_time"]
        if data["true_open"] >= 0.3:
            blocked = self.rng.random() > data["true_open"]
            return (not blocked), data["travel_time"] * 1.5
        return False, float("inf")

    def generate_report(
        self,
        u,
        v,
        current_step: int,
        delay_range: tuple = (1, 5),
        error_prob: float = 0.15,
    ) -> dict | None:
        """Queue a delayed, possibly wrong report about an edge (models misinformation)."""
        if not self.G.has_edge(u, v):
            return None

        true_open = self.G[u][v]["true_open"]
        delay = self.rng.randint(*delay_range)
        reported_open = true_open

        if self.rng.random() < error_prob:
            reported_open = 1.0 - true_open
        elif self.rng.random() < 0.2:
            noise = self.rng.uniform(-0.2, 0.2)
            reported_open = float(np.clip(true_open + noise, 0.0, 1.0))

        report = {
            "u": u,
            "v": v,
            "reported_open": reported_open,
            "delivery_step": current_step + delay,
        }
        self._pending_reports.append(report)
        return report

    def get_due_reports(self, current_step: int) -> list[dict]:
        due = [r for r in self._pending_reports if r["delivery_step"] <= current_step]
        self._pending_reports = [
            r for r in self._pending_reports if r["delivery_step"] > current_step
        ]
        return due

    def shelters(self):
        return [n for n, d in self.G.nodes(data=True) if d.get("is_shelter")]

    def nodes(self):
        return list(self.G.nodes())

    def edges(self):
        return list(self.G.edges(data=True))
