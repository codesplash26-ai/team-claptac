"""Outcome models consume a copied state, action, and seeded RNG."""

from random import Random
from typing import Protocol

from atlas.reasoning.contracts import Action, Transition


class OutcomeModel(Protocol):
    def step(self, state: dict, action: Action, rng: Random) -> Transition: ...
