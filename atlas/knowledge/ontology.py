"""The ontology and the claim validator.  (Owner: Sumudu)

The point is to stop hallucination structurally: the intelligence layer (Aeshan) may only assert
things as ``Claim``s, and a claim is accepted only if its subject and object are known entities of the
right type, its predicate is a defined relation or attribute, and any value it states has the right
type, unit and range. Anything else comes back as a ``Violation`` and is dropped or sent for review; it
never reaches an operator as fact.

The vocabulary itself lives in ``config/ontology.yaml`` (classes, relations with a domain and a range,
attributes with units, hazard types). It is checked for internal consistency when loaded.

``YamlOntology`` fails closed. A subject or object must be an entity the world graph actually contains
(or a hazard named in the vocabulary), so a plausible invented shelter is rejected as ``unknown_entity``.
An ontology built without a graph cannot check that, so it refuses to be constructed; the explicit
``YamlOntology.vocabulary_only()`` checks structure, units and values but not existence.

``PermissiveOntology`` accepts everything. It exists so people are not blocked; do not ship it.

Violation codes
    unknown_entity     the id names something that is not in the world (or a hazard not in the vocabulary)
    unknown_predicate  not a relation or attribute in the vocabulary
    malformed_id       not ``class:key``, or the key has the wrong shape for its class
    wrong_type         the class is wrong for the predicate, or the value is the wrong kind
    wrong_unit         the unit is missing, extra, or not one the attribute uses
    bad_value          out of bounds, or not one of the allowed values
    fake_evidence      a cited evidence id does not exist (only when an ``evidence_exists`` is given)
    no_evidence        the claim cites nothing (only when ``require_evidence`` is set)
"""

from __future__ import annotations

import difflib
import math
import re
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml
from pydantic import BaseModel, Field, model_validator

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "config" / "ontology.yaml"


class Claim(BaseModel):
    """One assertion, in a form that can be checked. ``evidence_ids`` point at ledger events,
    COP beliefs or plan ids; an unsupported claim is a violation.

    ``unit`` is the unit of a numeric ``object`` ("cm", "people"); it is None for a relation, an
    id, a flag, or an attribute that has no unit."""

    subject: str  # an entity id, e.g. "shelter:ratnapura-shelter-01"
    predicate: str  # a relation or attribute in the ontology, e.g. "capacity"
    object: str | float | int | bool
    unit: str | None = None
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
    """Accepts every claim. A placeholder for tests. Not for production."""

    def validate(self, claim: Claim) -> list[Violation]:
        return []

    def entity_type(self, entity_id: str) -> str | None:
        return entity_id.split(":", 1)[0] if ":" in entity_id else None

    def relations(self, class_name: str) -> list[str]:
        return []


ATTRIBUTE_TYPES = {"number", "integer", "boolean", "enum", "string"}
HAZARD_STATUSES = {"live", "partial", "history_only"}


