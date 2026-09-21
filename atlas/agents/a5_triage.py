"""Agent 5 - Rescue & Needs Triage.

Transparent scoring of urgency × vulnerability, continuous re-ranking, and
human-readable explanations.

Inputs (events): RISK_UPDATE, TASK_ASSIGNED (task=triage), OBSERVATION (field needs)
Outputs (events): CANDIDATE_PLAN with ranked needs list
"""
from __future__ import annotations

import logging
from typing import Any

from atlas.agents.base import Agent
from atlas.core.events import Event, EventType

log = logging.getLogger(__name__)

_LEVEL_URGENCY = {
    "normal": 0.1,
    "watch": 0.4,
    "warning": 0.7,
    "severe": 0.95,
    "uncertain": 0.5,
}


def score_need(
    *,
    urgency: float,
    vulnerability: float,
    people_affected: int = 0,
    time_criticality: float = 0.5,
) -> float:
    """urgency × vulnerability with mild population and time boosts.

    All inputs expected in [0, 1] except people_affected (count).
    """
    urgency = max(0.0, min(1.0, urgency))
    vulnerability = max(0.0, min(1.0, vulnerability))
    time_criticality = max(0.0, min(1.0, time_criticality))
    pop_factor = min(1.0, people_affected / 5000) if people_affected else 0.0
    raw = urgency * vulnerability
    boosted = raw * (1.0 + 0.25 * pop_factor + 0.15 * time_criticality)
    return round(min(1.0, boosted), 4)


def explain_score(item: dict[str, Any]) -> str:
    return (
        f"{item.get('area_id', '?')}: score={item['score']:.3f} "
        f"(urgency={item['urgency']:.2f} × vulnerability={item['vulnerability']:.2f}; "
        f"~{item.get('estimated_vulnerable', 0)} vulnerable; "
        f"level={item.get('level', '?')}; gaps={item.get('information_gaps') or []})"
    )


class TriageAgent(Agent):
    name = "a5_triage"
    subscribes_to = (EventType.RISK_UPDATE, EventType.TASK_ASSIGNED, EventType.OBSERVATION)

    def __init__(self, bus, cop, ledger):
        super().__init__(bus, cop, ledger)
        self._needs: dict[str, dict[str, Any]] = {}

    async def handle(self, event: Event) -> list[Event]:
        if event.type == EventType.RISK_UPDATE:
            self._ingest_risk(event)
            return [self._candidate_plan(event.incident_id)]
        if event.type == EventType.TASK_ASSIGNED and event.payload.get("task") == "triage":
            return [self._candidate_plan(event.incident_id)]
        if event.type == EventType.OBSERVATION and event.payload.get("kind") == "need":
            self._ingest_need_report(event)
            return [self._candidate_plan(event.incident_id)]
        return []

    def _ingest_risk(self, event: Event) -> None:
        p = event.payload
        area_id = p.get("area_id") or "unknown"
        level = str(p.get("level", "watch"))
        urgency = _LEVEL_URGENCY.get(level, 0.5)
        vuln_meta = p.get("vulnerability") or {}
        frac = float(vuln_meta.get("vulnerable_fraction") or 0.0)
        if not vuln_meta.get("known"):
            frac = 0.2  # conservative prior when profile missing
        people = int(p.get("estimated_vulnerable") or vuln_meta.get("estimated_vulnerable") or 0)
        confidence = float(p.get("confidence", 0.5))
        item = {
            "area_id": area_id,
            "hazard": p.get("hazard"),
            "level": level,
            "urgency": urgency,
            "vulnerability": frac,
            "estimated_vulnerable": people,
            "confidence": confidence,
            "information_gaps": list(p.get("information_gaps") or []),
            "score": score_need(
                urgency=urgency,
                vulnerability=frac,
                people_affected=people,
                time_criticality=confidence,
            ),
        }
        item["explanation"] = explain_score(item)
        self._needs[area_id] = item
        log.info("triage update %s", item["explanation"])

    def _ingest_need_report(self, event: Event) -> None:
        p = event.payload
        area_id = p.get("area_id") or "unknown"
        urgency = float(p.get("urgency", 0.6))
        vulnerability = float(p.get("vulnerability", 0.5))
        people = int(p.get("people_affected", 0))
        item = {
            "area_id": area_id,
            "hazard": p.get("hazard", "unspecified"),
            "level": p.get("level", "warning"),
            "urgency": urgency,
            "vulnerability": vulnerability,
            "estimated_vulnerable": people,
            "confidence": float(p.get("confidence", 0.6)),
            "information_gaps": list(p.get("information_gaps") or []),
            "score": score_need(
                urgency=urgency,
                vulnerability=vulnerability,
                people_affected=people,
            ),
        }
        item["explanation"] = explain_score(item)
        self._needs[area_id] = item

    def ranked(self) -> list[dict[str, Any]]:
        return sorted(self._needs.values(), key=lambda x: x["score"], reverse=True)

    def _candidate_plan(self, incident_id: str | None) -> Event:
        ranked = self.ranked()
        return Event(
            type=EventType.CANDIDATE_PLAN,
            source=self.name,
            incident_id=incident_id,
            payload={
                "plan_type": "triage",
                "ranked_needs": ranked,
                "explanations": [n["explanation"] for n in ranked],
                "top_priority": ranked[0]["area_id"] if ranked else None,
            },
        )
