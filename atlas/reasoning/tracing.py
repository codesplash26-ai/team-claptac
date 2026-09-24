"""Persist internal search traces separately from factual action events."""

from atlas.core.events import Event, EventType


def record_result(ledger, result, incident_id=None):
    ledger.append("reasoning:" + result.id, "reasoning", result.model_dump(mode="json"))
    return Event(
        type=EventType.REASONING_COMPLETED,
        source=result.agent,
        incident_id=incident_id,
        payload={
            "reasoning_id": result.id,
            "snapshot_version": result.snapshot_version,
            "action": result.action.model_dump(mode="json") if result.action else None,
            "fallback_used": result.fallback_used,
            "stop_reason": result.stop_reason,
            "evidence_ids": result.evidence_ids,
        },
    )
