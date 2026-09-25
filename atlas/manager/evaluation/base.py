"""Evaluators: how well did the agents do?  (Owner: Dinuka)

An evaluator is a small function registered with a
**level** (what it scores) and the **modes** it can run in:

* level ``span``   one step (an agent handling a message, an executor call);
* level ``trace``  one whole incident;
* level ``agent``  one agent across every trace.
* mode ``monitor``     online, on live traces, on a schedule;
* mode ``experiment``  offline, on a scenario replay in a sandbox, compared with a baseline.

Each returns an ``EvalResult``; ``passed=None`` means "not applicable here" (recorded, not counted).
Evaluators only read: they cannot change the system they are judging.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from pydantic import BaseModel, Field


class Level(str, Enum):
    SPAN = "span"
    TRACE = "trace"
    AGENT = "agent"


class Mode(str, Enum):
    MONITOR = "monitor"
    EXPERIMENT = "experiment"


class EvalResult(BaseModel):
    value: float | None = None
    passed: bool | None = None  # None: not applicable
    detail: str = ""


class Score(BaseModel):
    evaluator: str
    level: Level
    target: str  # trace id, agent name or span id
    incident_id: str | None = None
    value: float | None
    passed: bool | None
    threshold: float | None
    unit: str = ""
    detail: str = ""
    critical: bool = False
    at: float = 0.0


@dataclass(frozen=True)
class Evaluator:
    name: str
    level: Level
    description: str
    fn: Callable[..., EvalResult]
    threshold: float | None = None
    unit: str = ""
    modes: tuple[Mode, ...] = (Mode.MONITOR, Mode.EXPERIMENT)
    critical: bool = False  # a failure here is a safety failure, not a quality miss
    tags: tuple[str, ...] = ()
    builtin: bool = True


REGISTRY: dict[str, Evaluator] = {}


def evaluator(
    name: str,
    *,
    level: Level,
    description: str,
    threshold: float | None = None,
    unit: str = "",
    modes: tuple[Mode, ...] = (Mode.MONITOR, Mode.EXPERIMENT),
    critical: bool = False,
    tags: tuple[str, ...] = (),
    builtin: bool = True,
):
    """Register an evaluator. Custom ones use the same decorator (``builtin=False``)."""

    def wrap(fn: Callable[..., EvalResult]) -> Callable[..., EvalResult]:
        if name in REGISTRY:
            raise ValueError(f"evaluator {name!r} is already registered")
        REGISTRY[name] = Evaluator(
            name, level, description, fn, threshold, unit, modes, critical, tags, builtin
        )
        return fn

    return wrap


def describe(e: Evaluator) -> dict[str, Any]:
    return {
        "name": e.name,
        "level": e.level.value,
        "description": e.description,
        "threshold": e.threshold,
        "unit": e.unit,
        "modes": [m.value for m in e.modes],
        "critical": e.critical,
        "tags": list(e.tags),
        "builtin": e.builtin,
    }


class Summary(BaseModel):
    evaluators: dict[str, dict[str, Any]] = Field(default_factory=dict)
    scored: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    critical_failures: int = 0
    pass_rate: float | None = None
