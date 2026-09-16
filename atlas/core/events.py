"""Typed event contracts shared by every agent.

Agents communicate only by publishing/consuming these events on the bus and
reading/writing the Common Operating Picture. Change these with care: every
owner depends on them. Open a PR and tag the whole team.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EventType(str, Enum):
    OBSERVATION = "observation"            # A1 -> COP
    TRIGGER = "trigger"                    # A1 -> A3
    CONFLICT_FLAG = "conflict_flag"        # A1 -> A3
    RISK_UPDATE = "risk_update"            # A2 -> COP / A5
    TASK_ASSIGNED = "task_assigned"        # A3 -> A4..A7
    CANDIDATE_PLAN = "candidate_plan"      # A4..A7 -> A3
    PLAN_PROPOSED = "plan_proposed"        # A3 -> Action Gateway
    ACTION_AUTHORISED = "action_authorised"  # Gateway -> A8 / A9
    ACTION_ESCALATED = "action_escalated"  # Gateway -> Human IC
    ACTION_BLOCKED = "action_blocked"      # Gateway -> ledger
    MISSION_STATUS = "mission_status"      # A8 -> A3
    ALERT_PUBLISHED = "alert_published"    # A9 -> ledger


class Event(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    type: EventType
    source: str                      # e.g. "a1_sentinel"
    incident_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
    payload: dict[str, Any] = Field(default_factory=dict)


class Belief(BaseModel):
    """A single belief in the COP (road edge, area, shelter, etc.)."""
    subject: str                     # e.g. "edge:123-456", "area:ratnapura-gn-12"
    attribute: str                   # e.g. "open", "flood_depth_m"
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=_now)
    ttl_seconds: int | None = None


class Consequence(str, Enum):
    LOW = "low"        # executes autonomously, logged
    HIGH = "high"      # requires Human Incident Commander


class ProposedAction(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    idempotency_key: str
    incident_id: str
    proposed_by: str                 # always "a3_incident_commander"
    executor: str                    # "a8_dispatch" | "a9_comms"
    action_type: str                 # must exist in config/policy.yaml
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
