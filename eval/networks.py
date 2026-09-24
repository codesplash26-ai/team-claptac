"""Evacuation episodes on the real road networks (Colombo Fort, Ratnapura, Kandy).

The original repo ran its real-map scenarios inside a Flask app (`simulation_map/app.py`); this
reproduces that flood in the shared `simulate` loop so it can be run headless, seeded and in bulk:

* the flood rises in two stages, 2 m then 5 m above the lowest ground (steps 5 and 12);
* roads within 1.5 m above the flood line are slowed (partial closure) instead of shut;
* reports about affected roads arrive 3-7 steps late and are wrong 20% of the time
  (scenario C: 40%), and are delivered every step.

Differences from the original, so results are not silently compared as if identical:
* shelters are the reference-data shelters (real graph nodes on high ground), not random nodes;
* evacuees start on ground the final flood level does not submerge, and their starts are seeded;
* the largest strongly connected component is used, so every start can reach every shelter;
* 10 evacuees per agent type per episode, as in the stress grid.
These are illustrative synthetic scenarios on real geometry, not a forecast of any real flood.
"""

from __future__ import annotations

import random

from atlas.tools import geo
from atlas.tools.cognitive_map.environment import RoadNetwork
from atlas.tools.cognitive_map.routing import SLOPE_PENALTY_PER_M
from atlas.tools.reference import load_shelters
from eval.episodes import AGENT_TYPES, EpisodeResult, aggregate, simulate

FLOOD_LEVELS_M = {5: 2.0, 12: 5.0}  # step -> metres above the lowest ground (original app.py)
PARTIAL_BAND_M = 1.5
PARTIAL_SLOWDOWN = 1.4
REPORT_DELAY_RANGE = (3, 7)
REPORT_ERROR_PROB = 0.20
HIGH_NOISE_ERROR_PROB = 0.40
N_AGENTS = 10
MAX_STEPS = 60
SCENARIOS = {"A": "no disaster", "B": "flood", "C": "flood, high misinformation"}


def road_network_from_graph(rg: geo.RoadGraph, shelter_nodes, seed: int) -> RoadNetwork:
    """Ground-truth simulator network for a cached road graph, with shelters flagged."""
    rn = RoadNetwork(seed=seed)
    shelters = set(shelter_nodes)
    for node, data in rg.graph.nodes(data=True):
        rn.add_node(
            node,
            is_shelter=node in shelters,
            lat=data["lat"],
            lon=data["lon"],
            elevation=data["elevation"],
        )
    for u, v, data in rg.graph.edges(data=True):
        rn.add_road(
            u,
            v,
            travel_time=data["travel_time"],
            bidirectional=False,
            elev_u=data["elev_u"],
            elev_v=data["elev_v"],
        )
    return rn


def lowest_ground_m(rn: RoadNetwork) -> float:
    return min(min(d["elev_u"], d["elev_v"]) for *_, d in rn.G.edges(data=True))


def flood_hook(levels: dict[int, float] | None = None):
    """The original app's flood: close low roads, slow roads just above the line, report both."""
    levels = FLOOD_LEVELS_M if levels is None else levels

    def hook(rn, step, report):
        if step not in levels:
            return
        level = lowest_ground_m(rn) + levels[step]
        closed = rn.apply_flood(level, step)
        slowed = [
            (u, v)
            for u, v, d in rn.G.edges(data=True)
            if d["true_open"] >= 0.7
            and level < min(d["elev_u"], d["elev_v"]) <= level + PARTIAL_BAND_M
        ]
        if slowed:
            rn.partial_closure(slowed, slowdown=PARTIAL_SLOWDOWN)
        for u, v in closed:
            report(u, v)
        for u, v in slowed:
            report(u, v)

    return hook


def start_candidates(rn: RoadNetwork, levels: dict[int, float] | None = None) -> list:
    """Nodes an evacuee can start from: not a shelter, and not submerged by the final flood."""
    levels = FLOOD_LEVELS_M if levels is None else levels
    final = lowest_ground_m(rn) + max(levels.values())
    return sorted(
        n
        for n, d in rn.G.nodes(data=True)
        if not d.get("is_shelter") and d["elevation"] > final and rn.G.out_degree(n) > 0
    )


def run_network_episode(
    rg: geo.RoadGraph,
    scenario: str,
    run_id: int,
    seed: int,
    *,
    shelter_nodes=None,
    n_agents: int = N_AGENTS,
    slope_penalty_per_m: float = SLOPE_PENALTY_PER_M,
) -> EpisodeResult:
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario {scenario!r}; expected one of {sorted(SCENARIOS)}")
    if shelter_nodes is None:
        shelter_nodes = [s.node for s in load_shelters(rg.name)]
    rn = road_network_from_graph(rg, shelter_nodes, seed)
    pool = start_candidates(rn)
    if not pool:
        raise ValueError("No node is above the final flood level to start from")
    rng = random.Random(seed)
    starts = [rng.choice(pool) for _ in range(n_agents)]
    hook = (lambda rn, step, report: None) if scenario == "A" else flood_hook()
    return simulate(
        rn,
        rn.shelters(),
        starts,
        hook,
        error_prob=HIGH_NOISE_ERROR_PROB if scenario == "C" else REPORT_ERROR_PROB,
        delay_range=REPORT_DELAY_RANGE,
        scenario=scenario,
        run_id=run_id,
        max_steps=MAX_STEPS,
        report_every=1,
        slope_penalty_per_m=slope_penalty_per_m,
    )


def run_network_experiment(
    site: str,
    scenario: str,
    n_runs: int = 20,
    *,
    n_agents: int = N_AGENTS,
    slope_penalty_per_m: float = SLOPE_PENALTY_PER_M,
) -> list[EpisodeResult]:
    """Episode r uses seed 1000 + r, like the grid experiments.

    `slope_penalty_per_m` defaults to the original engine's 0.10; ATLAS plans with 0.0.
    """
    rg = geo.load_site(site)
    shelters = [s.node for s in load_shelters(site)]
    return [
        run_network_episode(
            rg,
            scenario,
            r,
            1000 + r,
            shelter_nodes=shelters,
            n_agents=n_agents,
            slope_penalty_per_m=slope_penalty_per_m,
        )
        for r in range(n_runs)
    ]


__all__ = [
    "AGENT_TYPES",
    "SCENARIOS",
    "aggregate",
    "flood_hook",
    "road_network_from_graph",
    "run_network_episode",
    "run_network_experiment",
    "start_candidates",
]
