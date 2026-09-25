"""USGS earthquake catalogue.  (Owner: Dinuka)

FDSN event API: bounding box, magnitude and time filters, GeoJSON output, at most 20000 results per
query. Sri Lanka is low-seismic, so treat this as a secondary scenario. There is no ATLAS sensor type
for earthquakes yet: adding one changes ``SensorType`` in ``atlas/ingest/sensors.py`` and needs the
whole team's approval, so the first step is to agree how a quake becomes an observation.
"""

from __future__ import annotations

from datetime import datetime

from atlas.datasets.base import SourceInfo
from atlas.ingest.sensors import SensorReading


class Usgs:
    info = SourceInfo(
        key="usgs",
        title="USGS earthquake catalogue (FDSN event service)",
        url="https://earthquake.usgs.gov/fdsnws/event/1/",
        licence="US Government public domain",
        hazards=["earthquake"],
    )

    def readings(
        self, area_id: str, start: datetime, end: datetime, *, offline: bool = False
    ) -> list[SensorReading]:
        raise NotImplementedError("Dinuka: earthquakes as observations (needs a contract decision)")
