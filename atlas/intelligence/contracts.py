"""What the intelligence layer produces.  (Owner: Aeshan; Dinuka's UI reads these)

Published to the bus as ``REASONING_COMPLETED`` events (the type already exists) with
``Advice.model_dump(mode="json")`` as the payload, and stored so the dashboard can show, for every
incident, what the system thinks, why, and which statements were rejected.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from atlas.knowledge.ontology import Claim, Violation


class GroundedClaim(BaseModel):
    """A statement the layer wants to make. ``text`` is what a person reads; ``claim`` is the
    checkable form. Only ``accepted`` claims may be shown as fact."""

    text: str
    claim: Claim
    accepted: bool = False
    violations: list[Violation] = Field(default_factory=list)


class ReasoningStep(BaseModel):
    kind: Literal["observation", "tool_call", "inference", "check", "question"]
    text: str
    refs: list[str] = Field(default_factory=list)  # event ids, plan ids, entity ids


class ReasoningTrace(BaseModel):
    steps: list[ReasoningStep] = Field(default_factory=list)
    model: str | None = None  # None: produced without an LLM
    latency_ms: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class Advice(BaseModel):
    incident_id: str
    cycle: int
    created_at: datetime
    summary: str  # plain-language situation and plan
    claims: list[GroundedClaim] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)  # things a human should look at
    questions: list[str] = Field(default_factory=list)  # what is unknown that would change the plan
    recommend_replan: bool = False
    trace: ReasoningTrace = Field(default_factory=ReasoningTrace)
    source: Literal["llm", "template"] = "template"  # "template": deterministic fallback
