"""Open-Meteo: historical rainfall and soil moisture, and GloFAS river discharge.  (Owner: Dinuka)

Historical weather (1940 to now) is free for non-commercial use, no key. The Flood API is GloFAS-based
at about 5 km; its documentation mentions history only to 2022, so check the range before relying on
it. Resolution is coarse: the nearest river cell may not be the right river.
"""

from __future__ import annotations

from datetime import datetime

from atlas.datasets.base import SourceInfo
from atlas.ingest.sensors import SensorReading


class OpenMeteo:
    info = SourceInfo(
        key="open_meteo",
        title="Open-Meteo historical weather and flood (GloFAS) APIs",
        url="https://open-meteo.com/en/docs/historical-weather-api",
        licence="Free for non-commercial use; attribution required",
        hazards=["flood", "landslide", "cyclone"],
    )

    def readings(
        self, area_id: str, start: datetime, end: datetime, *, offline: bool = False
    ) -> list[SensorReading]:
        raise NotImplementedError("Dinuka: rainfall, soil moisture and discharge as SensorReadings")
