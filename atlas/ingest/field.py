"""Messages from people in the field (LoRa, a responder app, SMS).  (Owner: Hasindu)

Two things that are not sensor readings:

* ``FieldReport``: a responder or resident says what they see or need. This is the input A5 (triage)
  has a seam for (``Triage.record_need``) and A2 can use as evidence.
* ``MissionAck``: a team acknowledges a mission, reports progress or an exception. This maps onto A8's
  field-update methods (``acknowledge``, ``report_progress``, ``report_exception``, ``complete``).

OPEN DECISIONS (settle with Dinuka before wiring, see docs/contracts.md):
  1. Do these become new EventTypes (needs the whole team's approval, ``core/events.py``), or do the API
     routes call A5/A8 directly the way ``/missions/{id}/acknowledge`` already does?
  2. How does a LoRa node learn which mission id it is acknowledging? (A8 mission ids are derived from
     the action's idempotency key; see ``atlas.agents.a8_dispatch.mission_id``.)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class _Stamped(BaseModel):
    node_id: str = Field(min_length=1)
    timestamp: datetime  # when it was received (or, if the sender has a clock, when it happened)
    via: Literal["lora", "sms", "app", "http"] = "http"
    rssi_dbm: float | None = None  # link quality, for the dashboard

    @field_validator("timestamp")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        return value


class FieldReport(_Stamped):
    report_id: str = Field(min_length=1)  # idempotency: a resent frame must not count twice
    area_id: str = Field(min_length=1)
    kind: Literal["need", "hazard", "road", "all_clear"]
    people: int = Field(default=0, ge=0)
    urgency: float | None = Field(default=None, ge=0, le=1)
    text: str = Field(default="", max_length=280)  # short: it may have crossed a radio


class MissionAck(_Stamped):
    mission_id: str = Field(min_length=1)
    change: Literal["acknowledged", "progress", "exception", "completed"]
    percent: int | None = Field(default=None, ge=0, le=100)
    note: str = Field(default="", max_length=160)
