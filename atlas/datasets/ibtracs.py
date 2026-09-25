"""Tropical cyclones: IBTrACS, North Indian basin.  (Owner: Dinuka)

NOAA's International Best Track Archive. The basin file is about 27 MB, downloaded once and cached; it
is read as a stream, so memory stays small. Verified against the live file.

One ``HazardEvent`` per storm that came within ``region.radius_km`` of the region centre: time of
closest approach, peak wind (knots) while inside that radius, lowest pressure, and the track points.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import datetime, timezone

from atlas.datasets.base import SourceInfo
from atlas.datasets.events import SRI_LANKA, HazardEvent, Region
from atlas.datasets.http import fetch

URL = (
    "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/"
    "v04r01/access/csv/ibtracs.NI.list.v04r01.csv"
)


def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    )
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _num(text: str | None) -> float | None:
    try:
        return float(text) if text and text.strip() else None
    except ValueError:
        return None


def category(knots: float | None) -> str:
    """Saffir-Simpson-style label from peak wind (knots); a rough guide, not the agency's own."""
    if knots is None:
        return ""
    for limit, label in (
        (137, "Cat 5"),
        (113, "Cat 4"),
        (96, "Cat 3"),
        (83, "Cat 2"),
        (64, "Cat 1"),
        (34, "Tropical storm"),
    ):
        if knots >= limit:
            return label
    return "Depression"


def parse(text: str, region: Region = SRI_LANKA) -> list[HazardEvent]:
    reader = csv.DictReader(io.StringIO(text))
    storms: dict[str, list[dict]] = {}
    for row in reader:
        if row.get("SID") in (None, "", " "):
            continue
        lat, lon = _num(row.get("LAT")), _num(row.get("LON"))
        if lat is None or lon is None or not row.get("ISO_TIME", "").strip():
            continue  # the units row and blank rows
        storms.setdefault(row["SID"], []).append(row)
    events = []
    for sid, rows in storms.items():
        inside = []
        for row in rows:
            lat, lon = float(row["LAT"]), float(row["LON"])
            distance = km_between(region.lat, region.lon, lat, lon)
            if distance <= region.radius_km:
                inside.append((distance, row))
        if not inside:
            continue
        closest = min(inside, key=lambda item: item[0])
        winds = [
            w
            for w in (_num(r.get("WMO_WIND")) or _num(r.get("USA_WIND")) for _, r in inside)
            if w is not None
        ]
        pressures = [
            p
            for p in (_num(r.get("WMO_PRES")) or _num(r.get("USA_PRES")) for _, r in inside)
            if p is not None
        ]
        peak = max(winds) if winds else None
        when = datetime.strptime(closest[1]["ISO_TIME"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        name = rows[0].get("NAME", "").strip()
        name = "unnamed" if name in ("", "NOT_NAMED") else name.title()
        events.append(
            HazardEvent(
                id=f"ibtracs:{sid}",
                hazard="cyclone",
                source="ibtracs",
                name=f"{name} ({rows[0]['SEASON']})",
                start=when,
                lat=float(closest[1]["LAT"]),
                lon=float(closest[1]["LON"]),
                severity=peak,
                severity_unit="knots",
                severity_label=category(peak),
                detail={
                    "closest_km": round(closest[0], 1),
                    "min_pressure_hpa": min(pressures) if pressures else None,
                    "season": rows[0]["SEASON"],
                    "track": [
                        [r["ISO_TIME"], float(r["LAT"]), float(r["LON"]), _num(r.get("WMO_WIND"))]
                        for r in rows
                    ],
                },
            )
        )
    return sorted(events, key=lambda e: e.start)


class Ibtracs:
    info = SourceInfo(
        key="ibtracs",
        title="IBTrACS v04r01, North Indian basin (NOAA NCEI)",
        url="https://www.ncei.noaa.gov/products/international-best-track-archive",
        licence="Public domain (NOAA); cite Knapp et al. 2010",
        hazards=["cyclone"],
        verified=True,
        notes="27 MB download, cached. Wind is the agency's sustained wind in knots.",
    )

    def __init__(self, region: Region = SRI_LANKA):
        self.region = region

    def events(self, start: datetime, end: datetime, *, offline: bool = False) -> list[HazardEvent]:
        body = fetch(URL, offline=offline, max_age_s=30 * 24 * 3600, timeout=300)
        found = parse(body.decode("utf-8", "replace"), self.region)
        return [e for e in found if start <= e.start <= end]
