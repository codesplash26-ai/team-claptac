"""The message log as data.  (Owner: Dinuka)

Each ``TraceEvent`` is one bus event with: its sequence number, type, sender, incident, a one-line
human summary, which subscribers handled it, and the event that caused it (when known). ``flow``
aggregates the same log into sender -> receiver counts for the agent-map diagram. All of it is
derived from the ledger and the bus's delivery records; nothing is stored separately.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TraceEvent(BaseModel):
    seq: int
    id: str
    type: str
    source: str
    incident_id: str | None = None
    created_at: datetime
    summary: str  # e.g. "A3 asks A4 to plan routes from (6.68, 80.40)"
    handled_by: list[str] = Field(default_factory=list)
    caused_by: str | None = None
    payload: dict = Field(default_factory=dict)


class FlowEdge(BaseModel):
    source: str
    target: str
    event_type: str
    count: int


def trace(runtime, *, after_seq: int = 0, limit: int = 200, incident_id: str | None = None):
    raise NotImplementedError("Dinuka: read events, deliveries and causes into TraceEvents")


def flow(runtime, *, incident_id: str | None = None) -> list[FlowEdge]:
    raise NotImplementedError("Dinuka: aggregate sender -> receiver counts")


def summarise(event: dict) -> str:
    raise NotImplementedError("Dinuka: one readable line per event type")
