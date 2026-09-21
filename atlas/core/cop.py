"""Common Operating Picture: shared state of beliefs (value, confidence, provenance, TTL)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from atlas.core.events import Belief


class CommonOperatingPicture:
    """In-memory COP. Workstream A may later back this with SQLite."""

    def __init__(self) -> None:
        self._beliefs: dict[tuple[str, str], Belief] = {}
        self._incidents: dict[str, dict[str, Any]] = {}

    def upsert(self, belief: Belief) -> Belief:
        key = (belief.subject, belief.attribute)
        existing = self._beliefs.get(key)
        if existing and existing.confidence > belief.confidence:
            # Keep stronger belief unless new one has fresher provenance signal.
            if belief.updated_at <= existing.updated_at:
                return existing
        self._beliefs[key] = belief
        return belief

    def get(self, subject: str, attribute: str) -> Belief | None:
        belief = self._beliefs.get((subject, attribute))
        if belief is None:
            return None
        if belief.ttl_seconds is not None:
            age = (datetime.now(timezone.utc) - belief.updated_at).total_seconds()
            if age > belief.ttl_seconds:
                return None
        return belief

    def query(
        self,
        *,
        subject_prefix: str | None = None,
        attribute: str | None = None,
    ) -> list[Belief]:
        results: list[Belief] = []
        for (subject, attr), belief in self._beliefs.items():
            if subject_prefix and not subject.startswith(subject_prefix):
                continue
            if attribute and attr != attribute:
                continue
            live = self.get(subject, attr)
            if live is not None:
                results.append(live)
        return results

    def set_incident(self, incident_id: str, state: dict[str, Any]) -> None:
        current = self._incidents.get(incident_id, {})
        current.update(state)
        current["incident_id"] = incident_id
        self._incidents[incident_id] = current

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        return self._incidents.get(incident_id)

    def list_incidents(self) -> list[dict[str, Any]]:
        return list(self._incidents.values())

    def snapshot(self) -> dict[str, Any]:
        return {
            "beliefs": [b.model_dump(mode="json") for b in self._beliefs.values()],
            "incidents": dict(self._incidents),
        }
