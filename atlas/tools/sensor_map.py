"""Sensor-to-map mapping: turns a validated water-level reading into road-belief evidence.

A1 emits `OBSERVATION_VALIDATED` with an `area_id` (e.g. `ratnapura-demo-river`). This module knows
where that area's gauge sits on the road network and what a given reading means for nearby roads.

Model (a deliberately simple, documented DEMO assumption, not hydrology):

    flood surface (m) = datum_elevation_m + metres_per_cm * level_cm

Roads whose midpoint is within `radius_m` of the gauge and whose lower endpoint is at or below that
surface are *inundated* (the original engine's rule). The scale factor lets a 0-40 cm tabletop
container stand in for a river rising several metres. Optional `pinned` overrides force chosen roads
closed from a given level, so a demo can guarantee a specific road closes.

Every reading yields one report per in-range road: inundated roads are reported closed (0.0) at full
weight; in-range roads that are still dry are reported open (1.0) at a lower weight, so roads reopen
when the water falls. Reports are applied with `CognitiveMap.update_from_report`.

Landslide and earthquake (also a DEMO assumption, not geotechnics): a slope fails on steep ground.
Roads within `radius_m` whose grade (height change over length) is at least `min_grade` are affected:

    landslide   soil wetness or slope tilt at or above its threshold -> steep roads reported blocked;
                at 1.5x the threshold ("severe") the grade limit drops to `severe_min_grade`.
                Below the threshold, steep roads are reported open at the dry weight, so they reopen.
    earthquake  a report at or above `magnitude` -> steep roads reported *uncertain* (open 0.35: check
                before use); at 1.5x ("severe") they are reported blocked.

Pure functions plus a small loader: no COP, no bus, no network.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from atlas.tools import geo

SENSOR_MAP_PATH = geo.ROOT / "data" / "reference" / "sensor_map.yaml"
MAX_REPORT_RELIABILITY = 0.95


class Gauge(BaseModel):
    lat: float
    lon: float


class Pinned(BaseModel):
    """Roads forced closed once the reading reaches `min_level_cm`."""

    min_level_cm: float = Field(ge=0)
    edges: list[str]


class SlopeRule(BaseModel):
    """Which roads a landslide threatens. Thresholds match config/sentinel.yaml by default."""

    soil_pct: float = Field(default=80, gt=0, le=100)
    tilt_deg: float = Field(default=2.0, gt=0)
    radius_m: float = Field(default=1200, gt=0)
    min_grade: float = Field(default=0.15, gt=0)  # 15%: steep enough to fail when saturated
    severe_min_grade: float = Field(default=0.12, gt=0)


class QuakeRule(BaseModel):
    """Which roads an earthquake puts in doubt (rockfall, cracked embankments, triggered slides)."""

    magnitude: float = Field(default=4.5, gt=0)
    radius_m: float = Field(default=1200, gt=0)
    min_grade: float = Field(default=0.15, gt=0)


class AreaMapping(BaseModel):
    area_id: str
    site: str
    gauge: Gauge
    datum_elevation_m: float  # ground elevation at the gauge, i.e. the "zero" of the reading
    metres_per_cm: float = Field(gt=0)  # demo scale: metres of flood surface per sensor centimetre
    radius_m: float = Field(gt=0)
    wet_weight: float = Field(
        default=1.0, gt=0, le=1
    )  # multiplies reliability for "closed" reports
    dry_weight: float = Field(default=0.3, ge=0, le=1)  # ... and for "still open" reports
    pinned: list[Pinned] = Field(default_factory=list)
    landslide: SlopeRule = Field(default_factory=SlopeRule)
    earthquake: QuakeRule = Field(default_factory=QuakeRule)


class RoadReport(BaseModel):
    """One piece of evidence about one road, ready for `CognitiveMap.update_from_report`."""

    u: int
    v: int
    reported_open: float
    reliability: float


def surface_elevation_m(mapping: AreaMapping, level_cm: float) -> float:
    return mapping.datum_elevation_m + mapping.metres_per_cm * level_cm


def edges_in_range(mapping: AreaMapping, rg: geo.RoadGraph) -> list[tuple]:
    return rg.edges_within(mapping.gauge.lat, mapping.gauge.lon, mapping.radius_m)


def inundated_edges(mapping: AreaMapping, rg: geo.RoadGraph, level_cm: float) -> list[tuple]:
    """Roads considered flooded at `level_cm`, sorted. Grows monotonically with the level."""
    surface = surface_elevation_m(mapping, level_cm)
    flooded = {
        (u, v)
        for (u, v) in edges_in_range(mapping, rg)
        if min(rg.elevation(u), rg.elevation(v)) <= surface
    }
    index = rg.edge_index()
    for pin in mapping.pinned:
        if level_cm >= pin.min_level_cm:
            flooded.update(index[e] for e in pin.edges)
    return sorted(flooded)


def road_reports(
    mapping: AreaMapping, rg: geo.RoadGraph, level_cm: float, confidence: float
) -> list[RoadReport]:
    """Evidence for every road in range from one reading with A1 confidence `confidence`."""
    wet = set(inundated_edges(mapping, rg, level_cm))
    targets = sorted(wet | set(edges_in_range(mapping, rg)))
    reports = []
    for u, v in targets:
        is_wet = (u, v) in wet
        weight = mapping.wet_weight if is_wet else mapping.dry_weight
        reports.append(
            RoadReport(
                u=u,
                v=v,
                reported_open=0.0 if is_wet else 1.0,
                reliability=min(MAX_REPORT_RELIABILITY, confidence * weight),
            )
        )
    return reports


def grade(rg: geo.RoadGraph, u, v) -> float:
    d = rg.graph[u][v]
    return abs(d["elev_u"] - d["elev_v"]) / max(1.0, d["length_m"])


def steep_edges(mapping: AreaMapping, rg: geo.RoadGraph, radius_m: float, min_grade: float) -> list:
    near = rg.edges_within(mapping.gauge.lat, mapping.gauge.lon, radius_m)
    return [(u, v) for (u, v) in near if grade(rg, u, v) >= min_grade]


SLOPE_ATTRIBUTES = {"soil_moisture", "tilt", "earthquake"}


def slope_reports(
    mapping: AreaMapping, rg: geo.RoadGraph, attribute: str, value: float, confidence: float
) -> list[RoadReport]:
    """Road evidence from a landslide signal (soil wetness, tilt) or an earthquake report."""
    if attribute == "earthquake":
        rule = mapping.earthquake
        ratio = value / rule.magnitude
        if ratio < 1:
            return []  # a small or distant quake says nothing about the roads
        roads = steep_edges(mapping, rg, rule.radius_m, rule.min_grade)
        open_, weight = (0.0, mapping.wet_weight) if ratio >= 1.5 else (0.35, 0.8)
        return [
            RoadReport(
                u=u,
                v=v,
                reported_open=open_,
                reliability=min(MAX_REPORT_RELIABILITY, confidence * weight),
            )
            for (u, v) in roads
        ]
    rule = mapping.landslide
    limit = rule.soil_pct if attribute == "soil_moisture" else rule.tilt_deg
    ratio = value / limit
    grade_limit = rule.severe_min_grade if ratio >= 1.5 else rule.min_grade
    roads = steep_edges(mapping, rg, rule.radius_m, grade_limit)
    blocked = ratio >= 1
    weight = mapping.wet_weight if blocked else mapping.dry_weight
    return [
        RoadReport(
            u=u,
            v=v,
            reported_open=0.0 if blocked else 1.0,
            reliability=min(MAX_REPORT_RELIABILITY, confidence * weight),
        )
        for (u, v) in roads
    ]


class SensorMap:
    """All configured areas, keyed by the `area_id` A1 puts on observations."""

    def __init__(self, areas: list[AreaMapping]):
        self.areas = {a.area_id: a for a in areas}
        if len(self.areas) != len(areas):
            raise ValueError("Duplicate area_id in sensor map")

    @classmethod
    def from_file(cls, path: Path | None = None) -> SensorMap:
        raw = yaml.safe_load((path or SENSOR_MAP_PATH).read_text(encoding="utf-8")) or {}
        return cls(
            [AreaMapping(area_id=area_id, **body) for area_id, body in raw.get("areas", {}).items()]
        )

    def get(self, area_id: str) -> AreaMapping | None:
        """None for an area with no mapping: the caller ignores the reading."""
        return self.areas.get(area_id)

    def validate(self, graphs: dict[str, geo.RoadGraph]) -> None:
        """Fail fast at start-up if the config does not fit the cached road graphs."""
        for area in self.areas.values():
            rg = graphs.get(area.site)
            if rg is None:
                raise ValueError(f"{area.area_id}: no road graph loaded for site {area.site!r}")
            index = rg.edge_index()
            for pin in area.pinned:
                unknown = [e for e in pin.edges if e not in index]
                if unknown:
                    raise ValueError(f"{area.area_id}: pinned edges not in graph: {unknown}")
