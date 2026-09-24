"""A1 recommends verification to A3; it never delegates or suppresses rule triggers."""

from atlas.reasoning import Action, Adapter, Transition


class SentinelAdapter(Adapter):
    async def candidates(self, state, budget):
        return [
            Action(id="recommend_verification"),
            Action(id="flag_conflict"),
            Action(id="monitor"),
        ]

    def feasible(self, state, action):
        return action.id in {"recommend_verification", "flag_conflict", "monitor"}

    async def simulate(self, state, action, rng, budget):
        # Declared demo assumptions; these are NOT derived from report confidence.
        p = state["verification_success_probability"]
        if not 0 <= p <= 1:
            raise ValueError("Invalid scenario probability")
        reward = (
            {"flag_conflict": 1.0, "monitor": -2.0}[action.id]
            if action.id != "recommend_verification"
            else (4.0 if rng.random() < p else -1.0)
        )
        return Transition(state={**state, "complete": True}, reward=reward, terminal=True)

    def terminal(self, state):
        return bool(state.get("complete"))

    def fallback(self, state):
        return Action(id="flag_conflict")
