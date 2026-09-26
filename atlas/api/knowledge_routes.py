"""Read-only routes onto the ontology and the world knowledge graph.  (Owner: Sumudu)

Nothing here changes the system: the world graph is a projection rebuilt from the live stores, and
every route only reads it. The two POST routes (validate, check) take a body, so they are POSTs, but
they too only read; they are audited like any other POST.

Permission: ``mission:read``, the closest existing read permission. If the team wants the graph to be
grantable on its own, add a ``knowledge:read`` in ``atlas/manager/rbac.py`` and change ``READ`` below.

Ids are query parameters, not path segments: they contain ``:`` and ``/`` (``group:rescue/g1``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from atlas.knowledge.evidence import EvidenceIndex
from atlas.knowledge.guard import EntityGuard
from atlas.knowledge.linking import find_mentions, link
from atlas.knowledge.ontology import Claim, YamlOntology
from atlas.knowledge.queries import neighbourhood
from atlas.knowledge.world import WorldProjector
from atlas.manager import rbac
from atlas.manager.rbac import requires

READ = rbac.MISSION_READ

router = APIRouter(prefix="/knowledge")

MAX_CLAIMS = 100
MAX_TEXT = 20_000


class ClaimCheck(BaseModel):
    claims: list[Claim] = Field(max_length=MAX_CLAIMS)
    check_evidence: bool = True  # also require every cited evidence id to exist, as grounding does


class TextInput(BaseModel):
    text: str = Field(max_length=MAX_TEXT)
    context: list[str] = Field(default_factory=list, max_length=20)
    strict_ambiguity: bool = True


# ------------------------------------------------------------------------------------ plumbing


def projector_of(request: Request) -> WorldProjector:
    """The one projector for this app, made the first time it is needed."""
    state = request.app.state
    if getattr(state, "world_projector", None) is None:
        state.world_projector = WorldProjector.from_system(state.system)
    return state.world_projector


def world_of(request: Request):
    return projector_of(request).world()


def ontology_of(request: Request, world) -> YamlOntology:
    """An ontology bound to ``world``. Cached until the world is rebuilt."""
    state = request.app.state
    cached = getattr(state, "world_ontology", None)
    if cached is None or cached[0] is not world:
        cached = (world, YamlOntology.load(graph=world))
        state.world_ontology = cached
    return cached[1]


def known(world, entity_id: str) -> str:
    entity_id = entity_id.strip()
    normal = ontology_normal(world, entity_id)
    if not world.has_entity(normal):
        raise HTTPException(404, f"no such entity {entity_id!r}")
    return normal


def ontology_normal(world, entity_id: str) -> str:
    return world.definition.normalise_id(entity_id)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def attribute_json(value) -> dict[str, Any]:
    return {
        "value": value.value,
        "unit": value.unit,
        "confidence": value.confidence,
        "valid_from": iso(value.valid_from),
        "source": value.source,
        "provenance": list(value.provenance),
    }


def fact_json(fact) -> dict[str, Any]:
    return {
        "subject": fact.subject,
        "predicate": fact.predicate,
        "object": fact.object,
        "confidence": fact.confidence,
        "valid_from": iso(fact.valid_from),
        "valid_to": iso(fact.valid_to),
        "source": fact.source,
        "provenance": list(fact.provenance),
        **({"attributes": fact.attrs} if fact.attrs else {}),
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return value


# ------------------------------------------------------------------------------------- routes


@router.get("/ontology", dependencies=requires(READ))
async def vocabulary(request: Request):
    """What may be said: classes, relations (with domain and range), attributes, hazards."""
    return ontology_of(request, world_of(request)).vocabulary()


@router.get("/summary", dependencies=requires(READ))
async def summary(request: Request):
    """How big the world is, and anything the projector could not place."""
    world = world_of(request)
    classes = {}
    for entity_id in world.entities():
        name = world.entity_class(entity_id)
        classes[name] = classes.get(name, 0) + 1
    return {
        "entities": dict(sorted(classes.items())),
        "facts": world.graph.number_of_edges(),
        "skipped": world.report.get("skipped", {}),
        "report": json_safe(world.report),
    }


@router.get("/entities", dependencies=requires(READ))
async def entities(
    request: Request,
    entity_class: str | None = Query(default=None, alias="class"),
    limit: int = Query(default=200, ge=1, le=2000),
):
    world = world_of(request)
    if entity_class and entity_class not in world.definition.classes:
        raise HTTPException(422, f"unknown class {entity_class!r}")
    ids = world.entities(entity_class)
    return {
        "class": entity_class,
        "total": len(ids),
        "entities": [
            {"id": i, "class": world.entity_class(i), "label": world.label(i)} for i in ids[:limit]
        ],
        "truncated": len(ids) > limit,
    }


@router.get("/entity", dependencies=requires(READ))
async def entity(request: Request, id: str):
    world = world_of(request)
    entity_id = known(world, id)
    return {
        "id": entity_id,
        "class": world.entity_class(entity_id),
        "label": world.label(entity_id),
        "meta": json_safe(world.meta(entity_id)),
        "attributes": {k: attribute_json(v) for k, v in world.attributes(entity_id).items()},
        "out": [fact_json(f) for f in world.facts(entity_id, direction="out")],
        "in": [fact_json(f) for f in world.facts(entity_id, direction="in")],
    }


@router.get("/neighbourhood", dependencies=requires(READ))
async def around(
    request: Request,
    entity: str,
    depth: int = Query(default=2, ge=0, le=4),
    predicate: list[str] = Query(default_factory=list),
    topology: bool = False,
    max_nodes: int = Query(default=200, ge=1, le=1000),
    min_confidence: float = Query(default=0.0, ge=0.0, le=1.0),
    as_of: datetime | None = None,
):
    """A picture of what surrounds an entity, ready to draw. Unknown ids answer 404."""
    world = world_of(request)
    entity_id = known(world, entity)
    return json_safe(
        neighbourhood(
            world,
            entity_id,
            depth,
            predicates=predicate or None,
            include_topology=topology,
            max_nodes=max_nodes,
            as_of=as_of,
            min_confidence=min_confidence,
        )
    )


@router.get("/impact", dependencies=requires(READ))
async def impact(request: Request, entity: str):
    """What does this closure (or any entity going out of use) affect?"""
    world = world_of(request)
    return json_safe(world.impact_report(known(world, entity)).summary())


@router.get("/link", dependencies=requires(READ))
async def resolve(
    request: Request,
    text: str = Query(max_length=500),
    context: list[str] = Query(default_factory=list),
):
    """Which entity does this name or id refer to? Ambiguity is returned, never guessed."""
    return link(text, world_of(request), context=context).model_dump(mode="json")


@router.post("/validate", dependencies=requires(READ))
async def validate(body: ClaimCheck, request: Request):
    """Would each claim be accepted? Every rejection carries its code and reason."""
    world = world_of(request)
    ontology = ontology_of(request, world)
    evidence = EvidenceIndex(request.app.state.runtime) if body.check_evidence else None
    results = []
    for claim in body.claims:
        violations = [v.model_dump(mode="json") for v in ontology.validate(claim)]
        if evidence is not None:
            if not claim.evidence_ids:
                violations.append({"code": "no_evidence", "message": "the claim cites no evidence"})
            violations += [
                {"code": "fake_evidence", "message": f"evidence {ref!r} does not exist"}
                for ref in claim.evidence_ids
                if not evidence.exists(ref)
            ]
        results.append(
            {
                "claim": claim.model_dump(mode="json"),
                "accepted": not violations,
                "violations": violations,
            }
        )
    return {"accepted": all(r["accepted"] for r in results), "results": results}


@router.post("/check", dependencies=requires(READ))
async def check(body: TextInput, request: Request):
    """Does this passage name anything that does not exist? (The guard against invented entities.)"""
    world = world_of(request)
    guard = EntityGuard(
        ontology_of(request, world),
        world,
        context=body.context,
        strict_ambiguity=body.strict_ambiguity,
    )
    return guard.check_text(body.text).model_dump(mode="json")


@router.post("/mentions", dependencies=requires(READ))
async def mentions(body: TextInput, request: Request):
    """Everything a passage refers to: resolved, ambiguous, and references that match nothing."""
    return find_mentions(body.text, world_of(request), context=body.context).model_dump(mode="json")
