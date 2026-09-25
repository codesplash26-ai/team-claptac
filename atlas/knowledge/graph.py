"""The world graph.  (Owner: Sumudu)

Nodes are ontology entities (``road:<u>-<v>``, ``shelter:<id>``, ``gauge:<node>``, ``area:<id>``,
``incident:<id>``, ``mission:<id>``, ``resource:<id>``); edges are ontology relations. It is a
*projection*: rebuilt from the COP, the reference data (``data/reference``) and the ledger, never a
second source of truth.

The queries below are the contract. The in-memory version is a minimal working stand-in so the
intelligence layer and the UI can be built against something real; Sumudu replaces or extends it
(temporal facts, confidence, provenance, road-network topology from ``atlas/tools/geo.py``).
"""

from __future__ import annotations

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
