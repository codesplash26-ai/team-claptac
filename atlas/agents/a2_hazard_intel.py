"""Agent 2 - Hazard & Vulnerability Intelligence.

Turns validated observations into a per-area risk picture, overlays the vulnerable population,
and says what is still unknown. Deterministic; no LLM.

Inputs
    OBSERVATION_VALIDATED (A1)  a measurement that passed validation. A2 never reads the raw
                                OBSERVATION: that would bypass the Sentinel's checks.
    TRIGGER (A1)                a threshold crossing; makes sure the area is at least "warning".
    CONFLICT_FLAG (A1)          contradicting reports; the risk is kept but marked uncertain.

Outputs
    RISK_UPDATE                 ``{"kind": "hazard_risk", ...}`` when an area's level for a hazard
                                changes (not on every reading), so downstream agents are not spammed.
                                A4 also emits RISK_UPDATE (``kind: "road_access_changed"``); consumers
                                must check ``kind``.
    COP                         ``area:<id>`` / ``risk_level`` and ``hazard``, written only when the
                                value changes (every COP write moves the version and can make an
                                in-flight plan stale at the Gateway).

Levels are ratios of the Sentinel's own thresholds, so A1 and A2 never disagree about what counts as
alarming: ratio < 0.8 normal, 0.8 watch, 1.0 warning, 1.5 severe.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from atlas.agents.base import Agent
from atlas.core.config import SentinelConfig
from atlas.core.events import Belief, Event, EventType, Observation

log = logging.getLogger(__name__)

REFERENCE = (
    Path(__file__).resolve().parents[2] / "data" / "reference" / "vulnerable_populations.json"
)
LEVELS = ("normal", "watch", "warning", "severe")
HAZARD_OF = {"water_level": "flood", "rainfall": "flood", "soil_moisture": "landslide"}


def load_vulnerabilities(path: Path = REFERENCE) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def level_for(ratio: float) -> str:
    if ratio >= 1.5:
        return "severe"
    if ratio >= 1.0:
        return "warning"
    if ratio >= 0.8:
        return "watch"
    return "normal"


class HazardIntel(Agent):
    name = "a2_hazard_intel"
    subscribes_to = (
        EventType.OBSERVATION_VALIDATED,
        EventType.TRIGGER,
        EventType.CONFLICT_FLAG,
    )

    def __init__(
        self,
        bus,
        cop,
        ledger,
        *,
        config: SentinelConfig | None = None,
        vulnerabilities: list[dict[str, Any]] | None = None,
        clock=None,
    ):
        super().__init__(bus, cop, ledger)
        self.config = config or SentinelConfig()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        profiles = vulnerabilities if vulnerabilities is not None else load_vulnerabilities()
        self.profiles = {p["area_id"]: p for p in profiles if "area_id" in p}
        self.db = cop.db
        self.db.connection.execute(
            "CREATE TABLE IF NOT EXISTS a2_risk (area_id TEXT, hazard TEXT, level TEXT NOT NULL,"
            " confidence REAL NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (area_id, hazard))"
        )

    # ------------------------------------------------------------------ queries

    def levels(self, area_id: str) -> dict[str, str]:
        rows = self.db.connection.execute(
            "SELECT hazard, level FROM a2_risk WHERE area_id=?", (area_id,)
        )
        return {row["hazard"]: row["level"] for row in rows}

    def area_level(self, area_id: str) -> str:
        return max(self.levels(area_id).values(), key=LEVELS.index, default="normal")

    def overlay(self, area_id: str) -> dict[str, Any]:
        profile = self.profiles.get(area_id)
        if profile is None:
            return {
                "area_id": area_id,
                "known": False,
                "population": None,
                "vulnerable_fraction": None,
                "estimated_vulnerable": 0,
                "groups": [],
            }
        population = int(profile.get("population", 0))
        fraction = float(profile.get("vulnerable_fraction", 0))
        return {
            "area_id": area_id,
            "known": True,
            "name": profile.get("name"),
            "population": population,
            "vulnerable_fraction": fraction,
            "estimated_vulnerable": int(round(population * fraction)),
            "groups": list(profile.get("groups", [])),
            "lat": profile.get("lat"),
            "lon": profile.get("lon"),
        }

    def information_gaps(self, area_id: str, *, conflicting: bool = False) -> list[str]:
        gaps = []
        if area_id not in self.profiles:
            gaps.append("no_vulnerability_profile")
        if not self.levels(area_id):
            gaps.append("no_direct_measurement")
        if conflicting:
            gaps.append("conflicting_reports")
        return gaps

    # ------------------------------------------------------------------- events

    async def handle(self, event: Event) -> list[Event]:
        if event.type is EventType.OBSERVATION_VALIDATED:
            return self._on_observation(event)
        if event.type is EventType.TRIGGER:
            return self._on_trigger(event)
        if event.type is EventType.CONFLICT_FLAG:
            return self._on_conflict(event)
        return []

    def _on_observation(self, event: Event) -> list[Event]:
        try:
            obs = Observation.model_validate(event.payload)
        except ValidationError:
            log.warning("A2 ignoring malformed validated observation %s", event.id)
            return []
        hazard = HAZARD_OF.get(obs.attribute)
        threshold = {
            "water_level": self.config.water_threshold_cm,
            "rainfall": self.config.rainfall_threshold_mm,
            "soil_moisture": self.config.soil_threshold_pct,
        }.get(obs.attribute)
        if hazard is None or threshold is None:
            return []
        level = level_for(obs.value / threshold)
        metric = {
            "attribute": obs.attribute,
            "value": obs.value,
            "unit": obs.unit,
            "threshold": threshold,
        }
        return self._record(
            event,
            obs.area_id,
            hazard,
            level,
            obs.confidence,
            metric,
            obs.evidence_ids,
            "measurement",
        )

    def _on_trigger(self, event: Event) -> list[Event]:
        area = event.payload.get("area_id")
        if not area:
            return []
        hazard = "flood"
        current = self.levels(area).get(hazard, "normal")
        if LEVELS.index(current) >= LEVELS.index("warning"):
            return []  # already at least as alarmed as the Sentinel
        confidence = float(event.payload.get("confidence", 0.6))
        return self._record(
            event,
            area,
            hazard,
            "warning",
            confidence,
            None,
            list(event.payload.get("evidence_ids", [])),
            "sentinel_trigger",
        )

    def _on_conflict(self, event: Event) -> list[Event]:
        area = event.payload.get("area_id")
        if not area:
            return []
        hazard = HAZARD_OF.get(event.payload.get("attribute", ""), "flood")
        level = self.levels(area).get(hazard, "watch")
        return self._record(
            event,
            area,
            hazard,
            level,
            0.4,
            None,
            list(event.payload.get("evidence_ids", [])),
            "conflicting_reports",
            conflicting=True,
            force=True,
        )

    def _record(
        self,
        event,
        area,
        hazard,
        level,
        confidence,
        metric,
        evidence,
        reason,
        *,
        conflicting=False,
        force=False,
    ) -> list[Event]:
        previous = self.levels(area).get(hazard)
        if previous == level and not force:
            return []
        now = self.clock()
        self.db.connection.execute(
            "INSERT OR REPLACE INTO a2_risk VALUES (?,?,?,?,?)",
            (area, hazard, level, confidence, now.isoformat()),
        )
        overall = self.area_level(area)
        self._write_beliefs(area, hazard, overall, confidence, evidence, now)
        overlay = self.overlay(area)
        payload = {
            "kind": "hazard_risk",
            "area_id": area,
            "hazard": hazard,
            "level": level,
            "previous_level": previous,
            "area_level": overall,
            "confidence": round(confidence, 3),
            "uncertain": conflicting,
            "reason": reason,
            "metric": metric,
            "vulnerability": overlay,
            "estimated_vulnerable": overlay["estimated_vulnerable"],
            "information_gaps": self.information_gaps(area, conflicting=conflicting),
            "evidence_ids": evidence,
        }
        return [
            Event(
                # Deterministic id: a retried delivery must produce the same event.
                id=sha256(f"{event.id}:risk_update:{self.name}".encode()).hexdigest(),
                type=EventType.RISK_UPDATE,
                source=self.name,
                created_at=now,
                incident_id=event.incident_id,
                payload=payload,
            )
        ]

    def _write_beliefs(self, area, hazard, overall, confidence, evidence, now):
        for attribute, value in (("risk_level", overall), ("hazard", hazard)):
            current = self.cop.get(f"area:{area}", attribute, include_stale=True)
            if current is not None and current.value == value:
                continue  # unchanged: writing would only move the COP version
            self.cop.put(
                Belief(
                    subject=f"area:{area}",
                    attribute=attribute,
                    value=value,
                    confidence=confidence,
                    provenance=[*evidence, self.name],
                    updated_at=now,
                )
            )
