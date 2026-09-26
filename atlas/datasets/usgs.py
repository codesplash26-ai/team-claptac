"""USGS earthquake catalogue.  (Owner: Dinuka)

FDSN event service: bounding box, magnitude and time filters, GeoJSON out, at most 20000 results per
query (a wider request than that is split by year here). Verified against the live service.

Sri Lanka is low-seismic: the catalogue holds no M3.5+ quakes inside the country's own box since 1990.
Its real earthquake exposure is distant, along the Sumatra and Andaman subduction zones that produced
the 2004 and 2012 tsunamis, so use ``INDIAN_OCEAN`` with a high magnitude cut-off. USGS's ``tsunami`` flag
is set only for events with a US tsunami-centre message and was false for 2004, so it is kept in
``detail`` but should not be relied on.

Earthquakes are live input too: ``reports_for_areas`` turns a quake into one official report per ATLAS
area within range (attribute ``earthquake``, the magnitude in ``M``), which the Sentinel scores against
``quake_threshold_magnitude``. ``python -m atlas.datasets.usgs --watch`` polls the live feed and posts
new quakes to a running ATLAS (``/ingest/report``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from atlas.datasets.base import SourceInfo
from atlas.datasets.events import SRI_LANKA, HazardEvent, Region
from atlas.datasets.http import fetch_json
from atlas.ingest.feeds import FeedReport
from atlas.tools.geo import haversine_m

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

    def events(
        self, start: datetime, end: datetime, *, offline: bool = False, fresh_s: int = 6 * 3600
    ) -> list[HazardEvent]:
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
                max_age_s=None if chunk_end < datetime.now(timezone.utc) else fresh_s,
            )
            found.extend(parse(data))
        return sorted({e.id: e for e in found}.values(), key=lambda e: e.start)


def reports_for_areas(
    events: list[HazardEvent], areas=None, *, radius_km: float = 100.0
) -> list[FeedReport]:
    """One official report per (quake, ATLAS area within ``radius_km`` of the epicentre).

    The value is the magnitude, unadjusted: the Sentinel decides whether it matters. The radius keeps
    distant quakes (Sumatra, Andaman) out of the live loop; they reach Sri Lanka as tsunami, not
    shaking, and a tsunami has no live rule yet.
    """
    from atlas.datasets.areas import AREAS

    out = []
    for quake in events:
        if quake.hazard != "earthquake" or quake.lat is None or quake.lon is None:
            continue
        for area in (areas or AREAS).values():
            km = float(haversine_m(quake.lat, quake.lon, area.lat, area.lon)) / 1000
            if km > radius_km:
                continue
            out.append(
                FeedReport(
                    report_id=f"{quake.id}:{area.area_id}",
                    source="usgs",
                    kind="official_warning",
                    area_id=area.area_id,
                    timestamp=quake.start,
                    attribute="earthquake",
                    value=max(0.0, quake.severity or 0.0),
                    unit="M",
                    text=f"{quake.name}; epicentre {km:.0f} km from {area.area_id}.",
                )
            )
    return out


def watch(
    atlas_url: str, *, every_s: int = 120, radius_km: float = 100.0, once: bool = False
) -> int:
    """Poll USGS for recent quakes near Sri Lanka and post each new one to ATLAS."""
    seen: set[str] = set()
    source = Usgs(min_magnitude=3.0)
    while True:
        now = datetime.now(timezone.utc)
        try:
            quakes = source.events(now - timedelta(hours=2), now, fresh_s=60)
        except Exception as exc:  # the feed being down must not stop the watcher
            print(f"USGS unavailable: {exc}", file=sys.stderr)
            quakes = []
        for report in reports_for_areas(quakes, radius_km=radius_km):
            if report.report_id in seen:
                continue
            body = json.dumps(report.model_dump(mode="json")).encode()
            request = urllib.request.Request(
                atlas_url.rstrip("/") + "/ingest/report",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                print(f"posted {report.report_id}: M{report.value} ({response.status})")
            seen.add(report.report_id)
        if once:
            return 0
        time.sleep(every_s)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Send live USGS earthquakes near Sri Lanka to ATLAS."
    )
    parser.add_argument("--watch", action="store_true", help="poll the live feed")
    parser.add_argument("--atlas", default="http://127.0.0.1:8000", help="the running ATLAS API")
    parser.add_argument("--every", type=int, default=120, help="seconds between polls")
    parser.add_argument("--radius-km", type=float, default=100.0)
    parser.add_argument("--once", action="store_true", help="poll once and exit")
    args = parser.parse_args(argv)
    if not args.watch:
        parser.print_help()
        return 2
    return watch(args.atlas, every_s=args.every, radius_km=args.radius_km, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
