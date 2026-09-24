"""Agent 5 - Rescue & Needs Triage.

A transparent, ranked queue of who needs help first: urgency x vulnerability, with mild boosts for the
number of people affected and for how sure we are. Every score comes with the sentence that explains
it. Deterministic; no LLM.

Inputs
    RISK_UPDATE (A2)      ``kind == "hazard_risk"`` updates the area's need. A4's road-access
                          RISK_UPDATEs are ignored (they say nothing about people).
    TASK_ASSIGNED (A3)    a ``TriageTask`` addressed to ``a5_triage``.

Outputs
    CANDIDATE_PLAN        a ``TriagePlan`` for each task, and an unsolicited one (``task_id`` starts
                          with ``rerank:``) when the top priority changes, so A3 can decide whether
                          to replan. A5 never delegates or acts.

The queue lives in the shared database, so it survives a restart. ``record_need`` is the seam for
field needs reports (LoRa or a responder app); nothing produces them yet.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from pydantic import ValidationError

from atlas.agents.base import Agent
from atlas.agents.contracts import (
    TRIAGE_ASSIGNEE,
    CandidatePlanPayload,
    RankedNeed,
    TriagePlan,
    TriageTask,
)
from atlas.core.events import Event, EventType

log = logging.getLogger(__name__)

LEVEL_URGENCY = {"normal": 0.1, "watch": 0.4, "warning": 0.7, "severe": 0.95}
UNKNOWN_VULNERABILITY = 0.2  # conservative prior when an area has no vulnerability profile
ASSUMPTIONS = (
    "Urgency comes from A2's risk level; vulnerability from the area's demographic profile.",
    "Population and confidence boosts are mild by design, so the score stays explainable.",
    "Areas without a vulnerability profile use a fixed conservative prior and say so.",
)


def score_need(
    *, urgency: float, vulnerability: float, people_affected: int = 0, time_criticality: float = 0.5
) -> float:
    """urgency x vulnerability, boosted up to 25% for population and 15% for confidence."""
    clamp = lambda x: max(0.0, min(1.0, x))  # noqa: E731
    population = min(1.0, people_affected / 5000) if people_affected else 0.0
    boosted = (
        clamp(urgency)
        * clamp(vulnerability)
        * (1.0 + 0.25 * population + 0.15 * clamp(time_criticality))
    )
    return round(min(1.0, boosted), 4)


def explain(need: dict[str, Any]) -> str:
    gaps = need.get("information_gaps") or []
    return (
        f"{need['area_id']}: score {need['score']:.3f} = urgency {need['urgency']:.2f} "
        f"(level {need['level']}) x vulnerability {need['vulnerability']:.2f}, about "
        f"{need['estimated_vulnerable']} vulnerable people, confidence {need['confidence']:.2f}"
        + (f"; unknown: {', '.join(gaps)}" if gaps else "")
    )


class Triage(Agent):
    name = TRIAGE_ASSIGNEE
    subscribes_to = (EventType.RISK_UPDATE, EventType.TASK_ASSIGNED)

    def __init__(self, bus, cop, ledger, *, clock=None):
        super().__init__(bus, cop, ledger)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.db = cop.db
        self.db.connection.execute(
            "CREATE TABLE IF NOT EXISTS a5_needs (area_id TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )

    # ------------------------------------------------------------------ queue

    def needs(self) -> list[dict[str, Any]]:
        rows = self.db.connection.execute("SELECT body FROM a5_needs")
        items = [json.loads(row["body"]) for row in rows]
        # Ties broken by area id so the order is deterministic.
        return sorted(items, key=lambda n: (-n["score"], n["area_id"]))

    def top_priority(self) -> str | None:
        ranked = self.needs()
        return ranked[0]["area_id"] if ranked else None

    def record_need(
        self,
        area_id: str,
        *,
        urgency: float,
        vulnerability: float,
        people: int = 0,
        level: str = "warning",
        hazard: str | None = None,
        confidence: float = 0.6,
        gaps: list[str] | None = None,
        evidence_ids: list[str] | None = None,
        lat: float | None = None,
        lon: float | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Add or replace an area's need (field report seam, and what RISK_UPDATE uses)."""
        need = {
            "area_id": area_id,
            "hazard": hazard,
            "level": level,
            "urgency": urgency,
            "vulnerability": vulnerability,
            "estimated_vulnerable": people,
            "confidence": confidence,
            "information_gaps": list(gaps or []),
            "evidence_ids": list(evidence_ids or []),
            "lat": lat,
            "lon": lon,
            "name": name,
            "score": score_need(
                urgency=urgency,
                vulnerability=vulnerability,
                people_affected=people,
                time_criticality=confidence,
            ),
        }
        need["explanation"] = explain(need)
        self.db.connection.execute(
            "INSERT OR REPLACE INTO a5_needs VALUES (?,?)", (area_id, json.dumps(need))
        )
        return need

    def plan(self, area_ids: list[str] | None = None) -> TriagePlan:
        ranked = [n for n in self.needs() if area_ids is None or n["area_id"] in area_ids]
        return TriagePlan(
            status="ok" if ranked else "empty",
            reason="" if ranked else "no areas with a known risk",
            planned_at=self.clock(),
            snapshot_version=self.cop.version,
            ranked_needs=[RankedNeed(rank=i, **n) for i, n in enumerate(ranked, start=1)],
            assumptions=list(ASSUMPTIONS),
        )

    # ----------------------------------------------------------------- events

    async def handle(self, event: Event) -> list[Event]:
        if event.type is EventType.RISK_UPDATE and event.payload.get("kind") == "hazard_risk":
            return self._on_risk(event)
        if event.type is EventType.TASK_ASSIGNED and event.payload.get("assignee") == self.name:
            return self._on_task(event)
        return []

    def _on_risk(self, event: Event) -> list[Event]:
        p = event.payload
        before = self.top_priority()
        overlay = p.get("vulnerability") or {}
        fraction = overlay.get("vulnerable_fraction") if overlay.get("known") else None
        # Area urgency is its worst hazard, not the hazard that happened to change last.
        level = p.get("area_level") or p.get("level", "watch")
        confidence = float(p.get("confidence", 0.5))
        self.record_need(
            p["area_id"],
            hazard=p.get("hazard"),
            level=level,
            urgency=LEVEL_URGENCY.get(level, 0.4) * (0.85 if p.get("uncertain") else 1.0),
            vulnerability=UNKNOWN_VULNERABILITY if fraction is None else float(fraction),
            people=int(p.get("estimated_vulnerable") or 0),
            confidence=confidence,
            gaps=list(p.get("information_gaps") or []),
            evidence_ids=list(p.get("evidence_ids") or []),
            lat=overlay.get("lat"),
            lon=overlay.get("lon"),
            name=overlay.get("name"),
        )
        after = self.top_priority()
        if after == before:
            return []
        plan = self.plan()
        task_id = "rerank:" + sha256(f"{event.id}:{after}".encode()).hexdigest()[:10]
        return [self._candidate(event, task_id, plan)]

    def _on_task(self, event: Event) -> list[Event]:
        task_id = str(event.payload.get("task_id") or event.id)
        try:
            task = TriageTask.model_validate(event.payload)
            plan = self.plan(task.area_ids)
        except ValidationError as exc:
            plan = TriagePlan(
                status="invalid_task",
                reason=str(exc),
                planned_at=self.clock(),
                snapshot_version=self.cop.version,
            )
        return [self._candidate(event, task_id, plan)]

    def _candidate(self, parent: Event, task_id: str, plan: TriagePlan) -> Event:
        payload = CandidatePlanPayload(
            task_id=task_id,
            task="triage",
            agent=self.name,
            status=plan.status,
            plan=plan.model_dump(mode="json"),
        )
        return Event(
            id=sha256(f"{parent.id}:candidate_plan:{self.name}".encode()).hexdigest(),
            type=EventType.CANDIDATE_PLAN,
            source=self.name,
            created_at=self.clock(),
            incident_id=parent.incident_id,
            payload=payload.model_dump(mode="json"),
        )
