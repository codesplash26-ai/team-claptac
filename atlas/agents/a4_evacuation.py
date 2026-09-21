"""Stub evacuation planner for Workstream B until C ships A4.

Exposes plan_evacuation(origin, destinations) -> RoutePlan as required by the
team handoff contract.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RoutePlan(BaseModel):
    origin: str
    destinations: list[str]
    primary_route: list[str]
    alternatives: list[list[str]] = Field(default_factory=list)
    risk_score: float = 0.3
    uncertainty: float = 0.2
    eta_minutes: float = 25.0
    notes: str = "stub route — replace with cognitive_map when Workstream C lands"


def plan_evacuation(origin: str, destinations: list[str] | None = None) -> RoutePlan:
    destinations = destinations or ["shelter:default"]
    primary = [origin, f"via:{origin}", destinations[0]]
    alt = [origin, "via:alt", destinations[0]]
    return RoutePlan(
        origin=origin,
        destinations=destinations,
        primary_route=primary,
        alternatives=[alt],
        risk_score=0.35,
        uncertainty=0.25,
        eta_minutes=30.0,
    )


class EvacuationAgent:
    """Minimal A4 stand-in: handles TASK_ASSIGNED(task=evacuate)."""

    name = "a4_evacuation"

    async def plan(self, origin: str, destinations: list[str] | None = None) -> dict[str, Any]:
        return plan_evacuation(origin, destinations).model_dump()
