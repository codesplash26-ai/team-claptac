"""What each agent is and does, in plain language.  (Owner: Dinuka)

Static description (from the proposal and each agent's module docstring) plus live activity: the last
thing it received, the last thing it produced, counts, and whether it ever used the LLM. This is what
lets a viewer see what each agent is doing without reading code.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentCard(BaseModel):
    name: str  # "a4_evacuation"
    title: str  # "Evacuation & Access"
    layer: str  # sense | plan | act | control
    purpose: str  # one sentence a non-engineer understands
    reads: list[str] = Field(default_factory=list)  # event types and COP subjects
    writes: list[str] = Field(default_factory=list)
    uses_llm: bool = False
    deterministic_tools: list[str] = Field(default_factory=list)
    owner: str = ""


CATALOG: dict[str, AgentCard] = {}  # Dinuka fills this in from the agents' docstrings


def cards(runtime) -> list[AgentCard]:
    raise NotImplementedError("Dinuka: CATALOG plus live activity from the bus")
