"""The ontology and the claim validator.  (Owner: Sumudu)

The point is to stop hallucination structurally: the intelligence layer (Aeshan) may only assert
things as ``Claim``s, and a claim is accepted only if its subject and object are known entities of the
right type, its predicate is a defined relation, and its evidence exists. Anything else comes back as a
``Violation`` and is dropped or sent for review; it never reaches an operator as fact.

The vocabulary itself lives in ``config/ontology.yaml`` (classes, relations with a domain and a range,
hazard types, and the units each attribute uses). Sumudu fills it in.

Until the real validator lands, tests and the intelligence layer can use ``PermissiveOntology``, which
accepts everything. It exists so people are not blocked; do not ship it.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class Claim(BaseModel):
    """One assertion, in a form that can be checked. ``evidence_ids`` point at ledger events,
    COP beliefs or plan ids; an unsupported claim is a violation."""

    subject: str  # an entity id, e.g. "shelter:ratnapura-shelter-01"
    predicate: str  # a relation in the ontology, e.g. "reachable_from"
    object: str | float | int | bool
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)


class Violation(BaseModel):
    claim: Claim
    code: str  # unknown_entity | unknown_predicate | wrong_type | wrong_unit | no_evidence | ...
    message: str


class Ontology(Protocol):
    def validate(self, claim: Claim) -> list[Violation]:
        """Empty list means the claim is well-formed and supported."""

    def entity_type(self, entity_id: str) -> str | None:
        """The ontology class of a known entity, or None if it is not known."""

    def relations(self, class_name: str) -> list[str]:
        """Predicates whose domain includes this class."""


class PermissiveOntology:
    """Accepts every claim. A placeholder so other workstreams are not blocked. Not for production."""

    def validate(self, claim: Claim) -> list[Violation]:
        return []

    def entity_type(self, entity_id: str) -> str | None:
        return entity_id.split(":", 1)[0] if ":" in entity_id else None

    def relations(self, class_name: str) -> list[str]:
        return []


class OntologyDefinition(BaseModel):
    """Shape of ``config/ontology.yaml``. Sumudu may extend it."""

    classes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    relations: dict[str, dict[str, Any]] = Field(default_factory=dict)  # domain, range, unit...
    hazards: list[str] = Field(default_factory=list)


class YamlOntology:
    """The real thing: loads ``config/ontology.yaml`` and checks claims against a WorldGraph."""

    def __init__(self, definition: OntologyDefinition, graph: Any) -> None:
        self.definition, self.graph = definition, graph

    @classmethod
    def load(cls, path: str | None = None, graph: Any = None) -> "YamlOntology":
        raise NotImplementedError("Sumudu: load config/ontology.yaml")

    def validate(self, claim: Claim) -> list[Violation]:
        raise NotImplementedError("Sumudu: claim validation")

    def entity_type(self, entity_id: str) -> str | None:
        raise NotImplementedError("Sumudu")

    def relations(self, class_name: str) -> list[str]:
        raise NotImplementedError("Sumudu")
