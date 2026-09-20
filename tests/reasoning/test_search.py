import asyncio
import json
from copy import deepcopy
from pathlib import Path

import pytest

from atlas.agents.planning.commander import CommanderAdapter
from atlas.agents.planning.sentinel import SentinelAdapter
from atlas.reasoning import Action, Adapter, Planner, PlannerConfig, PlanningRequest, Transition


class TwoStep(Adapter):
    async def candidates(self, state, budget):
        return (
            [Action(id="finish")]
            if state.get("stage")
            else [Action(id="quick"), Action(id="invest")]
        )

    def feasible(self, state, action):
        return action.id in {"quick", "invest", "finish"}

    async def simulate(self, state, action, rng, budget):
        state["mutated"] = True
        if action.id == "invest":
            return Transition(state={"stage": 1}, reward=-1)
        return Transition(
            state={"done": True}, reward=10 if action.id == "finish" else 2, terminal=True
        )

    def terminal(self, state):
        return bool(state.get("done"))

    def fallback(self, state):
        return Action(id="quick")


def request(state):
    return PlanningRequest(agent="test", state=state, snapshot_version=7)


async def test_mcts_looks_ahead_and_scenario_is_a_real_baseline():
    state = {"stage": 0}
    mcts = await Planner().plan(request(state), TwoStep())
    scenario = await Planner(PlannerConfig(mode="scenario")).plan(request(state), TwoStep())
    assert mcts.action.id == "invest"
    assert scenario.action.id == "quick"
    assert state == {"stage": 0}
    assert mcts.snapshot_version == 7
    assert mcts.input_state == state
    assert mcts.configuration["max_depth"] == 3
    assert any(len(item["steps"]) == 2 for item in mcts.trace)


async def test_seed_reproducibility_and_two_agent_adapters():
    scenario = json.loads(Path("data/scenarios/planning.json").read_text())
    state = scenario["state"]
    original = deepcopy(state)
    planner = Planner(PlannerConfig(mode="scenario", simulations=200, seed=17))
    first = await planner.plan(request(state), CommanderAdapter())
    second = await planner.plan(request(state), CommanderAdapter())
    assert first.action == second.action
    assert first.alternatives == second.alternatives
    assert first.trace == second.trace
    assert first.action.id == "route:long"
    assert "route:full_shelter" not in [a["action"]["id"] for a in first.alternatives]
    assert state == original
    sentinel = await planner.plan(
        request({"verification_success_probability": 0.9}), SentinelAdapter()
    )
    assert sentinel.action.id == "recommend_verification"


async def test_timeout_and_exception_use_fallback():
    class Slow(TwoStep):
        async def candidates(self, state, budget):
            await asyncio.sleep(1)

    result = await Planner(PlannerConfig(timeout_seconds=0.01)).plan(request({}), Slow())
    assert result.fallback_used and result.action.id == "quick"
    assert result.stop_reason == "timeout"

    class Broken(TwoStep):
        async def simulate(self, *args):
            raise RuntimeError("model unavailable")

    result = await Planner().plan(request({}), Broken())
    assert result.fallback_used and result.stop_reason == "adapter_error:RuntimeError"


async def test_model_budget_checked_before_call():
    calls = []

    class ModelAdapter(TwoStep):
        async def candidates(self, state, budget):
            async def query():
                calls.append(1)
                return [Action(id="quick")]

            return await budget.call_model(query)

    result = await Planner().plan(request({}), ModelAdapter())
    assert not calls and result.stop_reason == "model_call_budget"
    result = await Planner(PlannerConfig(max_model_calls=1)).plan(request({}), ModelAdapter())
    assert calls == [1] and result.model_calls == 1


async def test_invalid_fallback_cannot_escape_constraint_filter():
    class Denied(TwoStep):
        def feasible(self, state, action):
            return False

    result = await Planner().plan(request({}), Denied())
    assert result.action is None and result.fallback_used


async def test_terminal_state_returns_no_action():
    result = await Planner().plan(request({"done": True}), TwoStep())
    assert result.action is None and result.stop_reason == "terminal"


async def test_too_little_budget_does_not_select_untested_winner():
    result = await Planner(PlannerConfig(simulations=1)).plan(request({}), TwoStep())
    assert result.fallback_used and result.stop_reason == "insufficient_coverage"


async def test_lower_tail_changes_risky_choice():
    class Risky(TwoStep):
        async def candidates(self, state, budget):
            return [Action(id="quick"), Action(id="invest")]

        async def simulate(self, state, action, rng, budget):
            value = (20 if rng.random() > 0.2 else -30) if action.id == "quick" else 5
            return Transition(state={"done": True}, reward=value, terminal=True)

    mean = await Planner(PlannerConfig(mode="scenario", simulations=1000)).plan(
        request({}), Risky()
    )
    tail = await Planner(
        PlannerConfig(mode="scenario", simulations=1000, objective="lower_tail")
    ).plan(request({}), Risky())
    assert mean.action.id == "quick"
    assert tail.action.id == "invest"


@pytest.mark.parametrize(
    "config", [{"max_depth": 0}, {"simulations": 0}, {"timeout_seconds": -1}, {"tail_fraction": 2}]
)
def test_invalid_budgets_rejected(config):
    with pytest.raises(ValueError):
        PlannerConfig(**config)
