"""Drought, derived from rainfall.  (Owner: Dinuka)

No free feed gives drought events for Sri Lanka directly, so this computes them from Open-Meteo's ERA5
daily rainfall: for every day, the rainfall of the preceding 90 days compared with the same 90-day
window in the 1991-2020 climatology (a standardised anomaly, in the spirit of SPI-3 but simpler: a
z-score of the window total, not a fitted gamma distribution). Runs of days below -1 are merged into
one event; the severity is the lowest z reached.

    z <= -1.0 moderate    z <= -1.5 severe    z <= -2.0 extreme

This is a derived index at ERA5's resolution: a way to find and rank dry spells, not an official drought
declaration. It needs no ATLAS sensor type, so it feeds the timeline and evaluation, not the live loop.
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone

from atlas.datasets.base import SourceInfo
from atlas.datasets.events import HazardEvent
from atlas.datasets.open_meteo import OpenMeteo

WINDOW_DAYS = 90
BASELINE = (1991, 2020)
LEVELS = ((-2.0, "extreme"), (-1.5, "severe"), (-1.0, "moderate"))


def rolling_totals(days: list[date], mm: list[float | None], window: int) -> dict[date, float]:
    out, total, queue = {}, 0.0, []
    for day, value in zip(days, mm):
        value = value or 0.0
        queue.append(value)
        total += value
        if len(queue) > window:
            total -= queue.pop(0)
        if len(queue) == window:
            out[day] = total
    return out


def zscores(totals: dict[date, float], baseline: dict[date, float]) -> dict[date, float]:
    """z-score of each day's window total against the baseline totals for the same day of year."""
    by_doy: dict[int, list[float]] = {}
    for day, total in baseline.items():
        by_doy.setdefault(day.timetuple().tm_yday, []).append(total)
    stats = {
        d: (statistics.fmean(v), statistics.pstdev(v)) for d, v in by_doy.items() if len(v) > 5
    }
    out = {}
    for day, total in totals.items():
        mean, sd = stats.get(day.timetuple().tm_yday, (None, None))
        if mean is not None and sd and sd > 1e-6:
            out[day] = (total - mean) / sd
    return out


def spells(z: dict[date, float], threshold: float = -1.0, min_days: int = 14) -> list[dict]:
    """Merge runs of days at or below ``threshold`` into spells with their worst z."""
    runs, current = [], None
    for day in sorted(z):
        if z[day] <= threshold:
            if current and (day - current["end"]).days <= 1:
                current["end"] = day
                current["min_z"] = min(current["min_z"], z[day])
            else:
                current = {"start": day, "end": day, "min_z": z[day]}
                runs.append(current)
    return [r for r in runs if (r["end"] - r["start"]).days + 1 >= min_days]


def label(z: float) -> str:
    return next((name for limit, name in LEVELS if z <= limit), "normal")


class DroughtIndex:
    info = SourceInfo(
        key="drought_index",
        title="Derived drought index: 90-day rainfall anomaly (Open-Meteo ERA5)",
        url="https://open-meteo.com/en/docs/historical-weather-api",
        licence="Derived from Open-Meteo data (CC BY 4.0); attribution required",
        hazards=["drought"],
        verified=True,
        notes="Our own index, not an official declaration. z-score against 1991-2020.",
    )

    def __init__(self, lat: float = 7.87, lon: float = 80.77, name: str = "Sri Lanka (centre)"):
        self.lat, self.lon, self.name = lat, lon, name

    def events(self, start: datetime, end: datetime, *, offline: bool = False) -> list[HazardEvent]:
        om = OpenMeteo()
        base_start = datetime(BASELINE[0], 1, 1, tzinfo=timezone.utc)
        base_end = datetime(BASELINE[1], 12, 31, tzinfo=timezone.utc)
        base = om.series(
            self.lat,
            self.lon,
            base_start,
            base_end,
            ["precipitation_sum"],
            hourly=False,
            offline=offline,
        )["precipitation_sum"]
        base_totals = rolling_totals([t.date() for t, _ in base], [v for _, v in base], WINDOW_DAYS)
        lead = start - timedelta(days=WINDOW_DAYS)
        recent = om.series(
            self.lat, self.lon, lead, end, ["precipitation_sum"], hourly=False, offline=offline
        )["precipitation_sum"]
        totals = rolling_totals([t.date() for t, _ in recent], [v for _, v in recent], WINDOW_DAYS)
        z = {
            d: v for d, v in zscores(totals, base_totals).items() if start.date() <= d <= end.date()
        }
        events = []
        for spell in spells(z):
            begin = datetime.combine(spell["start"], datetime.min.time(), timezone.utc)
            finish = datetime.combine(spell["end"], datetime.min.time(), timezone.utc)
            events.append(
                HazardEvent(
                    id=f"drought_index:{self.lat:.2f},{self.lon:.2f}:{spell['start']}",
                    hazard="drought",
                    source="drought_index",
                    name=f"Dry spell near {self.name} ({spell['start']} to {spell['end']})",
                    start=begin,
                    end=finish,
                    lat=self.lat,
                    lon=self.lon,
                    country="Sri Lanka",
                    severity=round(spell["min_z"], 2),
                    severity_unit="z (90-day rainfall)",
                    severity_label=label(spell["min_z"]),
                    detail={"window_days": WINDOW_DAYS, "baseline": list(BASELINE)},
                )
            )
        return events
