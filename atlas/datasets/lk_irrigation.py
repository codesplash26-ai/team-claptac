"""Sri Lanka river gauges, via nuuuwan/lk_irrigation.  (Owner: Dinuka)

A community project that scrapes the Irrigation Department's dashboard every hour into a Git
repository (MIT): about 40 stations with the department's own alert, minor-flood and major-flood levels.
Verified against the live repository. History starts in November 2025 (which covers the Cyclone Ditwah
floods) and continues to today.

Layout: ``data/rwlds/<basin>-basin/<river>-river/<station>-station/<yyyy>/<yyyy-mm>/<yyyy-mm-dd>/
<yyyy-mm-dd-HH-MM-SS>.json`` holding ``{"station_name", "time_ut", "water_level_m"}``. To list a
station's files we ask GitHub for the station folder's tree once (two API calls), then fetch each
reading from ``raw.githubusercontent.com`` and cache it. Set ``GITHUB_TOKEN`` to lift the unauthenticated
GitHub API limit (60 requests an hour).

UNITS. Gauges report metres; the Sentinel's tabletop scale is centimetres with an alarm at 25 cm.
``to_sensor_cm`` converts with the same scale ``data/reference/sensor_map.yaml`` uses (0.20 m of river
per sensor cm), so the department's *alert level* at Ratnapura (5.2 m) lands just above the Sentinel's
alarm (26 cm), minor flood at 37.5 cm and major flood at 47.5 cm. ``levels`` returns the untouched metres
for anything that needs them, and ``Station.stage`` classifies a level with the department's own bands.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from pydantic import BaseModel

from atlas.datasets.areas import AREAS
from atlas.datasets.base import SourceInfo
from atlas.datasets.http import DatasetError, fetch, fetch_json
from atlas.ingest.sensors import SensorReading

RAW = "https://raw.githubusercontent.com/nuuuwan/lk_irrigation/main/"
API = "https://api.github.com/repos/nuuuwan/lk_irrigation/"
METRES_PER_CM = 0.20  # keep equal to data/reference/sensor_map.yaml
STAGE_ORDER = ("normal", "alert", "minor_flood", "major_flood")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class Station(BaseModel):
    name: str
    river: str
    basin: str
    lat: float
    lon: float
    alert_m: float
    minor_flood_m: float
    major_flood_m: float

    @property
    def path(self) -> str:
        return f"data/rwlds/{slug(self.basin)}-basin/{slug(self.river)}-river/{slug(self.name)}-station"

    def stage(self, level_m: float) -> str:
        if level_m >= self.major_flood_m:
            return "major_flood"
        if level_m >= self.minor_flood_m:
            return "minor_flood"
        return "alert" if level_m >= self.alert_m else "normal"


def to_sensor_cm(level_m: float) -> float:
    """River level in metres to the Sentinel's tabletop centimetres (see the module docstring)."""
    return round(level_m / METRES_PER_CM, 2)


def _api_headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


class LkIrrigation:
    info = SourceInfo(
        key="lk_irrigation",
        title="Sri Lanka river water levels (Irrigation Dept, via nuuuwan/lk_irrigation)",
        url="https://github.com/nuuuwan/lk_irrigation",
        licence="MIT (data originates from the Irrigation Department dashboard)",
        hazards=["flood"],
        verified=True,
        notes="History from Nov 2025. Metres, converted to the Sentinel's cm scale; see docstring.",
    )

    def stations(self, *, offline: bool = False) -> dict[str, Station]:
        rivers = {
            r["name"]: r["basin_name"]
            for r in fetch_json(
                RAW + "data/static/rivers.json", offline=offline, max_age_s=7 * 24 * 3600
            )
        }
        out = {}
        for s in fetch_json(
            RAW + "data/static/stations.json", offline=offline, max_age_s=7 * 24 * 3600
        ):
            basin = rivers.get(s["river_name"])
            if basin is None:
                continue
            out[s["name"]] = Station(
                name=s["name"],
                river=s["river_name"],
                basin=basin,
                lat=s["lat_lng"][0],
                lon=s["lat_lng"][1],
                alert_m=s["alert_level_m"],
                minor_flood_m=s["minor_flood_level_m"],
                major_flood_m=s["major_flood_level_m"],
            )
        return out

    def _files(self, station: Station, *, offline: bool) -> list[tuple[datetime, str]]:
        """(time, repo path) for every reading the station has, oldest first."""
        parent, _, leaf = station.path.rpartition("/")
        listing = fetch_json(
            f"{API}contents/{parent}", offline=offline, max_age_s=6 * 3600, headers=_api_headers()
        )
        sha = next((x["sha"] for x in listing if x["name"] == leaf), None)
        if sha is None:
            raise DatasetError(f"station folder not found in the repository: {station.path}")
        tree = fetch_json(
            f"{API}git/trees/{sha}",
            params={"recursive": 1},
            offline=offline,
            max_age_s=6 * 3600,
            headers=_api_headers(),
        )
        files = []
        for node in tree.get("tree", []):
            match = re.search(
                r"(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})\.json$", node["path"]
            )
            if node["type"] == "blob" and match:
                when = datetime(*map(int, match.groups()), tzinfo=timezone.utc)
                files.append((when, f"{station.path}/{node['path']}"))
        return sorted(files)

    def levels(
        self, station: Station, start: datetime, end: datetime, *, offline: bool = False
    ) -> list[tuple[datetime, float]]:
        """(time, level in metres) for the window, oldest first."""
        wanted = [(t, p) for t, p in self._files(station, offline=offline) if start <= t <= end]
        if not wanted:
            return []

        def one(item):
            body = fetch(RAW + item[1], offline=offline, max_age_s=None)
            data = json.loads(body)
            return datetime.fromtimestamp(data["time_ut"], timezone.utc), float(
                data["water_level_m"]
            )

        with ThreadPoolExecutor(max_workers=12) as pool:
            return sorted(pool.map(one, wanted))

    def readings(
        self, area_id: str, start: datetime, end: datetime, *, offline: bool = False
    ) -> list[SensorReading]:
        area = AREAS.get(area_id)
        if area is None or not area.gauge_station:
            raise DatasetError(
                f"no gauge is registered for area {area_id!r} (atlas/datasets/areas.py)"
            )
        stations = self.stations(offline=offline)
        station = stations.get(area.gauge_station)
        if station is None:
            raise DatasetError(f"unknown station {area.gauge_station!r}")
        out = []
        for when, level in self.levels(station, start, end, offline=offline):
            out.append(
                SensorReading(
                    node_id=f"lk-irrigation/{slug(station.name)}",
                    sensor_id="water-level",
                    type="water_level",
                    value=to_sensor_cm(level),
                    unit="cm",
                    timestamp=when,
                    measurement_id=f"lkirr-{slug(station.name)}-{int(when.timestamp())}",
                    location={"lat": area.lat, "lon": area.lon, "area_id": area_id},
                )
            )
        return out
