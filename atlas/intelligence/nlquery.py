"""Natural-language lookup over a detached read-only snapshot, with verified citations."""

from __future__ import annotations

from pydantic import BaseModel, Field

from atlas.intelligence.advisor import Advisor
from atlas.intelligence.contracts import GroundedClaim


class Answer(BaseModel):
    question: str
    text: str
    claims: list[GroundedClaim] = Field(default_factory=list)
    used_llm: bool = False


async def ask(question: str, *, provider, ontology, tools, timeout=15.0) -> Answer:
    # Only ReadContext's allowlisted tools are exposed, never arbitrary caller functions or SQL.
    from atlas.intelligence.context import ReadContext

    if not isinstance(tools, ReadContext):
        raise TypeError("tools must be a detached ReadContext")
    if not question.strip() or len(question) > 2000:
        raise ValueError("Question must contain 1–2000 characters")
    subset = ReadContext()
    subset.evidence = tools.evidence.copy()
    # With a model, retain all facts for tool-based follow-up, but seed with relevant facts first.
    relevant = tools.search(question, limit=80)
    from atlas.knowledge.ontology import Claim

    subset.facts = [Claim.model_validate(f) for f in relevant]
    if provider:
        subset.facts += [f for f in tools.facts if f not in subset.facts]
    result = await Advisor(provider, ontology, subset, timeout=timeout).advise(
        {
            "incident_id": "question",
            "cycle": 0,
            "question": question,
            "summary": "No matching snapshot facts. Try an area, incident, shelter or approval id.",
        }
    )
    return Answer(
        question=question,
        text=result.summary,
        claims=result.claims,
        used_llm=result.source == "llm",
    )
