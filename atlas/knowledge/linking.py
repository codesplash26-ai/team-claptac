"""Entity linking.  (Owner: Sumudu)

Resolve what a person or a model wrote ("the Kalu Ganga gauge", "Ratnapura shelter 1", an OSM node id)
to a known ontology entity id, or say it cannot. Used by the intelligence layer to ground free text and
by field-report handling. A link with low confidence is returned as ambiguous, never guessed.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Link(BaseModel):
    text: str
    entity_id: str | None  # None: no confident match
    entity_class: str | None = None
    confidence: float = 0.0
    alternatives: list[str] = Field(default_factory=list)


def link(text: str, graph) -> Link:
    raise NotImplementedError("Sumudu: entity linking against the world graph")
