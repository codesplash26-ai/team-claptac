"""Control-boundary value types. Everything here is JSON-safe (ledger payloads use it)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from atlas.core.events import Consequence, Event


class Verdict(str, Enum):
    ALLOW = "allow"
    ESCALATE = "escalate"
    BLOCK = "block"


class PolicyVerdict(BaseModel):
    verdict: Verdict
    reason: str  # machine-readable code, e.g. "action_not_permitted_for_executor"
    consequence: Consequence | None = None


class DecisionStatus(str, Enum):
    BLOCKED = "blocked"
    ESCALATED = "escalated"  # waiting for the Human Incident Commander
    REJECTED = "rejected"  # human said no
    AUTHORISED = "authorised"  # cleared, not yet executed
    EXECUTING = "executing"
    EXECUTED = "executed"
    COMPENSATED = "compensated"  # execution failed and was rolled back
    FAILED_UNCOMPENSATED = "failed_uncompensated"  # needs a human


class Decision(BaseModel):
    action_id: str
    idempotency_key: str
    incident_id: str
    status: DecisionStatus
    reason: str
    consequence: Consequence | None = None
    approval_id: str | None = None
    decided_at: datetime
    updated_at: datetime


class Receipt(BaseModel):
    ref: str  # executor-side reference, e.g. a mission id
    executed_at: datetime
    detail: dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    receipt: Receipt
    events: list[Event] = Field(default_factory=list)


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    EDITED = "edited"  # approved with changed params
    REJECTED = "rejected"


class ApprovalItem(BaseModel):
    id: str
    idempotency_key: str
    incident_id: str
    action: dict[str, Any]
    reason: str
    status: ApprovalStatus
    created_at: datetime
    resolved_at: datetime | None = None
    reviewer: str | None = None
    note: str = ""
