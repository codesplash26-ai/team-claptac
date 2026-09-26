"""The world graph.  (Owner: Sumudu)

Nodes are ontology entities (``road:<u>-<v>``, ``shelter:<id>``, ``gauge:<node>``, ``area:<id>``,
``incident:<id>``, ``mission:<id>``, ``resource:<id>``); edges are ontology relations. It is a
*projection*: rebuilt from the COP, the reference data (``data/reference``) and the ledger, never a
second source of truth.

Two implementations:

* ``InMemoryWorldGraph``: the minimal stand-in the intelligence layer and the tests were built against.
  Unchanged.
* ``ProjectedWorldGraph``: the real one, built by ``atlas.knowledge.world``. Entities carry typed
  attributes; facts carry where they came from, how sure we are and when they were true; and the
  queries follow the ontology's dependency relations (``atlas.knowledge.queries``).

The ``WorldGraph`` protocol below is the contract others code against. The new methods are additions on
``ProjectedWorldGraph`` only, so the protocol is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import networkx as nx


class WorldGraph(Protocol):
    def add_entity(self, entity_id: str, entity_class: str, **attrs: Any) -> None: ...

    def add_fact(self, subject: str, predicate: str, object: str, **attrs: Any) -> None: ...

    def has_entity(self, entity_id: str) -> bool: ...

    def entity_class(self, entity_id: str) -> str | None: ...

    def neighbors(self, entity_id: str, predicate: str | None = None) -> list[str]: ...

    def path(self, source: str, target: str) -> list[str] | None: ...

    def impact(self, entity_id: str) -> list[str]:
        """Everything that depends on this entity: what breaks, or must be re-planned, if it fails."""


class InMemoryWorldGraph:
    def __init__(self) -> None:
        self.graph = nx.MultiDiGraph()

    def add_entity(self, entity_id: str, entity_class: str, **attrs: Any) -> None:
        self.graph.add_node(entity_id, entity_class=entity_class, **attrs)

    def add_fact(self, subject: str, predicate: str, object: str, **attrs: Any) -> None:
        self.graph.add_edge(subject, object, key=predicate, predicate=predicate, **attrs)

    def has_entity(self, entity_id: str) -> bool:
        return entity_id in self.graph and "entity_class" in self.graph.nodes[entity_id]

    def entity_class(self, entity_id: str) -> str | None:
        return self.graph.nodes[entity_id].get("entity_class") if entity_id in self.graph else None

    def neighbors(self, entity_id: str, predicate: str | None = None) -> list[str]:
        if entity_id not in self.graph:
            return []
        return sorted(
            {
                v
                for _, v, d in self.graph.out_edges(entity_id, data=True)
                if predicate is None or d.get("predicate") == predicate
            }
        )

    def path(self, source: str, target: str) -> list[str] | None:
        try:
            return nx.shortest_path(self.graph, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def impact(self, entity_id: str) -> list[str]:
        if entity_id not in self.graph:
            return []
        return sorted(nx.ancestors(self.graph, entity_id))  # who points at it depends on it


# ----------------------------------------------------------------------------------------------
# The real graph
# ----------------------------------------------------------------------------------------------

FACT_FIELDS = ("predicate", "confidence", "valid_from", "valid_to", "provenance", "source")


@dataclass(frozen=True)
class AttributeValue:
    """A literal fact about an entity (a capacity, a belief), with its provenance."""

    value: Any
    unit: str | None = None
    confidence: float = 1.0
    valid_from: datetime | None = None
    provenance: tuple[str, ...] = ()
    source: str = ""


@dataclass(frozen=True)
class Fact:
    """A relation between two entities, with its provenance."""

    subject: str
    predicate: str
    object: str
    confidence: float = 1.0
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    provenance: tuple[str, ...] = ()
    source: str = ""
    attrs: dict[str, Any] = field(default_factory=dict, compare=False)

    def holds_at(self, moment: datetime | None) -> bool:
        """True if the fact was in force at `moment`. None means "now": only open-ended facts."""
        if moment is None:
            return self.valid_to is None
        if self.valid_from is not None and self.valid_from > moment:
            return False
        return self.valid_to is None or moment < self.valid_to


class ProjectedWorldGraph(InMemoryWorldGraph):
    """The world graph built by ``atlas.knowledge.world``. A projection, never a source of truth.

    Everything the base class offers still works. Added: typed attributes, facts with confidence,
    validity and provenance, and the dependency-aware queries in ``atlas.knowledge.queries``.
    """

    def __init__(self, definition: Any = None) -> None:
        super().__init__()
        # OntologyDefinition; it says which relations are dependencies, which drives `impact`.
        self.definition = definition
        self.report: dict[str, Any] = {}  # what was built and what was skipped, and why
        self.cache: dict[str, Any] = {}  # derived structures (topology); cleared on every write

    # ------------------------------------------------------------------ writes

    def add_entity(self, entity_id: str, entity_class: str, **attrs: Any) -> None:
        self.cache.clear()
        if entity_id in self.graph and "attributes" in self.graph.nodes[entity_id]:
            self.graph.nodes[entity_id].update(entity_class=entity_class, **attrs)
            return
        super().add_entity(entity_id, entity_class, attributes={}, **attrs)

    def add_fact(  # type: ignore[override]
        self,
        subject: str,
        predicate: str,
        object: str,
        *,
        confidence: float = 1.0,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        provenance: tuple[str, ...] | list[str] = (),
        source: str = "",
        **attrs: Any,
    ) -> None:
        self.cache.clear()
        super().add_fact(
            subject,
            predicate,
            object,
            confidence=confidence,
            valid_from=valid_from,
            valid_to=valid_to,
            provenance=tuple(provenance),
            source=source,
            **attrs,
        )

    def set_attribute(
        self,
        entity_id: str,
        name: str,
        value: Any,
        *,
        unit: str | None = None,
        confidence: float = 1.0,
        valid_from: datetime | None = None,
        provenance: tuple[str, ...] | list[str] = (),
        source: str = "",
    ) -> None:
        self.cache.clear()
        self.graph.nodes[entity_id]["attributes"][name] = AttributeValue(
            value, unit, confidence, valid_from, tuple(provenance), source
        )

    # ------------------------------------------------------------------- reads

    def entities(self, entity_class: str | None = None) -> list[str]:
        return sorted(
            n
            for n, d in self.graph.nodes(data=True)
            if "entity_class" in d and (entity_class is None or d["entity_class"] == entity_class)
        )

    def label(self, entity_id: str) -> str:
        if entity_id not in self.graph:
            return ""
        return self.graph.nodes[entity_id].get("label", entity_id)

    def attribute(self, entity_id: str, name: str) -> AttributeValue | None:
        if entity_id not in self.graph:
            return None
        return self.graph.nodes[entity_id].get("attributes", {}).get(name)

    def attributes(self, entity_id: str) -> dict[str, AttributeValue]:
        if entity_id not in self.graph:
            return {}
        return dict(self.graph.nodes[entity_id].get("attributes", {}))

    def meta(self, entity_id: str) -> dict[str, Any]:
        """Non-ontology details of an entity (label, coordinates, anchor junction, ...)."""
        if entity_id not in self.graph:
            return {}
        return {
            k: v
            for k, v in self.graph.nodes[entity_id].items()
            if k not in ("entity_class", "attributes")
        }

    def facts(
        self,
        entity_id: str,
        predicate: str | None = None,
        *,
        direction: str = "out",
        as_of: datetime | None = None,
        min_confidence: float = 0.0,
    ) -> list[Fact]:
        """Facts about an entity, optionally as they stood at ``as_of`` and above a confidence.

        ``direction`` is "out" (the entity is the subject), "in" (it is the object) or "both".
        """
        if entity_id not in self.graph:
            return []
        edges = []
        if direction in ("out", "both"):
            edges += list(self.graph.out_edges(entity_id, data=True))
        if direction in ("in", "both"):
            edges += list(self.graph.in_edges(entity_id, data=True))
        found = []
        for u, v, d in edges:
            if predicate is not None and d.get("predicate") != predicate:
                continue
            fact = Fact(
                u,
                d["predicate"],
                v,
                d.get("confidence", 1.0),
                d.get("valid_from"),
                d.get("valid_to"),
                d.get("provenance", ()),
                d.get("source", ""),
                {k: val for k, val in d.items() if k not in FACT_FIELDS},
            )
            if fact.confidence >= min_confidence and fact.holds_at(as_of):
                found.append(fact)
        return sorted(found, key=lambda f: (f.subject, f.predicate, f.object))

    # ---------------------------------------------------------------- queries

    def impact(self, entity_id: str) -> list[str]:
        """Everything that depends on this entity, and what access changes if it fails.

        Follows only relations the ontology marks ``dependency``, so a road does not "affect" the area
        it merely lies in. See ``impact_report`` for the reasons and the access changes.
        """
        return self.impact_report(entity_id).flat()

    def impact_report(self, entity_id: str):
        from atlas.knowledge.queries import impact_report

        return impact_report(self, entity_id)

    def neighbourhood(self, entity_id: str, depth: int = 2, **options: Any) -> dict[str, Any]:
        from atlas.knowledge.queries import neighbourhood

        return neighbourhood(self, entity_id, depth, **options)
