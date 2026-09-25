"""USGS earthquake catalogue.  (Owner: Dinuka)

FDSN event service: bounding box, magnitude and time filters, GeoJSON out, at most 20000 results per
query (a wider request than that is split by year here). Verified against the live service.

Sri Lanka is low-seismic: the catalogue holds no M3.5+ quakes inside the country's own box since 1990.
Its real earthquake exposure is distant, along the Sumatra and Andaman subduction zones that produced
the 2004 and 2012 tsunamis, so use ``INDIAN_OCEAN`` with a high magnitude cut-off. USGS's ``tsunami`` flag
is set only for events with a US tsunami-centre message and was false for 2004, so it is kept in
``detail`` but should not be relied on. There is no ATLAS sensor type for earthquakes, so these are
history and ground truth, not live input (see ``docs/data-and-hazards.md``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from atlas.datasets.base import SourceInfo
from atlas.datasets.events import SRI_LANKA, HazardEvent, Region
from atlas.datasets.http import fetch_json

URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"


def parse(collection: dict) -> list[HazardEvent]:
    out = []
    for feature in collection.get("features", []):
        p = feature.get("properties") or {}
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None, None]
        if p.get("time") is None or p.get("mag") is None:
            continue
        out.append(
            HazardEvent(
                id=f"usgs:{feature['id']}",
                hazard="earthquake",
                source="usgs",
                name=p.get("title") or p.get("place") or "earthquake",
                start=datetime.fromtimestamp(p["time"] / 1000, timezone.utc),
                lat=coords[1],
                lon=coords[0],
                severity=float(p["mag"]),
                severity_unit=p.get("magType") or "M",
                severity_label=f"M{p['mag']}",
                url=p.get("url"),
                detail={
                    "depth_km": coords[2] if len(coords) > 2 else None,
                    "place": p.get("place"),
                    "tsunami_flag": bool(p.get("tsunami")),
                    "felt": p.get("felt"),
                    "alert": p.get("alert"),
                },
            )
        )
    return out


class Usgs:
    info = SourceInfo(
        key="usgs",
        title="USGS earthquake catalogue (FDSN event service)",
        url="https://earthquake.usgs.gov/fdsnws/event/1/",
        licence="US Government public domain",
        hazards=["earthquake", "tsunami"],
        verified=True,
        notes="Low seismicity around Sri Lanka; use for history and the tsunami flag.",
    )

    def __init__(self, region: Region = SRI_LANKA, min_magnitude: float = 3.5):
        self.region, self.min_magnitude = region, min_magnitude

    def events(self, start: datetime, end: datetime, *, offline: bool = False) -> list[HazardEvent]:
        lat0, lon0, lat1, lon1 = self.region.bbox
        found: list[HazardEvent] = []
        for year in range(start.year, end.year + 1, 10):  # decade chunks stay under the 20000 cap
            chunk_end = min(datetime(year + 10, 1, 1, tzinfo=timezone.utc), end)
            chunk_start = max(datetime(year, 1, 1, tzinfo=timezone.utc), start)
            data = fetch_json(
                URL,
                params={
                    "format": "geojson",
                    "minlatitude": lat0,
                    "minlongitude": lon0,
                    "maxlatitude": lat1,
                    "maxlongitude": lon1,
                    "minmagnitude": self.min_magnitude,
                    "starttime": chunk_start.strftime("%Y-%m-%d"),
                    "endtime": chunk_end.strftime("%Y-%m-%d"),
                    "orderby": "time-asc",
                    "limit": 20000,
                },
                offline=offline,
                max_age_s=None if chunk_end < datetime.now(timezone.utc) else 6 * 3600,
            )
            found.extend(parse(data))
        return sorted({e.id: e for e in found}.values(), key=lambda e: e.start)
