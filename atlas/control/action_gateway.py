"""Action Gateway stub for Workstream B until D ships the real gateway.

submit(ProposedAction) -> Decision. Auto-approves everything so A3 can integrate.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from atlas.core.events import Event, EventType, ProposedAction
from atlas.memory.ledger import ActionLedger, LedgerEntry


class DecisionStatus(str, Enum):
    AUTHORISED = "authorised"
    ESCALATED = "escalated"
    BLOCKED = "blocked"


class Decision(BaseModel):
    status: DecisionStatus
    action_id: str
    reason: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ActionGateway:
    """Permissive stub: authorise all proposed actions and write the ledger."""

    def __init__(self, ledger: ActionLedger | None = None) -> None:
        self.ledger = ledger or ActionLedger()

    def submit(self, action: ProposedAction) -> Decision:
        self.ledger.append(
            LedgerEntry(
                idempotency_key=action.idempotency_key,
                incident_id=action.incident_id,
                action_type=action.action_type,
                status="authorised",
                actor=action.proposed_by,
                payload=action.model_dump(mode="json"),
            )
        )
        return Decision(
            status=DecisionStatus.AUTHORISED,
            action_id=action.id,
            reason="stub gateway auto-approve",
        )

    def decision_events(self, action: ProposedAction, decision: Decision) -> list[Event]:
        if decision.status == DecisionStatus.AUTHORISED:
            et = EventType.ACTION_AUTHORISED
        elif decision.status == DecisionStatus.ESCALATED:
            et = EventType.ACTION_ESCALATED
        else:
            et = EventType.ACTION_BLOCKED
        return [
            Event(
                type=et,
                source="action_gateway",
                incident_id=action.incident_id,
                payload={
                    "action_id": action.id,
                    "action_type": action.action_type,
                    "executor": action.executor,
                    "decision": decision.model_dump(mode="json"),
                    "params": action.params,
                },
            )
        ]
