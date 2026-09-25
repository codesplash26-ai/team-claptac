"""The interface every data source implements.  (Owner: Dinuka)"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

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
