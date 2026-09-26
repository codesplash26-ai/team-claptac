"""Questions to ask the world graph.  (Owner: Sumudu)

    impact_report(graph, "road:<u>-<v>")     what is affected if this road closes
    neighbourhood(graph, "incident:<id>")     a JSON view around an entity, for the UI

"What is affected" has two parts, and both are answered here:

1. **Dependants**: things that cannot work without the entity. They are found by walking the
   ontology's ``dependency`` relations backwards (a mission ``uses`` a road, a group is ``routed_over``
   it, a resource is ``assigned_to`` the mission, ...), transitively. A relation that is not a
   dependency does not propagate: a road lying in an area does not make the area depend on it.
2. **Access changes**: shelters and areas that lose, or have to take a longer way to, a safe route.
   Nothing "points at" a road for these, so they are recomputed on the road network with the road
   removed. Only roads not believed likely closed are usable; travel time is free-flow minutes.
   This approximates A4's routing (which also weighs beliefs); it does not replace it.

If the affected thing is part of an incident (``part_of``, a *rollup* relation), the incident is
affected too.
"""

from __future__ import annotations

import heapq
from typing import Any

from pydantic import BaseModel, Field

from atlas.knowledge.graph import ProjectedWorldGraph

USABLE_BELIEF = (
    0.3  # a road believed less open than this is treated as closed (A4's close_threshold)
)
TOPOLOGY_PREDICATES = ("from_junction", "to_junction")


class AccessChange(BaseModel):
    area: str
    shelter: str
    before_minutes: float
    after_minutes: float | None  # None: no safe route any more
    status: str  # "lost" | "longer"

    @property
    def delta_minutes(self) -> float | None:
        return None if self.after_minutes is None else self.after_minutes - self.before_minutes


class ImpactReport(BaseModel):
    entity: str
    known: bool = True
    entity_class: str | None = None
    already_unusable: bool = False  # the road was already closed or believed likely closed
    dependants: dict[str, list[str]] = Field(default_factory=dict)  # class -> ids
    reasons: dict[str, str] = Field(default_factory=dict)  # id -> "uses road:1-2"
    rollups: dict[str, list[str]] = Field(default_factory=dict)  # class -> ids (incidents)
    # Shelters a dependant is heading to or placed at: they do not depend on the entity, but their
    # expected arrivals are disrupted.
    expecting: list[str] = Field(default_factory=list)
    access: list[AccessChange] = Field(default_factory=list)

    @property
    def shelters_affected(self) -> list[str]:
        return sorted({c.shelter for c in self.access})

    @property
    def areas_affected(self) -> list[str]:
        return sorted({c.area for c in self.access})

    def flat(self) -> list[str]:
        """Every affected entity id, sorted: what the ``WorldGraph.impact`` protocol returns."""
        found = {i for ids in self.dependants.values() for i in ids}
        found |= {i for ids in self.rollups.values() for i in ids}
        found |= set(self.shelters_affected) | set(self.areas_affected) | set(self.expecting)
        found.discard(self.entity)
        return sorted(found)

    def summary(self) -> dict[str, Any]:
        """A JSON-ready view for the UI."""
        return {
            "entity": self.entity,
            "known": self.known,
            "entity_class": self.entity_class,
            "already_unusable": self.already_unusable,
            "dependants": self.dependants,
            "reasons": self.reasons,
            "rollups": self.rollups,
            "expecting": self.expecting,
            "access": [{**c.model_dump(), "delta_minutes": c.delta_minutes} for c in self.access],
            "affected": self.flat(),
        }


# ------------------------------------------------------------------------------------- impact


def _normalise(graph: ProjectedWorldGraph, entity_id: str) -> str:
    return graph.definition.normalise_id(entity_id) if graph.definition else entity_id.strip()


def _relation_names(graph: ProjectedWorldGraph, flag: str) -> set[str]:
    if graph.definition is None:
        return set()
    return {n for n, r in graph.definition.relations.items() if r.get(flag)}


