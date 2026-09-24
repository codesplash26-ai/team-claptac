"""Small agent adapter contract. Simulations and fallbacks must be side-effect free."""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from random import Random
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Action(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    params: dict[str, Any] = Field(default_factory=dict)


class Transition(BaseModel):
    state: dict[str, Any]
    reward: float = Field(allow_inf_nan=False)
    terminal: bool = False


class PlannerConfig(BaseModel):
    mode: Literal["scenario", "mcts"] = "mcts"
    simulations: int = Field(default=128, ge=1, le=10000)
    max_depth: int = Field(default=3, ge=1, le=20)
    max_candidates: int = Field(default=5, ge=1, le=50)
    timeout_seconds: float = Field(default=1.0, gt=0, le=120)
    max_model_calls: int = Field(default=0, ge=0)
    exploration: float = Field(default=1.4, ge=0)
    reward_scale: float = Field(default=100.0, gt=0)
    discount: float = Field(default=0.95, gt=0, le=1)
    objective: Literal["mean", "lower_tail"] = "mean"
    tail_fraction: float = Field(default=0.2, gt=0, le=1)
    seed: int = 0


class PlanningRequest(BaseModel):
    agent: str
    state: dict[str, Any]
    snapshot_version: int
    valid_until: datetime | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class PlanningResult(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    agent: str
    action: Action | None = None
    snapshot_version: int
    valid_until: datetime | None = None
    input_state: dict[str, Any] = Field(default_factory=dict)
    configuration: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str]
    assumptions: list[str]
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    simulations: int = 0
    model_calls: int = 0
    elapsed_seconds: float = 0
    stop_reason: str
    fallback_used: bool = False
    trace: list[dict[str, Any]] = Field(default_factory=list)

    def require_current(self, version: int, *, now=None):
        """Caller must check again immediately before proposing/executing a plan."""
        if version != self.snapshot_version:
            raise ValueError("Planning snapshot is stale; replan before proposing an action")
        if self.valid_until is not None and (now or datetime.now(timezone.utc)) >= self.valid_until:
            raise ValueError("Planning evidence expired; refresh before proposing an action")


class BudgetExceeded(Exception):
    pass


class Budget:
    """Adapters must route every optional model query through call_model."""

    def __init__(self, config: PlannerConfig):
        self.deadline = time.monotonic() + config.timeout_seconds
        self.max_model_calls = config.max_model_calls
        self.model_calls = 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise BudgetExceeded("timeout")

    async def call_model(self, factory: Callable[[], Awaitable[Any]]):
        self.check()
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceeded("model_call_budget")
        self.model_calls += 1
        return await asyncio.wait_for(
            factory(), timeout=max(0.001, self.deadline - time.monotonic())
        )


class Adapter(ABC):
    @abstractmethod
    async def candidates(self, state: dict, budget: Budget) -> list[Action]: ...

    @abstractmethod
    async def simulate(
        self, state: dict, action: Action, rng: Random, budget: Budget
    ) -> Transition: ...

    @abstractmethod
    def feasible(self, state: dict, action: Action) -> bool: ...

    @abstractmethod
    def terminal(self, state: dict) -> bool: ...

    @abstractmethod
    def fallback(self, state: dict) -> Action | None:
        """Fast local fallback. No model queries, I/O, or side effects."""
