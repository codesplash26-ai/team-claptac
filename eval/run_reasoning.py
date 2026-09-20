"""Compare baseline, scenario evaluation and MCTS on identical synthetic episodes."""

import argparse
import asyncio
import json
import time
from copy import deepcopy
from pathlib import Path
from random import Random
from statistics import mean

from atlas.agents.planning.commander import CommanderAdapter
from atlas.reasoning import Action, Budget, Planner, PlannerConfig, PlanningRequest


async def evaluate(path, trials=30):
    scenario = json.loads(Path(path).read_text())
    results = {}
    for mode in ("shortest_feasible", "scenario", "mcts"):
        scores, times, failures, successes = [], [], [], []
        for seed in range(trials):
            state = deepcopy(scenario["state"])
            adapter = CommanderAdapter()
            # Each route has an independent seeded stream shared across policies.
            streams = {
                key: Random(seed * 1000 + i) for i, key in enumerate(sorted(state["routes"]))
            }
            total, failed = 0, 0
            started = time.monotonic()
            for step in range(3):
                config = PlannerConfig(
                    mode="mcts" if mode == "mcts" else "scenario", seed=10000 + seed * 3 + step
                )
                if mode == "shortest_feasible":
                    candidates = [
                        Action(id="route:" + key, params={"route": key}) for key in state["routes"]
                    ]
                    feasible = [a for a in candidates if adapter.feasible(state, a)]
                    action = (
                        min(feasible, key=lambda a: state["routes"][a.params["route"]]["minutes"])
                        if feasible
                        else None
                    )
                else:
                    result = await Planner(config).plan(
                        PlanningRequest(
                            agent="a3_incident_commander",
                            state=state,
                            snapshot_version=0,
                            assumptions=scenario["assumptions"],
                        ),
                        adapter,
                    )
                    action = result.action
                if action is None:
                    break
                transition = await adapter.simulate(
                    state, action, streams[action.params["route"]], Budget(config)
                )
                state, total = transition.state, total + transition.reward
                failed += int(not transition.terminal)
                if transition.terminal:
                    break
            scores.append(total)
            times.append(time.monotonic() - started)
            failures.append(failed)
            successes.append(bool(state.get("complete")))
        results[mode] = {
            "trials": trials,
            "mean_return": mean(scores),
            "success_rate": mean(successes),
            "mean_failed_routes": mean(failures),
            "mean_planning_and_simulation_seconds": mean(times),
        }
    return {
        "scenario": scenario["name"],
        "assumptions": scenario["assumptions"],
        "results": results,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="data/scenarios/planning.json")
    parser.add_argument("--trials", type=int, default=30)
    args = parser.parse_args()
    if args.trials < 1:
        parser.error("trials must be positive")
    print(json.dumps(asyncio.run(evaluate(args.scenario, args.trials)), indent=2))
