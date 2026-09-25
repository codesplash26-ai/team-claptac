"""NASA COOLR landslide catalogue.  (Owner: Dinuka)

Cooperative Open Online Landslide Repository: a global catalogue (CSV, shapefile or ArcGIS REST API),
CC-BY requested. Events are dated and located but not measurements, so they are best used as ground
truth to score the system's landslide warnings against, and as scenario seeds.
"""

from __future__ import annotations

from datetime import datetime

from atlas.datasets.base import SourceInfo
from atlas.ingest.sensors import SensorReading


class Coolr:
    info = SourceInfo(
        key="coolr",
        title="NASA Cooperative Open Online Landslide Repository",
        url="https://gpm.nasa.gov/landslides/data.html",
        licence="Open data, CC-BY attribution requested",
        hazards=["landslide"],
    )

    def readings(
        self, area_id: str, start: datetime, end: datetime, *, offline: bool = False
    ) -> list[SensorReading]:
        raise NotImplementedError("Dinuka: landslide events for scoring and scenario seeds")
