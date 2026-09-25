"""Free-text field reports to structured facts.  (Owner: Aeshan; input from Hasindu's field channel)

A short message like "bridge at Kuruwita washed out, 12 families on the roof" becomes a ``FieldReport``
(atlas/ingest/field.py) plus ``Claim``s. The model extracts; it never invents: anything it cannot link
to a known entity (Sumudu's ``atlas/knowledge/linking.py``) is returned as unresolved and asked about,
not guessed. The result goes through the same validation as any other input.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from atlas.ingest.field import FieldReport
from atlas.knowledge.ontology import Claim


class Extraction(BaseModel):
    report: FieldReport | None
    claims: list[Claim] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)  # phrases that matched no known entity


async def extract(text: str, *, node_id: str, provider, graph) -> Extraction:
    raise NotImplementedError("Aeshan: LLM extraction with entity linking")
