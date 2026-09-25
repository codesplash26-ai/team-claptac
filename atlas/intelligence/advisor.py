"""The advisor: explains an incident's plan and questions it.  (Owner: Aeshan)

Runs from a worker after A3 has proposed a plan, never inside a bus handler. Reads the incident
(``IncidentCommander.get_incident``), the COP snapshot and the graph; produces an ``Advice``; grounds
it; publishes ``REASONING_COMPLETED``. It advises: it cannot add, remove or change an action. If it
thinks a replan is warranted it says so in ``recommend_replan`` and a human or A3's own rules decide.

``advise_without_llm`` is the deterministic fallback and is already usable, so the UI has something
to show from day one and the demo survives the provider being down.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from atlas.intelligence.contracts import Advice, ReasoningStep, ReasoningTrace


def advise_without_llm(incident: dict[str, Any], now: datetime | None = None) -> Advice:
    """Template advice straight from A3's own summary and notes. No model, no network."""
    return Advice(
        incident_id=incident["incident_id"],
        cycle=incident.get("cycle", 0),
        created_at=now or datetime.now(timezone.utc),
        summary=incident.get("summary") or "No plan has been proposed yet.",
        concerns=list(incident.get("notes", []))[-5:],
        questions=[],
        recommend_replan=False,
        trace=ReasoningTrace(
            steps=[ReasoningStep(kind="observation", text="Built from A3's reconciled summary.")]
        ),
        source="template",
    )


class Advisor:
    def __init__(self, provider, ontology, evidence_exists) -> None:
        self.provider, self.ontology, self.evidence_exists = provider, ontology, evidence_exists

    async def advise(self, incident: dict[str, Any]) -> Advice:
        """LLM advice if a provider is available, else ``advise_without_llm``. Always grounded."""
        raise NotImplementedError("Aeshan: LLM advisor with read-only tools and grounding")
