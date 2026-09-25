"""The hazard history of Sri Lanka, from every verified source.  (Owner: Dinuka)

``build_history`` asks each event source for what it knows and merges the answers into one list of
``HazardEvent``s. The result is committed as ``data/reference/hazard_history.json`` so the timeline, the
map and the evaluation work offline, and so a reader can see exactly which sources said what.

What each source covers (and does not):
    GDACS         floods, cyclones, quakes, droughts since 2015, with an alert colour
    IBTrACS       every North Indian Ocean cyclone that came within 600 km, since 1980
    NOAA tsunami  measured run-ups on Sri Lanka's coast, including 2004
    USGS          large quakes across the northern Indian Ocean (M7+), the source of Sri Lanka's real
                  earthquake and tsunami exposure. Quakes inside Sri Lanka's own box: none at M3.5+.
    Drought index our own 90-day rainfall anomaly, 2010 onward
Landslides have no free feed we could verify (COOLR was unreachable): see ``coolr.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from atlas.datasets.drought import DroughtIndex
from atlas.datasets.events import INDIAN_OCEAN, SRI_LANKA, HazardEvent
from atlas.datasets.gdacs import Gdacs
from atlas.datasets.ibtracs import Ibtracs
from atlas.datasets.noaa_tsunami import NoaaTsunami
from atlas.datasets.usgs import Usgs

HISTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "reference" / "hazard_history.json"


def _utc(y, m=1, d=1):
    return datetime(y, m, d, tzinfo=timezone.utc)


def build_history(*, offline: bool = False, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    plan = [
        (Gdacs(), _utc(2015), now),
        (Ibtracs(), _utc(1980), now),
        (NoaaTsunami(), _utc(1800), now),
        (Usgs(region=INDIAN_OCEAN, min_magnitude=7.0), _utc(1990), now),
        (Usgs(region=SRI_LANKA, min_magnitude=3.5), _utc(1990), now),
        (DroughtIndex(), _utc(2010), now),
    ]
    events: dict[str, HazardEvent] = {}
    sources = []
    for source, start, end in plan:
        found = source.events(start, end, offline=offline)
        for event in found:
            events[event.id] = event
        sources.append(
            {
                **source.info.model_dump(),
                "count": len(found),
                "window": [start.date().isoformat(), end.date().isoformat()],
            }
        )
    ordered = sorted(events.values(), key=lambda e: e.start)
    return {
        "retrieved": now.isoformat(),
        "region": SRI_LANKA.name,
        "sources": sources,
        "events": [_compact(e) for e in ordered],
    }


def _compact(event: HazardEvent) -> dict:
    """Drop bulky per-point detail (cyclone tracks, every run-up) so the snapshot stays small."""
    data = event.model_dump(mode="json")
    detail = data["detail"]
    if "track" in detail:
        detail["track_points"] = len(detail.pop("track"))
    if "runups" in detail:
        detail["runup_count"] = len(detail.pop("runups"))
    return data


def save_history(*, offline: bool = False, path: Path = HISTORY_PATH) -> Path:
    path.write_text(json.dumps(build_history(offline=offline), indent=1), encoding="utf-8")
    return path


def load_history(path: Path = HISTORY_PATH) -> list[HazardEvent]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [HazardEvent.model_validate(e) for e in data["events"]]
