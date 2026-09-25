"""Grounding: a claim is shown as fact only if the ontology and the evidence support it.  (Owner: Aeshan)

The check is the ontology's (Sumudu). This module applies it to a whole piece of advice and decides
what happens to rejected claims, so both sides can build against the same small contract.
"""

from __future__ import annotations

from typing import Callable

from atlas.intelligence.contracts import Advice
from atlas.knowledge.ontology import Ontology

EvidenceExists = Callable[[str], bool]  # is this event / belief / plan id real?


def ground(advice: Advice, ontology: Ontology, evidence_exists: EvidenceExists) -> Advice:
    """Return the advice with every claim marked accepted or rejected (with its violations).

    A claim is rejected if the ontology reports any violation, or if it cites no evidence, or cites
    evidence that does not exist. Rejected claims stay in the advice (the UI shows them struck out, as
    proof the guard works) but must never be rendered as fact or copied into an alert or sitrep.
    """
    for item in advice.claims:
        item.violations = list(ontology.validate(item.claim))
        if not item.claim.evidence_ids:
            item.violations.append(_no_evidence(item, "the claim cites no evidence"))
        for ref in item.claim.evidence_ids:
            if not evidence_exists(ref):
                item.violations.append(_no_evidence(item, f"evidence {ref!r} does not exist"))
        item.accepted = not item.violations
    return advice


def _no_evidence(item, message):
    from atlas.knowledge.ontology import Violation

    return Violation(claim=item.claim, code="no_evidence", message=message)
