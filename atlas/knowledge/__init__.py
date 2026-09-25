"""Ontology and world knowledge graph.  (Owner: Sumudu)

    ontology.py   what kinds of things and relations exist; validates claims against them
    graph.py      the world graph: roads, shelters, gauges, missions, incidents and how they relate
    linking.py    turn free text or ids into ontology entities (used by the intelligence layer)

Not to be confused with ``atlas/memory/knowledge_graph.py``, which is Aeshan's audit projection of the
ledger ("what depended on what"). This package models the *world*, not the audit trail.
"""

from atlas.knowledge.graph import InMemoryWorldGraph, WorldGraph
from atlas.knowledge.ontology import (
    Claim,
    Ontology,
    PermissiveOntology,
    Violation,
)

__all__ = [
    "Claim",
    "InMemoryWorldGraph",
    "Ontology",
    "PermissiveOntology",
    "Violation",
    "WorldGraph",
]
