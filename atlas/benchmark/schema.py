from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas.agents.a8_dispatch import MissionStatus
from atlas.control.models import ApprovalStatus, DecisionStatus
from atlas.core.events import EventType

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "data/benchmarks/tasks"
Slug = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")]
METRICS = sorted(
    {
        *(f"events.{e.value}" for e in EventType),
        *(f"missions.{s.value}" for s in MissionStatus),
        *(f"actions.{s.value}" for s in DecisionStatus),
        *(f"approvals.{s.value}" for s in ApprovalStatus),
        "inputs.total",
        "inputs.duplicates",
        "inputs.random",
        "missions.total",
        "actions.total",
        "actions.escalated_total",
        "incidents.total",
        "incidents.replans",
        "shelters.unplaced",
        "shelters.over_capacity",
        "logistics.unserved",
        "roads.closed",
        "hazards.landslide_updates",
        "reasoning.planner_runs",
        "reasoning.advice",
        "reasoning.accepted_claims",
        "reasoning.rejected_claims",
        "integrity.ledger_valid",
        "integrity.execution_errors",
        "integrity.unsafe_executions",
        "timing.execution_p95_ms",
    }
)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class StepBase(Strict):
    id: Slug
    at_seconds: float = Field(ge=0, le=7200)
    note: str = Field(default="", max_length=500)


class SensorStep(StepBase):
    kind: Literal["sensor"] = "sensor"
    sensor_type: Literal["water_level", "rainfall", "soil_moisture", "temperature"] = "water_level"
    value: float = Field(ge=0, le=10000)
    node_id: str = Field(default="rtp-node-01", min_length=1, max_length=100)
    observed_seconds: float | None = Field(default=None, ge=-7200, le=7200)

    @model_validator(mode="after")
    def percentage(self):
        if self.sensor_type == "soil_moisture" and self.value > 100:
            raise ValueError("soil moisture must be 0..100")
        return self


class ReportStep(StepBase):
    kind: Literal["report"] = "report"
    attribute: Literal["water_level", "rainfall", "soil_moisture"] = "water_level"
    value: float = Field(ge=0, le=10000)
    source: str = Field(default="feed:simulation", min_length=1, max_length=100)
    report_kind: Literal["official_warning", "field_report"] = "field_report"
    text: str = Field(default="Simulated field observation", max_length=280)
    observed_seconds: float | None = Field(default=None, ge=-7200, le=7200)


class DuplicateStep(StepBase):
    kind: Literal["duplicate"] = "duplicate"
    target: Slug


class MissionStep(StepBase):
    kind: Literal["mission"] = "mission"
    change: Literal["acknowledge", "progress", "complete", "fail"]
    limit: int = Field(default=100, ge=1, le=100)
    percent: int = Field(default=50, ge=0, le=100)


class ReviewStep(StepBase):
    kind: Literal["review"] = "review"
    decision: Literal["approve", "reject"]
    limit: int = Field(default=100, ge=1, le=100)


class CheckpointStep(StepBase):
    kind: Literal["checkpoint"] = "checkpoint"


Step = Annotated[
    SensorStep | ReportStep | DuplicateStep | MissionStep | ReviewStep | CheckpointStep,
    Field(discriminator="kind"),
]


class RandomEvents(Strict):
    count: int = Field(default=4, ge=0, le=30)
    kinds: list[Literal["ambient_sensor", "late_reading", "duplicate", "field_report"]] = Field(
        default_factory=lambda: ["ambient_sensor", "duplicate"], min_length=1
    )
    start_seconds: int = Field(default=10, ge=0, le=7200)
    end_seconds: int = Field(default=450, ge=0, le=7200)

    @model_validator(mode="after")
    def ordered(self):
        if self.start_seconds > self.end_seconds:
            raise ValueError("random event window must be ordered")
        return self


class InitialState(Strict):
    shelter_occupancy_fraction: float = Field(default=0, ge=0, le=1)
    available_resource_fraction: float = Field(default=1, ge=0, le=1)
    strict_mission_approval: bool = False
    auto_review: Literal["approve", "reject", "ignore"] = "approve"


class Expectation(Strict):
    metric: str = Field(json_schema_extra={"enum": METRICS})
    operator: Literal["eq", "gte", "lte"]
    value: float
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("metric")
    @classmethod
    def known_metric(cls, value):
        if value not in METRICS:
            raise ValueError(f"Unknown metric: {value}")
        return value


class Task(Strict):
    schema_version: Literal[1] = 1
    id: Slug
    title: str = Field(min_length=1, max_length=140)
    description: str = Field(min_length=1, max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=12)
    site: Literal["ratnapura"] = "ratnapura"
    area_id: Literal["ratnapura-demo-river"] = "ratnapura-demo-river"
    assumptions: list[str] = Field(min_length=1)
    initial: InitialState = Field(default_factory=InitialState)
    random_events: RandomEvents = Field(default_factory=RandomEvents)
    steps: list[Step] = Field(min_length=1, max_length=200)
    expectations: list[Expectation] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def references_and_order(self):
        seen, previous = {}, -1
        for step in self.steps:
            if step.id in seen or step.id.startswith("random-"):
                raise ValueError("step ids must be unique; random- is reserved")
            if step.at_seconds < previous:
                raise ValueError("steps must be ordered by receive time")
            if isinstance(step, DuplicateStep) and not isinstance(
                seen.get(step.target), (SensorStep, ReportStep)
            ):
                raise ValueError("duplicates must reference an earlier sensor or report step")
            seen[step.id], previous = step, step.at_seconds
        return self


def load_tasks(directory=TASKS):
    tasks = [
        Task.model_validate_json(p.read_text()) for p in sorted(Path(directory).glob("*.json"))
    ]
    if not tasks or len({t.id for t in tasks}) != len(tasks):
        raise ValueError("Tasks directory must contain tasks with unique ids")
    return tasks
