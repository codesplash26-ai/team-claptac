"""Ontology and world knowledge graph.  (Owner: Sumudu)

    ontology.py   what kinds of things and relations exist; validates claims against them
    graph.py      the world graph: roads, shelters, gauges, missions, incidents and how they relate
    world.py      builds that graph from the live system (a projection, never a second source of truth)
    queries.py    impact ("what does this closure affect?") and neighbourhood views
    linking.py    turn free text or ids into ontology entities, and scan prose for invented ones
    guard.py      the check that stops a model inventing entities in claims or in wording
    evidence.py   does this evidence id exist? (the function the intelligence layer's grounding asks for)

Not to be confused with ``atlas/memory/knowledge_graph.py``, which is the audit projection of the
ledger ("what depended on what"). This package models the *world*, not the audit trail.

Full description: ``docs/knowledge-graph.md``.
"""

from atlas.knowledge.evidence import EvidenceIndex
from atlas.knowledge.graph import InMemoryWorldGraph, ProjectedWorldGraph, WorldGraph
from atlas.knowledge.guard import EntityGuard
from atlas.knowledge.linking import Link, Mentions, find_mentions, link
from atlas.knowledge.ontology import (
    Claim,
    Ontology,
    PermissiveOntology,
    Violation,
    YamlOntology,
)
from atlas.knowledge.queries import ImpactReport, impact_report, neighbourhood
from atlas.knowledge.world import WorldProjector, build_world

__all__ = [
    "Claim",
    "EntityGuard",
    "EvidenceIndex",
    "ImpactReport",
    "InMemoryWorldGraph",
    "Link",
    "Mentions",
    "Ontology",
    "PermissiveOntology",
    "ProjectedWorldGraph",
    "Violation",
    "WorldGraph",
    "WorldProjector",
    "YamlOntology",
    "build_world",
    "find_mentions",
    "impact_report",
    "link",
    "neighbourhood",
]
