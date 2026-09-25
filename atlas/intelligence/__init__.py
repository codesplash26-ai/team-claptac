"""The intelligence layer: reasoning, grounding, and the LLM.  (Owner: Aeshan)

    contracts.py    Advice, GroundedClaim, ReasoningTrace: what the layer produces (the UI reads these)
    providers.py    LLMProvider interface; Anthropic (default) and OpenAI (optional fallback)
    advisor.py      per-incident advice, off the bus's critical path
    grounding.py    accept a claim only if the ontology and the evidence support it
    nlquery.py      "ask the system" questions over the COP, ledger and graph
    extraction.py   free-text field reports to structured facts

Design rules (see docs/orchestration.md for why):
  * The LLM is never on the critical path. Bus handlers run inside one database transaction and one
    lock, so they must not call a model. The advisor runs from a worker after a plan is proposed.
  * The LLM never computes routes, scores, allocations or policy. It explains, questions and extracts.
  * Every factual statement it makes is a ``Claim`` that must pass ``grounding.ground`` first.
  * With no key, or the provider down, the system behaves exactly as it does today.
"""

from atlas.intelligence.contracts import Advice, GroundedClaim, ReasoningTrace

__all__ = ["Advice", "GroundedClaim", "ReasoningTrace"]
