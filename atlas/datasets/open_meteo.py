"""Open-Meteo: hourly rainfall, soil moisture and wind (ERA5), and GloFAS river discharge.  (Owner: Dinuka)

Verified against the live APIs. Free for non-commercial use, no key. Reanalysis, not observation: about
25 km (ERA5) and 11 km (ERA5-Land) cells, so a cell average stands in for the point, and rain in
mountains is smoothed. Good for what happened when and how much, not for a single street.

* ``readings`` gives hourly rainfall (mm) and soil moisture as ``SensorReading``s for an area.
  Soil moisture is volumetric (m3/m3); the Sentinel wants percent of saturation, so the value is divided
  by ``SATURATION_M3M3`` (an assumption: about 0.5 for the loams around Ratnapura, capped at 100%).
* ``series`` returns any hourly or daily variable untouched, for charts, the cyclone wind, and the
  discharge that the GloFAS flood model gives (the Flood API documentation mentions history only to
  2022, so check the range you need).
"""

from __future__ import annotations

from datetime import datetime, timezone

from atlas.datasets.areas import AREAS
from atlas.datasets.base import SourceInfo
from atlas.datasets.http import DatasetError, fetch_json
from atlas.ingest.sensors import SensorReading

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FLOOD = "https://flood-api.open-meteo.com/v1/flood"
SATURATION_M3M3 = 0.50


def _parse_times(block: dict) -> list[datetime]:
    return [datetime.fromisoformat(t).replace(tzinfo=timezone.utc) for t in block["time"]]


class OpenMeteo:
    info = SourceInfo(
        key="open_meteo",
        title="Open-Meteo historical weather (ERA5) and flood (GloFAS) APIs",
        url="https://open-meteo.com/en/docs/historical-weather-api",
        licence="Free for non-commercial use; attribution required (CC BY 4.0 data)",
        hazards=["flood", "landslide", "cyclone", "drought"],
        verified=True,
        notes="Reanalysis at 11-25 km, not point observations. Flood API history range unchecked.",
    )

    def series(
        self,
        lat: float,
        lon: float,
        start: datetime,
        end: datetime,
        variables: list[str],
        *,
        hourly: bool = True,
        flood: bool = False,
        offline: bool = False,
    ) -> dict[str, list[tuple[datetime, float | None]]]:
        key = "hourly" if hourly else "daily"
        params = {
            "latitude": lat,
            "longitude": lon,
            "timezone": "UTC",
            "start_date": start.strftime("%Y-%m-%d"),
            "end_date": end.strftime("%Y-%m-%d"),
            key: ",".join(variables),
        }
        data = fetch_json(
            FLOOD if flood else ARCHIVE,
            params=params,
            offline=offline,
            max_age_s=None if end < datetime.now(timezone.utc) else 3 * 3600,
        )
        if key not in data:
            raise DatasetError(f"Open-Meteo returned no {key} data: {str(data)[:200]}")
        times = _parse_times(data[key])
        return {v: list(zip(times, data[key][v])) for v in variables}

    def readings(
        self, area_id: str, start: datetime, end: datetime, *, offline: bool = False
    ) -> list[SensorReading]:
        area = AREAS.get(area_id)
        if area is None:
            raise DatasetError(f"unknown area {area_id!r} (atlas/datasets/areas.py)")
        got = self.series(
            area.lat,
            area.lon,
            start,
            end,
            ["precipitation", "soil_moisture_0_to_7cm"],
            offline=offline,
        )
        location = {"lat": area.lat, "lon": area.lon, "area_id": area_id}
        node = "open-meteo/era5"
        out = []
        for when, mm in got["precipitation"]:
            if mm is not None and start <= when <= end:
                out.append(
                    SensorReading(
                        node_id=node,
                        sensor_id="rain",
                        type="rainfall",
                        value=max(0.0, mm),
                        unit="mm",
                        timestamp=when,
                        location=location,
                        measurement_id=f"om-rain-{area_id}-{int(when.timestamp())}",
                    )
                )
        for when, theta in got["soil_moisture_0_to_7cm"]:
            if theta is not None and start <= when <= end:
                percent = min(100.0, max(0.0, theta / SATURATION_M3M3 * 100))
                out.append(
                    SensorReading(
                        node_id=node,
                        sensor_id="soil",
                        type="soil_moisture",
                        value=round(percent, 1),
                        unit="%",
                        timestamp=when,
                        location=location,
                        measurement_id=f"om-soil-{area_id}-{int(when.timestamp())}",
                    )
                )
        return sorted(out, key=lambda r: (r.timestamp, r.type.value))
