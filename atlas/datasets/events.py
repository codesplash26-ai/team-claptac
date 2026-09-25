"""One shape for a disaster that happened.  (Owner: Dinuka)

Sources disagree about everything (units, time zones, what "severity" means), so every adapter maps
its records to ``HazardEvent`` and keeps the source's own numbers in ``detail``. These records are
history: they feed the timeline and map in the UI, the ground truth the evaluation scores against, and
the seeds for scenarios. They are NOT live sensor input; see ``atlas/datasets/base.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Hazard = Literal[
    "flood",
    "landslide",
    "cyclone",
    "earthquake",
    "tsunami",
    "drought",
    "wildfire",
    "volcano",
    "other",
]
HAZARDS: tuple[str, ...] = (
    "flood",
    "landslide",
    "cyclone",
    "earthquake",
    "tsunami",
    "drought",
    "wildfire",
    "volcano",
    "other",
)


class HazardEvent(BaseModel):
    id: str  # "<source>:<source id>", stable across fetches
    hazard: Hazard
    source: str  # adapter key, e.g. "usgs"
    name: str
    start: datetime
    end: datetime | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    country: str | None = None
    # One number a person can compare within a hazard, in ``severity_unit``: magnitude for a quake,
    # peak wind (knots) for a cyclone, run-up height (m) for a tsunami, alert score for GDACS, ...
    severity: float | None = None
    severity_unit: str = ""
    severity_label: str = ""  # the source's own words ("Orange", "Category 2")
    url: str | None = None
    detail: dict = Field(default_factory=dict)  # source-specific fields, untouched

    @field_validator("start", "end")
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("event times must include a timezone (use UTC)")
        return value


class Region(BaseModel):
    """Where we care about. Adapters that filter spatially use ``bbox`` or the circle."""

    name: str
    lat: float
    lon: float
    radius_km: float
    bbox: tuple[float, float, float, float]  # min_lat, min_lon, max_lat, max_lon


# Sri Lanka and its near waters: the box catches offshore quakes and the circle catches cyclones
# passing close enough to matter.
SRI_LANKA = Region(
    name="Sri Lanka", lat=7.87, lon=80.77, radius_km=600, bbox=(5.5, 79.0, 10.2, 82.5)
)

# Sri Lanka's real earthquake exposure is distant: large quakes along the Sumatra and Andaman
# subduction zones that send tsunamis across the ocean (2004, 2012). The box covers the northern Indian
# Ocean; use it with a high magnitude cut-off.
INDIAN_OCEAN = Region(
    name="Northern Indian Ocean", lat=5.0, lon=88.0, radius_km=4000, bbox=(-10.0, 65.0, 25.0, 105.0)
)
