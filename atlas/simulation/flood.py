"""Explicitly synthetic route/shelter model; probabilities are scenario assumptions."""

from atlas.reasoning import Transition


class FloodModel:
    def step(self, state, action, rng):
        route = state["routes"][action.params["route"]]
        probability = route["failure_probability"]
        if not 0 <= probability <= 1:
            raise ValueError("Scenario failure probability must be in [0,1]")
        if rng.random() < probability:
            state["blocked"] = [*state.get("blocked", []), action.params["route"]]
            return Transition(state=state, reward=-40, terminal=False)
        state["complete"] = True
        return Transition(state=state, reward=100 - route["minutes"], terminal=True)
