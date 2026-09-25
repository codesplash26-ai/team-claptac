"""The two kinds of data source.  (Owner: Dinuka)

* ``SourceAdapter``: gives *readings* (numbers over time at a place) that ATLAS's sensors could have
  produced: river level, rainfall, soil moisture. They become ``SensorReading``s and replay through the
  normal pipeline, so a real flood exercises exactly the code a live sensor does.
* ``EventSource``: gives *events* (a quake, a cyclone, a tsunami, a GDACS alert). History for the
  timeline, ground truth for evaluation, and seeds for scenarios.

Both carry a ``SourceInfo`` saying what it is, its licence, and whether anyone has checked it against
the real service. Adapters never hide a failure: a source that cannot be reached raises ``DatasetError``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from atlas.datasets.events import HazardEvent
from atlas.ingest.sensors import SensorReading


class SourceInfo(BaseModel):
    """What a person needs to know before trusting or citing the data."""

    key: str  # "lk_irrigation"
    title: str
    url: str
    licence: str
    hazards: list[str]  # which hazards it can drive: flood, landslide, earthquake, cyclone
    needs_key: bool = False
    verified: bool = False  # someone has fetched real data and checked the format
    notes: str = ""


class SourceAdapter(Protocol):
    info: SourceInfo

    def readings(
        self, area_id: str, start: datetime, end: datetime, *, offline: bool = False
    ) -> list[SensorReading]:
        """Normalised readings for the window, oldest first. With ``offline=True`` use the cache or a
        committed fixture only and never touch the network."""


class EventSource(Protocol):
    info: SourceInfo

    def events(self, start: datetime, end: datetime, *, offline: bool = False) -> list[HazardEvent]:
        """Events in the window for the region this adapter is configured for, oldest first."""
