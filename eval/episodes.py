"""Monte-Carlo episode runner for the A/B/C scenarios (Cognitive vs Omniscient vs Reactive).

Ported from EvacuationAgent-using-Cognitivity/simulation.py. The episode logic, seeds and constants
are unchanged so the results can be compared episode-by-episode with the original `results.json`
(see tests/tools/test_cognitive_map_fidelity.py).

`simulate` is the shared step loop. What happens to the roads at each step is supplied as a `hook`,
so the same loop drives the synthetic grids here and the real road networks in `eval.networks`.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field

from atlas.tools.cognitive_map.beliefs import CognitiveMap
from atlas.tools.cognitive_map.routing import SLOPE_PENALTY_PER_M
from atlas.tools.cognitive_map.scenarios import (
    build_small_grid,
    build_stress_grid,
    scenario_A_normal,
    scenario_B_flood,
    scenario_B_flood_stress,
    scenario_C_high_noise,
    scenario_C_high_noise_stress,
)
from atlas.tools.cognitive_map.sim_agents import (
    CognitiveAgent,
    OmniscientAgent,
    ReactiveDynAgent,
)

MAX_STEPS = 60
N_AGENTS = 5
REPORT_EVERY = 2
DEFAULT_DELAY_RANGE = (2, 6)
STRESS_MODE = True
STRESS_N_AGENTS = 10
STRESS_DELAY_RANGE = (3, 8)
AGENT_TYPES = ("Omniscient", "Cognitive", "Reactive")


@dataclass
class AgentResult:
    agent_type: str
    agent_id: int
    reached: bool
    total_travel_time: float
    failed_routes: int
    risky_edges_hit: int
    steps_taken: int


@dataclass
class EpisodeResult:
    scenario: str
    run_id: int
    agents: list[AgentResult] = field(default_factory=list)
    evacuation_time_by_type: dict[str, float | None] = field(default_factory=dict)


def simulate(
    rn,
    shelters,
    start_nodes,
    hook,
    *,
    error_prob: float,
    delay_range: tuple[int, int],
    scenario: str,
    run_id: int,
    max_steps: int = MAX_STEPS,
    report_every: int = REPORT_EVERY,
    slope_penalty_per_m: float = SLOPE_PENALTY_PER_M,
) -> EpisodeResult:
    """Run one episode: the same evacuees on the same roads with each of the three agent types.

    `hook(rn, step, report)` is called at the start of every step. It applies whatever happens to
    the roads at that step and calls `report(u, v)` for each road that outside observers would
    report on (reports are delayed and sometimes wrong, see `RoadNetwork.generate_report`).
    """
    cog_agents, omni_agents, reac_agents = [], [], []
    for i, start in enumerate(start_nodes):
        cmap = CognitiveMap(
            rn.G, prior_open=0.9, alpha_risk=0.5, slope_penalty_per_m=slope_penalty_per_m
        )
        cog_agents.append(CognitiveAgent(i, start, rn, cmap))
        omni_agents.append(OmniscientAgent(i, start, rn))
        reac_agents.append(ReactiveDynAgent(i, start, rn))

    for group in (cog_agents, omni_agents, reac_agents):
        for a in group:
            a.plan(shelters)

    episode = EpisodeResult(scenario=scenario, run_id=run_id)

    for step in range(1, max_steps + 1):

        def report(u, v, step=step):
            rn.generate_report(u, v, step, delay_range=delay_range, error_prob=error_prob)

        hook(rn, step, report)

        if step % report_every == 0:
            due = rn.get_due_reports(step)
            for a in cog_agents:
                a.receive_reports(due, step)
            for a in reac_agents:
                a.receive_reports(due, step)

        for group in (cog_agents, omni_agents, reac_agents):
            for a in group:
                a.step(shelters, step)

        if all(a.reached_shelter for g in (cog_agents, omni_agents, reac_agents) for a in g):
            break

    for label, group in (
        ("Cognitive", cog_agents),
        ("Omniscient", omni_agents),
        ("Reactive", reac_agents),
    ):
        for a in group:
            episode.agents.append(
                AgentResult(
                    label,
                    a.id,
                    a.reached_shelter,
                    a.total_travel_time,
                    a.failed_routes,
                    a.risky_edges_hit,
                    a.steps_taken,
                )
            )

    def _evac_time(agent_list):
        if all(a.reached_shelter for a in agent_list):
            return max(a.total_travel_time for a in agent_list)
        return None

    episode.evacuation_time_by_type = {
        "Cognitive": _evac_time(cog_agents),
        "Omniscient": _evac_time(omni_agents),
        "Reactive": _evac_time(reac_agents),
    }
    return episode


def scheduled_hook(closures, partials):
    """Hook for scripted scenarios: full closures and slowdowns at fixed steps, in that order."""

    def hook(rn, step, report):
        for dis_step, edges in closures:
            if dis_step == step:
                rn.apply_disaster(edges, step)
                for u, v in edges:
                    report(u, v)
        # Partial closures: slowdowns with uncertainty
        for p_step, edges, slowdown in partials:
            if p_step == step:
                rn.partial_closure(edges, slowdown=slowdown)
                for u, v in edges:
                    report(u, v)

    return hook


def run_episode(
    scenario_name,
    run_id,
    seed,
    error_prob=0.15,
    *,
    stress_mode: bool = STRESS_MODE,
    n_agents: int | None = None,
    delay_range: tuple[int, int] | None = None,
    error_prob_override: float | None = None,
) -> EpisodeResult:
    rn = build_stress_grid() if stress_mode else build_small_grid()
    rng = random.Random(seed)
    closures = []

    n_agents_effective = (
        n_agents if n_agents is not None else (STRESS_N_AGENTS if stress_mode else N_AGENTS)
    )
    delay_range_effective = (
        delay_range
        if delay_range is not None
        else (STRESS_DELAY_RANGE if stress_mode else DEFAULT_DELAY_RANGE)
    )

    partials = []

    if scenario_name == "A":
        rn, closures, partials = scenario_A_normal(rn)
    elif scenario_name == "B":
        rn, closures, partials = (
            scenario_B_flood_stress(rn) if stress_mode else scenario_B_flood(rn)
        )
    elif scenario_name == "C":
        rn, closures, extra, partials = (
            scenario_C_high_noise_stress(rn) if stress_mode else scenario_C_high_noise(rn)
        )
        error_prob = extra.get("error_prob", error_prob)

    if error_prob_override is not None:
        error_prob = error_prob_override

    shelters = rn.shelters()
    start_nodes = [
        rng.choice([n for n in rn.nodes() if n not in shelters]) for _ in range(n_agents_effective)
    ]
    return simulate(
        rn,
        shelters,
        start_nodes,
        scheduled_hook(closures, partials),
        error_prob=error_prob,
        delay_range=delay_range_effective,
        scenario=scenario_name,
        run_id=run_id,
    )


def aggregate(episodes, agent_type) -> dict:
    """Summary metrics for one agent type across episodes."""
    all_agents = [a for ep in episodes for a in ep.agents if a.agent_type == agent_type]
    travel_times = [a.total_travel_time for a in all_agents if a.reached]
    evac_times = [
        ep.evacuation_time_by_type.get(agent_type)
        for ep in episodes
        if ep.evacuation_time_by_type.get(agent_type) is not None
    ]
    return {
        "agent_type": agent_type,
        "success_rate": statistics.mean(int(a.reached) for a in all_agents),
        "avg_travel_time": statistics.mean(travel_times) if travel_times else 0.0,
        "avg_failed_routes": statistics.mean(a.failed_routes for a in all_agents),
        "avg_risky_edges": statistics.mean(a.risky_edges_hit for a in all_agents),
        "avg_total_evac_time": statistics.mean(evac_times) if evac_times else 0.0,
    }


def run_experiment(scenario, n_runs=20, *, stress_mode: bool = STRESS_MODE) -> list[EpisodeResult]:
    """Same seeds as the original: episode r uses seed 1000 + r."""
    return [run_episode(scenario, r, seed=1000 + r, stress_mode=stress_mode) for r in range(n_runs)]