def impact_report(graph: ProjectedWorldGraph, entity_id: str) -> ImpactReport:
    entity_id = _normalise(graph, entity_id)
    if not graph.has_entity(entity_id):
        return ImpactReport(entity=entity_id, known=False)
    entity_class = graph.entity_class(entity_id)
    report = ImpactReport(entity=entity_id, entity_class=entity_class)

    dependency, rollup = _relation_names(graph, "dependency"), _relation_names(graph, "rollup")

    # 1. dependants: walk dependency edges backwards, transitively
    seen, frontier = {entity_id}, [entity_id]
    while frontier:
        following = []
        for node in frontier:
            for subject, _, data in graph.graph.in_edges(node, data=True):
                predicate = data.get("predicate")
                if predicate in dependency and subject not in seen:
                    seen.add(subject)
                    report.reasons[subject] = f"{predicate} {node}"
                    following.append(subject)
        frontier = following
    for dependant in sorted(seen - {entity_id}):
        report.dependants.setdefault(graph.entity_class(dependant) or "?", []).append(dependant)

    # 1b. shelters that affected missions are heading to, or affected groups are placed at
    expecting = set()
    for dependant in sorted(seen - {entity_id}):
        for _, shelter, data in graph.graph.out_edges(dependant, data=True):
            if data.get("predicate") in dependency and graph.entity_class(shelter) == "Shelter":
                expecting.add(shelter)
                report.reasons.setdefault(shelter, f"{data['predicate']} of affected {dependant}")
    expecting.discard(entity_id)
    report.expecting = sorted(expecting)

    # 2. access: which shelters and areas lose, or lengthen, a safe route
    if entity_class == "Road":
        topology = _topology(graph)
        report.already_unusable = entity_id not in topology["usable_roads"]
        if not report.already_unusable:
            report.access = _access_without_road(graph, topology, entity_id)
    elif entity_class == "Shelter":
        report.access = _access_without_shelter(graph, entity_id)

    # 3. rollup: an affected part means an affected whole (a mission's incident)
    affected = {entity_id, *seen, *report.areas_affected}
    for node in sorted(affected):
        for _, whole, data in graph.graph.out_edges(node, data=True):
            if data.get("predicate") in rollup and whole not in affected:
                report.rollups.setdefault(graph.entity_class(whole) or "?", [])
                if whole not in report.rollups[graph.entity_class(whole) or "?"]:
                    report.rollups[graph.entity_class(whole) or "?"].append(whole)
                report.reasons.setdefault(whole, f"{data['predicate']} of affected {node}")
    for ids in report.rollups.values():
        ids.sort()
    return report


def _topology(graph: ProjectedWorldGraph) -> dict[str, Any]:
    """Junction graph of roads not believed likely closed. Cached until the graph changes."""
    if "topology" in graph.cache:
        return graph.cache["topology"]
    adjacency: dict[str, list[tuple[str, float, str]]] = {}
    usable_roads: set[str] = set()
    ends: dict[str, tuple[str, str]] = {}
    for road in graph.entities("Road"):
        heads = graph.neighbors(road, "from_junction")
        tails = graph.neighbors(road, "to_junction")
        if not heads or not tails:
            continue
        ends[road] = (heads[0], tails[0])
        closed = graph.attribute(road, "closed")
        belief = graph.attribute(road, "belief_open")
        minutes = graph.attribute(road, "minutes")
        if (closed and closed.value) or (belief and belief.value < USABLE_BELIEF):
            continue
        weight = float(minutes.value) if minutes else 1.0
        adjacency.setdefault(heads[0], []).append((tails[0], weight, road))
        usable_roads.add(road)
    topology = {"adjacency": adjacency, "usable_roads": usable_roads, "ends": ends}
    graph.cache["topology"] = topology
    return topology


def _shortest_minutes(adjacency, source: str, target: str, banned: str | None = None):
    """Dijkstra over free-flow minutes, ignoring the road `banned`. None if unreachable."""
    if source == target:
        return 0.0
    best = {source: 0.0}
    heap = [(0.0, source)]
    while heap:
        cost, node = heapq.heappop(heap)
        if node == target:
            return cost
        if cost > best.get(node, float("inf")):
            continue
        for neighbour, weight, road in adjacency.get(node, ()):
            if road == banned:
                continue
            new = cost + weight
            if new < best.get(neighbour, float("inf")):
                best[neighbour] = new
                heapq.heappush(heap, (new, neighbour))
    return None


