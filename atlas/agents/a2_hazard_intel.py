"""Agent 2 - Hazard & Vulnerability Intelligence.

Builds a risk-zone layer from observations (including sensor readings), overlays
vulnerable populations, and reports information gaps.

Inputs (events): OBSERVATION, TRIGGER, CONFLICT_FLAG
Outputs (events): RISK_UPDATE
Also writes beliefs into the COP under subject prefixes area: and risk:.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from atlas.agents.base import Agent
from atlas.core.events import Belief, Event, EventType

log = logging.getLogger(__name__)

_REF = Path(__file__).resolve().parents[2] / "data" / "reference" / "vulnerable_populations.json"

# Heuristic water-level thresholds (cm above sensor zero / river bed proxy).
_WATER_WATCH_CM = 20.0
_WATER_WARNING_CM = 40.0
_WATER_SEVERE_CM = 60.0


def _load_vulnerabilities() -> list[dict[str, Any]]:
    if not _REF.exists():
        return []
    return json.loads(_REF.read_text(encoding="utf-8"))


def _risk_level_from_water(depth_cm: float) -> tuple[str, float]:
    if depth_cm >= _WATER_SEVERE_CM:
        return "severe", min(0.95, 0.7 + (depth_cm - _WATER_SEVERE_CM) / 200)
    if depth_cm >= _WATER_WARNING_CM:
        return "warning", 0.55 + (depth_cm - _WATER_WARNING_CM) / 100
    if depth_cm >= _WATER_WATCH_CM:
        return "watch", 0.35 + (depth_cm - _WATER_WATCH_CM) / 100
    return "normal", max(0.05, depth_cm / 100)


def _risk_from_rainfall(mm: float) -> tuple[str, float]:
    if mm >= 50:
        return "warning", 0.7
    if mm >= 25:
        return "watch", 0.45
    return "normal", 0.15


class HazardIntelAgent(Agent):
    name = "a2_hazard_intel"
    subscribes_to = (EventType.OBSERVATION, EventType.TRIGGER, EventType.CONFLICT_FLAG)

    def __init__(self, bus, cop, ledger, vulnerabilities: list[dict[str, Any]] | None = None):
        super().__init__(bus, cop, ledger)
        self.vulnerabilities = vulnerabilities if vulnerabilities is not None else _load_vulnerabilities()
        self._by_area = {v["area_id"]: v for v in self.vulnerabilities if "area_id" in v}

    async def handle(self, event: Event) -> list[Event]:
        if event.type == EventType.OBSERVATION:
            return self._from_observation(event)
        if event.type == EventType.TRIGGER:
            return self._from_trigger(event)
        if event.type == EventType.CONFLICT_FLAG:
            return self._from_conflict(event)
        return []

    def _from_observation(self, event: Event) -> list[Event]:
        payload = event.payload
        kind = payload.get("kind")
        area_id = None
        location = payload.get("location") or {}
        if isinstance(location, dict):
            area_id = location.get("area_id")

        level = "watch"
        confidence = 0.4
        hazard = "unknown"
        metric_value: float | None = None

        if kind == "sensor":
            sensor_type = payload.get("type")
            value = float(payload.get("value", 0))
            metric_value = value
            if sensor_type == "water_level":
                hazard = "flood"
                level, confidence = _risk_level_from_water(value)
            elif sensor_type == "rainfall":
                hazard = "flood"
                level, confidence = _risk_from_rainfall(value)
            elif sensor_type == "soil_moisture":
                hazard = "landslide"
                level, confidence = ("watch", 0.4) if value > 0.7 else ("normal", 0.2)
            else:
                hazard = str(sensor_type or "environmental")
                level, confidence = "watch", 0.3
        else:
            # Generic field report / feed observation
            hazard = str(payload.get("hazard") or payload.get("hazard_type") or "unspecified")
            level = str(payload.get("level") or "watch")
            confidence = float(payload.get("confidence", 0.5))
            area_id = area_id or payload.get("area_id")

        if not area_id:
            area_id = "unknown"

        return [self._emit_risk(event, area_id, hazard, level, confidence, metric_value)]

    def _from_trigger(self, event: Event) -> list[Event]:
        area_id = event.payload.get("area_id") or "unknown"
        hazard = event.payload.get("hazard") or "flood"
        score = float(event.payload.get("anomaly_score", 0.8))
        level = "severe" if score >= 0.85 else "warning"
        return [self._emit_risk(event, area_id, hazard, level, min(0.95, score), None)]

    def _from_conflict(self, event: Event) -> list[Event]:
        area_id = event.payload.get("area_id") or "unknown"
        gaps = self.information_gaps(area_id)
        self.cop.upsert(
            Belief(
                subject=f"area:{area_id}",
                attribute="info_gaps",
                value=gaps,
                confidence=0.6,
                provenance=[event.id, self.name],
            )
        )
        return [
            Event(
                type=EventType.RISK_UPDATE,
                source=self.name,
                incident_id=event.incident_id,
                payload={
                    "area_id": area_id,
                    "hazard": event.payload.get("hazard", "unspecified"),
                    "level": "uncertain",
                    "confidence": 0.3,
                    "information_gaps": gaps,
                    "reason": "conflicting reports",
                },
            )
        ]

    def _emit_risk(
        self,
        source_event: Event,
        area_id: str,
        hazard: str,
        level: str,
        confidence: float,
        metric_value: float | None,
    ) -> Event:
        overlay = self.overlay_vulnerability(area_id)
        gaps = self.information_gaps(area_id, have_metric=metric_value is not None)

        self.cop.upsert(
            Belief(
                subject=f"area:{area_id}",
                attribute="risk_level",
                value=level,
                confidence=confidence,
                provenance=[source_event.id, self.name],
            )
        )
        self.cop.upsert(
            Belief(
                subject=f"area:{area_id}",
                attribute="hazard",
                value=hazard,
                confidence=confidence,
                provenance=[source_event.id, self.name],
            )
        )
        self.cop.upsert(
            Belief(
                subject=f"risk:{area_id}",
                attribute="vulnerability_overlay",
                value=overlay,
                confidence=0.8,
                provenance=[self.name],
            )
        )

        exposed = int(overlay.get("estimated_vulnerable", 0))
        payload: dict[str, Any] = {
            "area_id": area_id,
            "hazard": hazard,
            "level": level,
            "confidence": round(confidence, 3),
            "vulnerability": overlay,
            "estimated_vulnerable": exposed,
            "information_gaps": gaps,
        }
        if metric_value is not None:
            payload["metric_value"] = metric_value

        log.info(
            "risk_update area=%s hazard=%s level=%s vulnerable≈%s",
            area_id,
            hazard,
            level,
            exposed,
        )
        return Event(
            type=EventType.RISK_UPDATE,
            source=self.name,
            incident_id=source_event.incident_id,
            payload=payload,
        )

    def overlay_vulnerability(self, area_id: str) -> dict[str, Any]:
        profile = self._by_area.get(area_id)
        if profile is None:
            return {
                "area_id": area_id,
                "known": False,
                "population": None,
                "vulnerable_fraction": None,
                "estimated_vulnerable": 0,
                "groups": [],
            }
        pop = int(profile.get("population", 0))
        frac = float(profile.get("vulnerable_fraction", 0))
        return {
            "area_id": area_id,
            "known": True,
            "name": profile.get("name"),
            "population": pop,
            "vulnerable_fraction": frac,
            "estimated_vulnerable": int(round(pop * frac)),
            "groups": list(profile.get("groups", [])),
        }

    def information_gaps(self, area_id: str, *, have_metric: bool = False) -> list[str]:
        gaps: list[str] = []
        if area_id not in self._by_area:
            gaps.append("no_vulnerability_profile")
        if not have_metric and self.cop.get(f"area:{area_id}", "risk_level") is None:
            gaps.append("no_direct_sensor_reading")
        if self.cop.get(f"area:{area_id}", "road_access") is None:
            gaps.append("road_access_unknown")
        if self.cop.get(f"area:{area_id}", "shelter_capacity") is None:
            gaps.append("shelter_capacity_unknown")
        return gaps
