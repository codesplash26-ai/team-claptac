"""NASA COOLR landslide catalogue.  (Owner: Dinuka)

NOT VERIFIED. From this network the NASA ArcGIS servers timed out or returned 404 for the endpoints
found in NASA's documentation, so this adapter is written to the standard ArcGIS FeatureServer
``query`` JSON and tested only against a hand-written sample of that format. Before relying on it:
find a working endpoint (see ``ENDPOINTS``), fetch a real page, and compare it with ``parse``.

Events are dated and located landslides, not measurements. Use them as ground truth to score landslide
warnings against and as scenario seeds.
"""

from __future__ import annotations

from datetime import datetime, timezone

from atlas.datasets.base import SourceInfo
from atlas.datasets.events import SRI_LANKA, HazardEvent, Region
from atlas.datasets.http import DatasetError, fetch_json

ENDPOINTS = [  # candidates from NASA's documentation; none confirmed reachable from our network
    "https://maps.nccs.nasa.gov/mapping/rest/services/COOLR/COOLR_Events_Point/FeatureServer/0/query",
    "https://maps.nccs.nasa.gov/mapping/rest/services/landslide_viewer/Landslide_Points/FeatureServer/0/query",
]


def parse(collection: dict) -> list[HazardEvent]:
    """Standard ArcGIS ``f=json`` response: ``features[].attributes`` and ``geometry {x, y}``."""
    out = []
    for feature in collection.get("features", []):
        a = feature.get("attributes") or {}
        g = feature.get("geometry") or {}
        stamp = a.get("event_date") or a.get("Date") or a.get("date")
        if stamp is None or g.get("x") is None:
            continue
        when = (
            datetime.fromtimestamp(stamp / 1000, timezone.utc)
            if isinstance(stamp, (int, float))
            else datetime.fromisoformat(str(stamp)).replace(tzinfo=timezone.utc)
        )
        ident = a.get("event_id") or a.get("OBJECTID") or a.get("id")
        out.append(
            HazardEvent(
                id=f"coolr:{ident}",
                hazard="landslide",
                source="coolr",
                name=a.get("event_title") or a.get("Title") or "landslide",
                start=when,
                lat=g["y"],
                lon=g["x"],
                country=a.get("country_name") or a.get("Country"),
                severity=float(a["fatality_count"])
                if a.get("fatality_count") is not None
                else None,
                severity_unit="fatalities",
                detail={
                    "trigger": a.get("landslide_trigger"),
                    "size": a.get("landslide_size"),
                    "raw": a,
                },
            )
        )
    return out


class Coolr:
    info = SourceInfo(
        key="coolr",
        title="NASA Cooperative Open Online Landslide Repository",
        url="https://gpm.nasa.gov/landslides/data.html",
        licence="Open data, CC-BY attribution requested",
        hazards=["landslide"],
        verified=False,
        notes="Endpoints unreachable from our network when checked; parser tested on a sample only.",
    )

    def __init__(self, region: Region = SRI_LANKA):
        self.region = region

    def events(self, start: datetime, end: datetime, *, offline: bool = False) -> list[HazardEvent]:
        lat0, lon0, lat1, lon1 = self.region.bbox
        params = {
            "where": "1=1",
            "geometry": f"{lon0},{lat0},{lon1},{lat1}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "true",
            "f": "json",
        }
        last: Exception | None = None
        for endpoint in ENDPOINTS:
            try:
                data = fetch_json(
                    endpoint, params=params, offline=offline, max_age_s=30 * 24 * 3600
                )
                return sorted(
                    (e for e in parse(data) if start <= e.start <= end), key=lambda e: e.start
                )
            except DatasetError as exc:
                last = exc
        raise DatasetError(f"no COOLR endpoint reachable: {last}")
