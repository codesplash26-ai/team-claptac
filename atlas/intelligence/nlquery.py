"""Ask the system a question.  (Owner: Aeshan; Dinuka's UI hosts the box)

"Why was shelter 2 dropped?", "Which roads closed since 10:00?", "What is waiting for approval?"
Answers come from read-only tools over the ledger, the COP and the world graph. The model chooses the
tools and words the answer; every fact in it is a grounded claim with its evidence ids, shown as links
in the UI. It cannot change anything.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from atlas.intelligence.contracts import GroundedClaim


class Answer(BaseModel):
    question: str
    text: str
    claims: list[GroundedClaim] = Field(default_factory=list)
    used_llm: bool = False


async def ask(question: str, *, provider, ontology, tools) -> Answer:
    raise NotImplementedError("Aeshan: natural-language questions over read-only tools")
