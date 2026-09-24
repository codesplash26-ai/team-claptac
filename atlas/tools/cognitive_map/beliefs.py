"""Per-edge Bayesian belief updates: experience, reports, time decay.

Ported from EvacuationAgent-using-Cognitivity/simulation_map/cognitive_map.py. The three update
rules are unchanged, because the proposal's evaluation (96% evacuation success) was produced with
them:

* direct traversal: Bayesian update with fixed likelihoods (0.95 / 0.05);
* external report:  linear blend, weighted by the report's reliability;
* time decay:       exponential pull toward 0.5 based on age since `last_updated`.

Known quirk kept for fidelity: `apply_time_decay` does not reset `last_updated`, so calling it every
step compounds the decay. The simulation relies on that behaviour. ATLAS's live path calls it once
per plan on a copy rebuilt from COP beliefs, where it is a single decay from the last evidence.

Time is a plain number. The simulation uses steps; the live path uses minutes (1 step ~ 1 minute).

This module has no dependency on the rest of ATLAS.
"""

from __future__ import annotations

import networkx as nx
import numpy as np

from atlas.tools.cognitive_map import routing

DECAY_RATE = 0.02
ALPHA_RISK = 0.5
TRAVEL_ALPHA = 0.3
MIN_BELIEF = 0.05
MAX_BELIEF = 0.95


def _clamp(v, lo=MIN_BELIEF, hi=MAX_BELIEF):
    return float(np.clip(v, lo, hi))


class CognitiveMap:
    def __init__(
        self,
        graph_template: nx.DiGraph,
        prior_open: float = 0.9,
        alpha_risk: float = ALPHA_RISK,
        slope_penalty_per_m: float = routing.SLOPE_PENALTY_PER_M,
    ):
        self.alpha = alpha_risk
        self.slope_penalty_per_m = slope_penalty_per_m
        self.map: nx.DiGraph = nx.DiGraph()
        self._persisted: dict[tuple, float] = {}

        for u, v, data in graph_template.edges(data=True):
            self.map.add_edge(
                u,
                v,
                belief_open=_clamp(prior_open),
                est_travel=data.get("travel_time", 5.0),
                last_updated=0,
                uncertainty=1.0 - abs(prior_open - 0.5) * 2,
                elev_u=data.get("elev_u", 0.0),
                elev_v=data.get("elev_v", 0.0),
            )

        for n, d in graph_template.nodes(data=True):
            self.map.add_node(n, **d)

    # ------------------------------------------------------------------ routing

    def edge_cost(self, u, v) -> float:
        return routing.edge_cost(self, u, v)

    def best_path(self, source, targets: list):
        return routing.best_path(self, source, targets)

    # ------------------------------------------------------------- belief updates

    def update_from_traversal(
        self,
        u,
        v,
        success: bool,
        step: int,
        actual_travel_time: float | None = None,
    ):
        """Update 1: direct traversal experience (belief + travel-time estimate)."""
        if not self.map.has_edge(u, v):
            return

        prior = self.map[u][v]["belief_open"]
        lh_open, lh_closed = (0.95, 0.05) if success else (0.05, 0.95)
        posterior = (lh_open * prior) / (lh_open * prior + lh_closed * (1.0 - prior))

        self.map[u][v]["belief_open"] = _clamp(posterior)
        self.map[u][v]["last_updated"] = step
        self.map[u][v]["uncertainty"] = 1.0 - abs(posterior - 0.5) * 2

        if success and actual_travel_time is not None and np.isfinite(actual_travel_time):
            d = self.map[u][v]
            prior_tt = float(d.get("est_travel", actual_travel_time))
            blended = (1.0 - TRAVEL_ALPHA) * prior_tt + TRAVEL_ALPHA * float(actual_travel_time)
            d["est_travel"] = max(0.05, blended)

    def update_from_report(
        self,
        u,
        v,
        reported_open: float,
        step: int,
        report_reliability: float = 0.8,
    ):
        """Update 2: external delayed/noisy report."""
        if not self.map.has_edge(u, v):
            return

        prior = self.map[u][v]["belief_open"]
        posterior = (1 - report_reliability) * prior + report_reliability * float(reported_open)

        self.map[u][v]["belief_open"] = _clamp(posterior)
        self.map[u][v]["last_updated"] = step
        self.map[u][v]["uncertainty"] = 1.0 - abs(posterior - 0.5) * 2

    def apply_time_decay(self, current_step: int, decay_rate: float = DECAY_RATE):
        """Update 3: temporal decay toward uncertainty baseline (0.5)."""
        for u, v in self.map.edges():
            d = self.map[u][v]
            age = max(0, current_step - d["last_updated"])
            if age > 0:
                b = d["belief_open"]
                b = 0.5 + (b - 0.5) * np.exp(-decay_rate * age)
                d["belief_open"] = _clamp(b)
                d["uncertainty"] = 1.0 - abs(b - 0.5) * 2

    # ------------------------------------------------------- ATLAS live additions

    def set_belief(self, u, v, belief_open: float, last_updated):
        """Load a stored belief (e.g. from the COP) without treating it as new evidence."""
        d = self.map[u][v]
        d["belief_open"] = _clamp(belief_open)
        d["last_updated"] = last_updated
        d["uncertainty"] = 1.0 - abs(d["belief_open"] - 0.5) * 2

    def set_closed(self, u, v, closed: bool = True):
        """Hard closure (A8 close_major_route). Excluded from routing regardless of belief."""
        if self.map.has_edge(u, v):
            self.map[u][v]["closed"] = closed

    def mark_persisted(self) -> None:
        """Remember current beliefs as what the COP already holds."""
        self._persisted = {(u, v): d["belief_open"] for u, v, d in self.map.edges(data=True)}

    def changed_edges(self, epsilon: float = 0.01) -> list[tuple]:
        """Edges whose belief moved more than `epsilon` since `mark_persisted`.

        Every COP write bumps the global COP version and can make an in-flight plan stale at the
        Gateway, so callers write only what changed materially.
        """
        return [
            (u, v)
            for u, v, d in self.map.edges(data=True)
            if abs(d["belief_open"] - self._persisted.get((u, v), d["belief_open"])) > epsilon
        ]