class OntologyDefinition(BaseModel):
    """Shape of ``config/ontology.yaml``. Checked for consistency as soon as it is loaded."""

    classes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    id_aliases: dict[str, str] = Field(default_factory=dict)  # spelling -> canonical prefix
    relations: dict[str, dict[str, Any]] = Field(default_factory=dict)  # domain, range, flags
    attributes: dict[str, dict[str, Any]] = Field(default_factory=dict)  # type, units, bounds
    hazards: list[str] = Field(default_factory=list)
    hazard_status: dict[str, dict[str, Any]] = Field(default_factory=dict)
    aliases: dict[str, list[str]] = Field(default_factory=dict)  # entity id -> extra names

    @model_validator(mode="after")
    def _consistent(self) -> OntologyDefinition:
        problems: list[str] = []
        problems += self._check_classes()
        problems += self._check_relations()
        problems += self._check_attributes()
        problems += self._check_hazards()
        problems += self._check_aliases()
        if problems:
            raise ValueError("ontology is inconsistent:\n  - " + "\n  - ".join(problems))
        return self

    def _check_classes(self) -> list[str]:
        problems, seen = [], {}
        for name, spec in self.classes.items():
            prefix = spec.get("prefix")
            if not isinstance(prefix, str) or not re.fullmatch(r"[a-z][a-z_]*", prefix):
                problems.append(f"class {name}: needs a lowercase `prefix`")
            elif prefix in seen:
                problems.append(f"classes {seen[prefix]} and {name} share the prefix {prefix!r}")
            else:
                seen[prefix] = name
            pattern = spec.get("key_pattern")
            if pattern is not None:
                try:
                    re.compile(pattern)
                except re.error as error:
                    problems.append(f"class {name}: key_pattern is not a valid regex ({error})")
        for alias, target in self.id_aliases.items():
            if alias in seen:
                problems.append(f"id alias {alias!r} is also a class prefix")
            if target not in seen:
                problems.append(f"id alias {alias!r} points at unknown prefix {target!r}")
        return problems

    def _check_relations(self) -> list[str]:
        problems = []
        for name, rel in self.relations.items():
            for side in ("domain", "range"):
                classes = rel.get(side)
                if not isinstance(classes, list) or not classes:
                    problems.append(
                        f"relation {name}: `{side}` must be a non-empty list of classes"
                    )
                    continue
                problems += [
                    f"relation {name}: {side} names unknown class {c!r}"
                    for c in classes
                    if c not in self.classes
                ]
            problems += [
                f"relation {name}: link attribute {a!r} is not an attribute"
                for a in rel.get("attributes", [])
                if a not in self.attributes
            ]
            if rel.get("dependency") and rel.get("rollup"):
                problems.append(f"relation {name}: cannot be both `dependency` and `rollup`")
        return problems

    def _check_attributes(self) -> list[str]:
        problems = []
        for name, attr in self.attributes.items():
            if name in self.relations:
                problems.append(f"{name!r} is both a relation and an attribute")
            applies = attr.get("applies_to")
            if not isinstance(applies, list) or not applies:
                problems.append(f"attribute {name}: `applies_to` must be a non-empty list")
            else:
                problems += [
                    f"attribute {name}: applies_to names unknown class {c!r}"
                    for c in applies
                    if c not in self.classes
                ]
            if attr.get("type") not in ATTRIBUTE_TYPES:
                problems.append(f"attribute {name}: type must be one of {sorted(ATTRIBUTE_TYPES)}")
            if not isinstance(attr.get("units", []), list):
                problems.append(f"attribute {name}: `units` must be a list")
            if attr.get("type") == "enum" and not attr.get("values"):
                problems.append(f"attribute {name}: an enum needs `values`")
            low, high = attr.get("min"), attr.get("max")
            if low is not None and high is not None and low > high:
                problems.append(f"attribute {name}: min is above max")
        return problems

    def _check_hazards(self) -> list[str]:
        problems = []
        if len(set(self.hazards)) != len(self.hazards):
            problems.append("hazards contains duplicates")
        for name, status in self.hazard_status.items():
            if name not in self.hazards:
                problems.append(f"hazard_status names unknown hazard {name!r}")
            if status.get("status") not in HAZARD_STATUSES:
                problems.append(f"hazard {name}: status must be one of {sorted(HAZARD_STATUSES)}")
            problems += [
                f"hazard {name}: signal {s!r} is not an attribute"
                for s in status.get("signals", [])
                if s not in self.attributes
            ]
        problems += [
            f"hazard {h!r} has no hazard_status"
            for h in self.hazards
            if h not in self.hazard_status
        ]
        return problems

    def _check_aliases(self) -> list[str]:
        prefixes = {spec.get("prefix") for spec in self.classes.values()}
        return [
            f"alias target {entity!r} is not `class:key` with a known class"
            for entity in self.aliases
            if ":" not in entity or entity.split(":", 1)[0] not in prefixes
        ]

    # ---------------------------------------------------------------- helpers

    def class_of_prefix(self, prefix: str) -> str | None:
        prefix = self.id_aliases.get(prefix, prefix)
        for name, spec in self.classes.items():
            if spec.get("prefix") == prefix:
                return name
        return None

    def prefix_of(self, class_name: str) -> str:
        return self.classes[class_name]["prefix"]

    def normalise_id(self, entity_id: str) -> str:
        """Rewrite an accepted alternative prefix (``edge:`` for ``road:``) to the canonical one."""
        entity_id = entity_id.strip()
        prefix, sep, key = entity_id.partition(":")
        if not sep:
            return entity_id
        return f"{self.id_aliases.get(prefix, prefix)}:{key}"

    def predicates_for(self, class_name: str) -> list[str]:
        relations = [n for n, r in self.relations.items() if class_name in r["domain"]]
        attributes = [n for n, a in self.attributes.items() if class_name in a["applies_to"]]
        return sorted(relations + attributes)