def _service_pairs(graph: ProjectedWorldGraph):
    """(area, shelter, area junction, shelter junction) for every safe `reachable_from` fact."""
    for shelter in graph.entities("Shelter"):
        placed = graph.neighbors(shelter, "located_at")
        if not placed:
            continue
        for fact in graph.facts(shelter, "reachable_from"):
            if not fact.attrs.get("safe", False):
                continue
            anchor = graph.meta(fact.object).get("anchor_junction")
            if anchor:
                yield fact.object, shelter, anchor, placed[0]


def _access_without_road(graph, topology, road: str) -> list[AccessChange]:
    changes = []
    for area, shelter, source, target in _service_pairs(graph):
        before = _shortest_minutes(topology["adjacency"], source, target)
        if before is None:
            continue
        after = _shortest_minutes(topology["adjacency"], source, target, banned=road)
        if after is None:
            changes.append(
                AccessChange(
                    area=area,
                    shelter=shelter,
                    before_minutes=before,
                    after_minutes=None,
                    status="lost",
                )
            )
        elif after > before + 1e-9:
            changes.append(
                AccessChange(
                    area=area,
                    shelter=shelter,
                    before_minutes=before,
                    after_minutes=after,
                    status="longer",
                )
            )
    return sorted(changes, key=lambda c: (c.area, c.shelter))


def _access_without_shelter(graph, shelter: str) -> list[AccessChange]:
    return sorted(
        (
            AccessChange(
                area=fact.object,
                shelter=shelter,
                before_minutes=float(fact.attrs.get("minutes", 0.0)),
                after_minutes=None,
                status="lost",
            )
            for fact in graph.facts(shelter, "reachable_from")
            if fact.attrs.get("safe", False)
        ),
        key=lambda c: c.area,
    )


# --------------------------------------------------------------------------- neighbourhood


def _iso(moment) -> str | None:
    return moment.isoformat() if moment is not None else None


def neighbourhood(
    graph: ProjectedWorldGraph,
    entity_id: str,
    depth: int = 2,
    *,
    predicates: list[str] | None = None,
    include_topology: bool = False,
    max_nodes: int = 200,
    as_of=None,
    min_confidence: float = 0.0,
) -> dict[str, Any]:
    """Entities and facts within ``depth`` steps of ``entity_id``, as plain JSON for the UI.

    Road topology (``from_junction``/``to_junction``) is left out unless asked for: it would drown
    everything else. ``truncated`` is true when ``max_nodes`` cut the picture short.
    """
    entity_id = _normalise(graph, entity_id)
    if not graph.has_entity(entity_id):
        return {
            "root": entity_id,
            "known": False,
            "depth": depth,
            "nodes": [],
            "edges": [],
            "truncated": False,
        }
    skip = set() if include_topology else set(TOPOLOGY_PREDICATES)
    order, distance, truncated = [entity_id], {entity_id: 0}, False
    frontier = [entity_id]
    for level in range(1, max(depth, 0) + 1):
        following = []
        for node in frontier:
            for fact in graph.facts(
                node, direction="both", as_of=as_of, min_confidence=min_confidence
            ):
                if fact.predicate in skip or (predicates and fact.predicate not in predicates):
                    continue
                other = fact.object if fact.subject == node else fact.subject
                if other in distance:
                    continue
                if len(order) >= max_nodes:
                    truncated = True
                    continue
                distance[other] = level
                order.append(other)
                following.append(other)
        frontier = sorted(following)
    included = set(order)
    edges = []
    for node in order:
        for fact in graph.facts(node, as_of=as_of, min_confidence=min_confidence):
            if fact.object in included and fact.predicate not in skip:
                if predicates and fact.predicate not in predicates:
                    continue
                edges.append(
                    {
                        "source": fact.subject,
                        "target": fact.object,
                        "predicate": fact.predicate,
                        "confidence": fact.confidence,
                        "valid_from": _iso(fact.valid_from),
                        "provenance": list(fact.provenance),
                        "origin": fact.source,
                        **{k: v for k, v in fact.attrs.items()},
                    }
                )
    nodes = [
        {
            "id": node,
            "class": graph.entity_class(node),
            "label": graph.label(node),
            "distance": distance[node],
            "attributes": {
                name: {
                    "value": a.value,
                    "unit": a.unit,
                    "confidence": a.confidence,
                    "valid_from": _iso(a.valid_from),
                    "origin": a.source,
                }
                for name, a in sorted(graph.attributes(node).items())
            },
        }
        for node in order
    ]
    return {
        "root": entity_id,
        "known": True,
        "depth": depth,
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
    }
