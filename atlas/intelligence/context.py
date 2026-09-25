"""Detached evidence view: only exact snapshot facts can be accepted as claims.

This is a conservative intelligence-layer adapter, not the team's domain ontology.
It implements that seam without accepting invented entities while the ontology is unfinished.
"""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256

from atlas.intelligence.contracts import GroundedClaim
from atlas.knowledge.ontology import Claim, Violation
from atlas.tools.llm import ToolSpec


def render_claim(claim):
    value = json.dumps(claim.object, ensure_ascii=False)
    labels = {
        "risk_level": "Risk level",
        "water_level": "Water level",
        "hazard": "Hazard",
        "summary": "Plan summary",
        "state": "Incident state",
        "needs_human": "Human review required",
    }
    label = labels.get(claim.predicate, claim.predicate)
    return f"{claim.subject} · {label}: {value}"


class ReadContext:
    def __init__(self):
        self.facts = []
        self.evidence = {}

    def add(self, subject, body, *, confidence=None, provenance=None, observed_at=None):
        document = {
            "subject": subject,
            "body": deepcopy(body),
            "provenance": provenance or [],
            "confidence": confidence,
            "observed_at": observed_at,
        }
        ref = "snapshot:" + sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
        self.evidence[ref] = document

        def flatten(value, path):
            if isinstance(value, dict):
                for key, item in value.items():
                    flatten(item, f"{path}.{key}" if path else key)
            elif isinstance(value, list):
                # Long road node arrays are not useful narrative evidence.
                if path.endswith("nodes") or path.endswith("evidence_ids"):
                    return
                for i, item in enumerate(value[:30]):
                    flatten(item, f"{path}[{i}]")
            elif isinstance(value, (str, int, float, bool)) and path:
                self.facts.append(
                    Claim(
                        subject=subject,
                        predicate=path,
                        object=value,
                        evidence_ids=[ref],
                        confidence=confidence,
                    )
                )

        flatten(body, "")
        return ref

    def __call__(self, ref):
        return ref in self.evidence

    def supports(self, claim):
        return any(
            f.subject == claim.subject
            and f.predicate == claim.predicate
            and type(f.object) is type(claim.object)
            and f.object == claim.object
            and set(f.evidence_ids) <= set(claim.evidence_ids)
            and (
                claim.confidence is None
                or (f.confidence is not None and claim.confidence <= f.confidence)
            )
            for f in self.facts
        )

    def validate(self, claim):
        if self.supports(claim):
            return []
        return [
            Violation(
                claim=claim,
                code="unsupported_claim",
                message="The cited snapshot does not support this exact entity, value and confidence.",
            )
        ]

    def entity_type(self, entity_id):
        return (
            entity_id.split(":", 1)[0] if any(f.subject == entity_id for f in self.facts) else None
        )

    def relations(self, class_name):
        return sorted(
            {f.predicate for f in self.facts if self.entity_type(f.subject) == class_name}
        )

    def search(self, query="", limit=40):
        words = set(query.lower().replace("?", "").split()) - {
            "what",
            "which",
            "is",
            "are",
            "the",
            "a",
            "for",
            "was",
            "why",
            "of",
            "to",
            "in",
        }
        ranked = sorted(
            self.facts,
            key=lambda f: -sum(w in f"{f.subject} {f.predicate} {f.object}".lower() for w in words),
        )
        if words:
            ranked = [
                f
                for f in ranked
                if any(w in f"{f.subject} {f.predicate} {f.object}".lower() for w in words)
            ]
        return [f.model_dump(mode="json") for f in ranked[: max(1, min(limit, 80))]]

    def tool_specs(self):
        return [
            ToolSpec(
                "search_facts",
                "Read exact COP, incident, plan and approval snapshot facts.",
                {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                self.search,
            ),
            ToolSpec(
                "graph_neighbors",
                "Read explicit entity references in this snapshot only.",
                {
                    "type": "object",
                    "properties": {"entity_id": {"type": "string"}},
                    "required": ["entity_id"],
                    "additionalProperties": False,
                },
                self.graph_neighbors,
            ),
        ]

    def graph_neighbors(self, entity_id):
        subjects = {f.subject for f in self.facts}
        if entity_id not in subjects:
            return {"known": False, "links": []}
        links = []
        for fact in self.facts:
            if not isinstance(fact.object, str):
                continue
            target = fact.object
            # Only normalize a typed id field when its target already exists in the snapshot.
            prefix = fact.predicate.rsplit(".", 1)[-1].removesuffix("_id")
            if target not in subjects and fact.predicate.endswith("_id"):
                target = prefix + ":" + target
            if (
                target in subjects
                and target != fact.subject
                and entity_id in (fact.subject, target)
            ):
                links.append(
                    {
                        "subject": fact.subject,
                        "predicate": fact.predicate,
                        "target": target,
                        "claim": fact.model_dump(mode="json"),
                    }
                )
        return {"known": True, "links": links[:80], "complete_domain_graph": False}

    def claims(self, query="", limit=10):
        return [
            GroundedClaim(text=render_claim(c), claim=c)
            for row in self.search(query, limit)
            for c in [Claim.model_validate(row)]
        ]

    def overview(self, incident, limit=12):
        """Prioritize the actual incident summary and uncertainty over arbitrary road-id order."""
        ident = incident["incident_id"]
        subject = ident if ident.startswith("incident:") else "incident:" + ident
        area = "area:" + incident.get("area_id", "")
        rows = [f for f in self.facts if f.subject in {subject, area}]
        priorities = {
            "summary": 0,
            "risk_level": 1,
            "hazard": 2,
            "state": 3,
            "needs_human": 4,
            "water_level": 5,
        }
        rows.sort(
            key=lambda f: priorities.get(
                f.predicate,
                6
                if "information_gaps" in f.predicate
                else 7
                if f.predicate.endswith(".status")
                else 20,
            )
        )
        return [
            GroundedClaim(text=render_claim(f), claim=f.model_copy(deep=True)) for f in rows[:limit]
        ] or self.claims(limit=limit)


def capture(system, incident=None):
    """Capture synchronously, before awaiting a provider. Never expose mutable runtime tools."""
    context = ReadContext()
    snapshot = system.runtime.snapshot()
    for belief in snapshot["beliefs"]:
        context.add(
            belief["subject"],
            {belief["attribute"]: belief["value"]},
            confidence=belief["confidence"],
            provenance=belief["provenance"],
            observed_at=belief["updated_at"],
        )
    for item in [incident] if incident else system.orchestration.commander.incidents():
        ident = item["incident_id"]
        context.add(ident if ident.startswith("incident:") else "incident:" + ident, item)
    for approval in system.control.approvals.pending():
        body = approval.model_dump(mode="json") if hasattr(approval, "model_dump") else approval
        context.add("approval:" + body["id"], body)
    return context
