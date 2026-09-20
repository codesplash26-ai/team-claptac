"""A3 integration example; does not implement or execute the Incident Commander."""

from atlas.reasoning import Action, Adapter
from atlas.simulation.flood import FloodModel


class CommanderAdapter(Adapter):
    def __init__(self, model=None):
        self.model = model or FloodModel()

    async def candidates(self, state, budget):
        return [Action(id="route:" + key, params={"route": key}) for key in sorted(state["routes"])]

    def feasible(self, state, action):
        key = action.params.get("route")
        route = state["routes"].get(key)
        return bool(
            route
            and key not in state.get("blocked", [])
            and route["capacity"] >= state["people"]
            and route.get("permitted", True)
        )

    async def simulate(self, state, action, rng, budget):
        return self.model.step(state, action, rng)

    def terminal(self, state):
        return bool(state.get("complete"))

    def fallback(self, state):
        candidates = [Action(id="route:" + key, params={"route": key}) for key in state["routes"]]
        valid = [a for a in candidates if self.feasible(state, a)]
        return (
            min(
                valid,
                key=lambda a: (
                    state["routes"][a.params["route"]]["failure_probability"],
                    state["routes"][a.params["route"]]["minutes"],
                ),
            )
            if valid
            else None
        )