EvidenceExists = Callable[[str], bool]


class YamlOntology:
    """The real thing: the vocabulary in ``config/ontology.yaml`` checked against a world graph."""

    def __init__(
        self,
        definition: OntologyDefinition,
        graph: Any,
        *,
        evidence_exists: EvidenceExists | None = None,
        require_evidence: bool = False,
        _structure_only: bool = False,
    ) -> None:
        if graph is None and not _structure_only:
            raise ValueError(
                "YamlOntology needs a world graph to check that entities exist; use "
                "YamlOntology.vocabulary_only() to check structure, units and values alone"
            )
        self.definition, self.graph = definition, graph
        self.evidence_exists = evidence_exists
        self.require_evidence = require_evidence
        self._structure_only = _structure_only

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        graph: Any = None,
        *,
        evidence_exists: EvidenceExists | None = None,
        require_evidence: bool = False,
    ) -> YamlOntology:
        return cls(
            cls.read(path),
            graph,
            evidence_exists=evidence_exists,
            require_evidence=require_evidence,
        )

    @classmethod
    def vocabulary_only(cls, path: str | Path | None = None) -> YamlOntology:
        """Checks structure, units and values but NOT that an entity exists. Say so where you use it."""
        return cls(cls.read(path), None, _structure_only=True)

    @staticmethod
    def read(path: str | Path | None = None) -> OntologyDefinition:
        data = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8")) or {}
        return OntologyDefinition.model_validate(data)

    # -------------------------------------------------------------- Ontology

    def normalise(self, entity_id: str) -> str:
        return self.definition.normalise_id(entity_id)

    def entity_type(self, entity_id: str) -> str | None:
        entity_id = self.normalise(entity_id)
        prefix, sep, key = entity_id.partition(":")
        cls_name = self.definition.class_of_prefix(prefix) if sep else None
        if cls_name is None:
            return None
        if cls_name == "Hazard":
            return cls_name if key in self.definition.hazards else None
        if self.graph is None or not self.graph.has_entity(entity_id):
            return None
        return self.graph.entity_class(entity_id)

    def relations(self, class_name: str) -> list[str]:
        return self.definition.predicates_for(class_name)

    def vocabulary(self) -> dict[str, Any]:
        """The vocabulary as plain data, for the UI legend and for prompting a model with what it may say."""
        d = self.definition
        return {
            "classes": {
                n: {"prefix": s["prefix"], "description": s.get("description", "")}
                for n, s in d.classes.items()
            },
            "relations": {
                n: {
                    "domain": r["domain"],
                    "range": r["range"],
                    "dependency": bool(r.get("dependency")),
                    "rollup": bool(r.get("rollup")),
                    "description": r.get("description", ""),
                }
                for n, r in d.relations.items()
            },
            "attributes": {n: {k: v for k, v in a.items()} for n, a in d.attributes.items()},
            "hazards": {h: d.hazard_status[h] for h in d.hazards},
        }

    def validate(self, claim: Claim) -> list[Violation]:
        found: list[Violation] = []

        def add(code: str, message: str) -> None:
            found.append(Violation(claim=claim, code=code, message=message))

        d = self.definition
        relation = d.relations.get(claim.predicate)
        attribute = d.attributes.get(claim.predicate)
        if relation is None and attribute is None:
            known = sorted([*d.relations, *d.attributes])
            near = difflib.get_close_matches(claim.predicate, known, n=3)
            hint = f"; did you mean {', '.join(near)}?" if near else ""
            add("unknown_predicate", f"{claim.predicate!r} is not in the vocabulary{hint}")

        subject_class = self._entity(claim.subject, "subject", add)

        if relation is not None:
            if not isinstance(claim.object, str):
                add("wrong_type", f"{claim.predicate} relates entities; the object must be an id")
            else:
                object_class = self._entity(claim.object, "object", add)
                self._check_domain(claim, relation, subject_class, object_class, add)
            if claim.unit is not None:
                add("wrong_unit", f"{claim.predicate} relates entities and takes no unit")
        elif attribute is not None:
            if subject_class is not None and subject_class not in attribute["applies_to"]:
                add(
                    "wrong_type",
                    f"{claim.predicate} applies to {', '.join(attribute['applies_to'])}, "
                    f"not {subject_class}",
                )
            self._check_value(claim, attribute, add)

        if self.require_evidence and not claim.evidence_ids:
            add("no_evidence", "the claim cites no evidence")
        if self.evidence_exists is not None:
            for ref in claim.evidence_ids:
                if not self.evidence_exists(ref):
                    add("fake_evidence", f"evidence {ref!r} does not exist")
        return found

    # ---------------------------------------------------------------- checks

    def _entity(self, entity_id: str, role: str, add) -> str | None:
        """The class of a well-formed, existing entity, else None (after reporting why)."""
        d = self.definition
        entity_id = self.normalise(entity_id)
        prefix, sep, key = entity_id.partition(":")
        if not sep or not key:
            add("malformed_id", f"{role} {entity_id!r} is not of the form class:key")
            return None
        cls_name = d.class_of_prefix(prefix)
        if cls_name is None:
            known = ", ".join(sorted(s["prefix"] for s in d.classes.values()))
            add(
                "unknown_entity",
                f"{role} {entity_id!r}: no such kind of thing {prefix!r} ({known})",
            )
            return None
        pattern = d.classes[cls_name].get("key_pattern")
        if pattern and not re.fullmatch(pattern, key):
            add("malformed_id", f"{role} {entity_id!r}: a {cls_name} key looks like {pattern}")
            return None
        if cls_name == "Hazard":
            if key not in d.hazards:
                add("unknown_entity", f"{role} {entity_id!r}: {key!r} is not a known hazard")
                return None
            return cls_name
        if self._structure_only:
            return cls_name  # existence is not checked in this mode
        if not self.graph.has_entity(entity_id):
            add("unknown_entity", f"{role} {entity_id!r} does not exist")
            return None
        actual = self.graph.entity_class(entity_id)
        if actual != cls_name:
            add("wrong_type", f"{role} {entity_id!r} is a {actual} in the world, not a {cls_name}")
            return None
        return cls_name

    @staticmethod
    def _check_domain(claim, relation, subject_class, object_class, add) -> None:
        if subject_class is not None and subject_class not in relation["domain"]:
            add(
                "wrong_type",
                f"{claim.predicate} starts from {', '.join(relation['domain'])}, not {subject_class}",
            )
        if object_class is not None and object_class not in relation["range"]:
            add(
                "wrong_type",
                f"{claim.predicate} points at {', '.join(relation['range'])}, not {object_class}",
            )

    @staticmethod
    def _check_value(claim, attribute, add) -> None:
        value, kind = claim.object, attribute["type"]
        name = claim.predicate
        units = attribute.get("units", [])
        if units:
            if claim.unit is None:
                add("wrong_unit", f"{name} needs a unit, one of {units}")
            elif claim.unit not in units:
                add("wrong_unit", f"{name} is not measured in {claim.unit!r}; use one of {units}")
        elif claim.unit not in (None, ""):
            add("wrong_unit", f"{name} takes no unit, not {claim.unit!r}")

        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        if kind in ("number", "integer"):
            if not is_number or (isinstance(value, float) and not math.isfinite(value)):
                add("wrong_type", f"{name} must be a finite number, not {value!r}")
                return
            if kind == "integer" and not isinstance(value, int):
                add("wrong_type", f"{name} must be a whole number, not {value!r}")
                return
            low, high = attribute.get("min"), attribute.get("max")
            if low is not None and value < low:
                add("bad_value", f"{name} = {value} is below the minimum {low}")
            if high is not None and value > high:
                add("bad_value", f"{name} = {value} is above the maximum {high}")
        elif kind == "boolean":
            if not isinstance(value, bool):
                add("wrong_type", f"{name} must be true or false, not {value!r}")
        elif kind == "enum":
            if value not in attribute["values"]:
                add("bad_value", f"{name} = {value!r} is not one of {attribute['values']}")
        elif kind == "string" and not isinstance(value, str):
            add("wrong_type", f"{name} must be text, not {value!r}")
