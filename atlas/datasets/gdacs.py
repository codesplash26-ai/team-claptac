"""GDACS: the UN/EC Global Disaster Alert and Coordination System.  (Owner: Dinuka)

Verified against the live API. One feed for floods, tropical cyclones, earthquakes, droughts, volcanoes
and wildfires, each with a Green / Orange / Red alert level. Good for a wide, current view of "what is
happening in the country" and for scoring how our own alerting compares with an international one.
"""

from __future__ import annotations

from datetime import datetime, timezone

from atlas.datasets.base import SourceInfo
from atlas.datasets.events import HazardEvent
from atlas.datasets.http import fetch_json

URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
KINDS = {
    "FL": "flood",
    "TC": "cyclone",
    "EQ": "earthquake",
    "DR": "drought",
    "VO": "volcano",
    "WF": "wildfire",
    "TS": "tsunami",
}
ALERT_SCORE = {"Green": 1.0, "Orange": 2.0, "Red": 3.0}


def _time(text: str | None) -> datetime | None:
    if not text:
        return None
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def parse(collection: dict) -> list[HazardEvent]:
    out = []
    for feature in collection.get("features", []):
        p = feature.get("properties") or {}
        start = _time(p.get("fromdate"))
        if start is None or p.get("eventtype") not in KINDS:
            continue
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        sev = p.get("severitydata") or {}
        level = p.get("alertlevel") or ""
        out.append(
            HazardEvent(
                id=f"gdacs:{p['eventtype']}{p['eventid']}",
                hazard=KINDS[p["eventtype"]],
                source="gdacs",
                name=p.get("name") or p.get("description") or "GDACS event",
                start=start,
                end=_time(p.get("todate")),
                lat=coords[1],
                lon=coords[0],
                country=p.get("country"),
                severity=ALERT_SCORE.get(level),
                severity_unit="alert level (1 green, 2 orange, 3 red)",
                severity_label=level,
                url=(p.get("url") or {}).get("report"),
                detail={
                    "glide": p.get("glide") or None,
                    "source": p.get("source"),
                    "severity_text": sev.get("severitytext"),
                    "gdacs_severity": sev.get("severity"),
                    "severity_unit": sev.get("severityunit"),
                },
            )
        )
    return out


class Gdacs:
    info = SourceInfo(
        key="gdacs",
        title="GDACS (Global Disaster Alert and Coordination System)",
        url="https://www.gdacs.org/",
        licence="Free to use with attribution to GDACS / JRC",
        hazards=["flood", "cyclone", "earthquake", "drought", "volcano", "wildfire"],
        verified=True,
    )

    def __init__(self, country: str = "Sri Lanka"):
        self.country = country

    def events(self, start: datetime, end: datetime, *, offline: bool = False) -> list[HazardEvent]:
        data = fetch_json(
            URL,
            params={
                "eventlist": "FL;TC;EQ;DR;VO;WF",
                "fromdate": start.strftime("%Y-%m-%d"),
                "todate": end.strftime("%Y-%m-%d"),
                "alertlevel": "Green;Orange;Red",
                "country": self.country,
            },
            offline=offline,
            max_age_s=None if end < datetime.now(timezone.utc) else 3 * 3600,
        )
        return sorted(parse(data), key=lambda e: e.start)
