"""Tsunami run-ups on Sri Lanka's coast: NOAA NCEI/WDS historical tsunami database.  (Owner: Dinuka)

Verified against the live API. The run-up records (measured wave heights at places on the coast) are
what matters for Sri Lanka: the 2004 Indian Ocean tsunami was generated near Sumatra, so a search by
*source country* would miss it. Records are grouped by tsunami event, one ``HazardEvent`` each, with
the highest run-up as severity and every place listed in ``detail``.
"""

from __future__ import annotations

from datetime import datetime, timezone

from atlas.datasets.base import SourceInfo
from atlas.datasets.events import HazardEvent
from atlas.datasets.http import fetch_json

URL = "https://www.ngdc.noaa.gov/hazel/hazard-service/api/v1/tsunamis/runups"


def _when(item: dict) -> datetime | None:
    if not item.get("year") or item["year"] < 1:
        return None
    return datetime(
        item["year"],
        item.get("month") or 1,
        item.get("day") or 1,
        item.get("hour") or 0,
        item.get("minute") or 0,
        tzinfo=timezone.utc,
    )


def parse(items: list[dict]) -> list[HazardEvent]:
    groups: dict[int, list[dict]] = {}
    for item in items:
        if item.get("tsunamiEventId") and _when(item):
            groups.setdefault(item["tsunamiEventId"], []).append(item)
    events = []
    for event_id, runups in groups.items():
        heights = [r["runupHt"] for r in runups if r.get("runupHt") is not None]
        first = min(runups, key=lambda r: _when(r))
        peak = max(runups, key=lambda r: r.get("runupHt") or 0)
        events.append(
            HazardEvent(
                id=f"noaa_tsunami:{event_id}",
                hazard="tsunami",
                source="noaa_tsunami",
                name=f"Tsunami {_when(first).year} ({len(runups)} run-up record(s) in Sri Lanka)",
                start=_when(first),
                lat=peak.get("latitude"),
                lon=peak.get("longitude"),
                country="Sri Lanka",
                severity=max(heights) if heights else None,
                severity_unit="m run-up",
                severity_label=f"max run-up {max(heights):g} m" if heights else "",
                detail={
                    "places": sorted(
                        {r.get("locationName") for r in runups if r.get("locationName")}
                    ),
                    "runups": [
                        {
                            "place": r.get("locationName"),
                            "height_m": r.get("runupHt"),
                            "lat": r.get("latitude"),
                            "lon": r.get("longitude"),
                        }
                        for r in runups
                    ],
                },
            )
        )
    return sorted(events, key=lambda e: e.start)


class NoaaTsunami:
    info = SourceInfo(
        key="noaa_tsunami",
        title="NOAA NCEI/WDS Global Historical Tsunami Database (run-ups)",
        url="https://www.ngdc.noaa.gov/hazel/view/hazards/tsunami/runup-search",
        licence="Public domain (NOAA); cite NCEI/WDS",
        hazards=["tsunami"],
        verified=True,
    )

    def __init__(self, country: str = "SRI LANKA"):
        self.country = country

    def events(self, start: datetime, end: datetime, *, offline: bool = False) -> list[HazardEvent]:
        items: list[dict] = []
        page = 1
        while True:
            data = fetch_json(
                URL,
                params={"country": self.country, "page": page},
                offline=offline,
                max_age_s=30 * 24 * 3600,
            )
            items.extend(data.get("items", []))
            if page >= int(data.get("totalPages") or 1):
                break
            page += 1
        return [e for e in parse(items) if start <= e.start <= end]
