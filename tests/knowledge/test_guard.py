"""The guard: one check for claims and for prose, that only reads and never repairs."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from atlas.intelligence.contracts import Advice, GroundedClaim
from atlas.knowledge.guard import EntityGuard
from atlas.knowledge.ontology import Claim, YamlOntology

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
RSH1 = "shelter:ratnapura-shelter-01"


@pytest.fixture
def guard(multisite_world):
    return EntityGuard(YamlOntology.load(graph=multisite_world), multisite_world)


def claim(subject, predicate, obj, unit=None, evidence=("ev-1",)):
    return Claim(
        subject=subject, predicate=predicate, object=obj, unit=unit, evidence_ids=list(evidence)
    )


def advice(summary="", concerns=(), questions=(), claims=()):
    return Advice(
        incident_id="incident:ratnapura-demo-river",
        cycle=1,
        created_at=NOW,
        summary=summary,
        concerns=list(concerns),
        questions=list(questions),
        claims=list(claims),
    )


def grounded(text, c):
    return GroundedClaim(text=text, claim=c)


# ---------------------------------------------------------------------------------- claims


def test_true_claims_pass_and_invented_ones_come_back_as_violations(guard):
    good = claim(RSH1, "capacity", 120, "people")
    invented = claim("shelter:ratnapura-shelter-99", "capacity", 80, "people")
    violations = guard.check_claims([good, invented])
    assert [(v.claim.subject, v.code) for v in violations] == [
        ("shelter:ratnapura-shelter-99", "unknown_entity")
    ]


def test_no_claims_no_violations(guard):
    assert guard.check_claims([]) == []


# ----------------------------------------------------------------------------------- prose


def test_clean_prose_is_ok(guard):
    check = guard.check_text("Ratnapura shelter 1 has room and the Kalu Ganga is rising.")
    assert check.ok and check.problems == []
    assert {m.entity_id for m in check.mentions.resolved} == {RSH1, "area:ratnapura-demo-river"}


def test_prose_naming_something_that_does_not_exist_is_not_ok_and_says_what(guard):
    check = guard.check_text("Shelter 7 is full and the bridge at Kuruwita is out.")
    assert not check.ok
    assert check.problems == [
        "'Shelter 7' does not match anything known",
        "'bridge at Kuruwita' does not match anything known",
    ]


def test_an_ambiguous_reference_is_a_problem_by_default(guard):
    check = guard.check_text("Shelter 1 is full.")
    assert not check.ok and "could be any of" in check.problems[0]
    assert "shelter:kandy-shelter-01" in check.problems[0] and RSH1 in check.problems[0]


def test_ambiguity_can_be_tolerated_but_inventions_never_are(multisite_world):
    lenient = EntityGuard(
        YamlOntology.load(graph=multisite_world), multisite_world, strict_ambiguity=False
    )
    assert lenient.check_text("Shelter 1 is full.").ok
    assert not lenient.check_text("Shelter 7 is full.").ok


def test_context_settles_an_ambiguous_reference(multisite_world):
    guard = EntityGuard(
        YamlOntology.load(graph=multisite_world), multisite_world, context=["ratnapura"]
    )
    assert guard.check_text("Shelter 1 is full.").ok


# ---------------------------------------------------------------------------------- advice


def test_advice_with_nothing_invented_is_ok(guard):
    result = guard.check_advice(
        advice(
            summary="Send groups to Ratnapura shelter 1.",
            concerns=["The Kalu Ganga is rising."],
            questions=["Is the road to Ratnapura shelter 1 open?"],
            claims=[
                grounded("Ratnapura shelter 1 holds 120.", claim(RSH1, "capacity", 120, "people"))
            ],
        )
    )
    assert result.ok and result.issues == []


def test_every_part_of_the_advice_is_checked_and_the_issue_says_where(guard):
    result = guard.check_advice(
        advice(
            summary="Move people to Shelter 7.",
            concerns=["fine", "Kuruwita bridge is out"],
            questions=["Is Shelter 9 open?"],
            claims=[
                grounded(
                    "Shelter 7 holds 300.",
                    claim("shelter:ratnapura-shelter-99", "capacity", 300, "people"),
                ),
            ],
        )
    )
    assert not result.ok
    found = {(i.where, i.kind, i.text) for i in result.issues}
    assert ("summary", "unresolved", "Shelter 7") in found
    assert ("concerns[1]", "unresolved", "Kuruwita bridge") in found
    assert ("questions[0]", "unresolved", "Shelter 9") in found
    assert ("claims[0].text", "unresolved", "Shelter 7") in found
    assert ("claims[0]", "claim", "unknown_entity") in found
    assert not any(i.where == "concerns[0]" for i in result.issues)


def test_a_true_claim_with_invented_wording_is_still_flagged(guard):
    """The claim is fine; the sentence shown to a person names a shelter that is not real."""
    result = guard.check_advice(
        advice(claims=[grounded("Shelter 7 holds 120.", claim(RSH1, "capacity", 120, "people"))])
    )
    assert [(i.where, i.kind) for i in result.issues] == [("claims[0].text", "unresolved")]


def test_checking_advice_changes_nothing(guard):
    item = advice(
        summary="Move to Shelter 7.", claims=[grounded("x", claim(RSH1, "capacity", 120, "people"))]
    )
    before = item.model_dump()
    guard.check_advice(item)
    assert item.model_dump() == before
    assert all(
        not c.accepted and c.violations == [] for c in item.claims
    )  # it does not grade the claims


def test_anything_shaped_like_advice_can_be_checked(guard):
    duck = SimpleNamespace(summary="Move to Shelter 7.", concerns=None, questions=[], claims=None)
    assert [i.text for i in guard.check_advice(duck).issues] == ["Shelter 7"]
    assert guard.check_advice(SimpleNamespace()).ok


def test_ambiguity_is_reported_in_advice_unless_tolerated(multisite_world):
    strict = EntityGuard(YamlOntology.load(graph=multisite_world), multisite_world)
    lenient = EntityGuard(
        YamlOntology.load(graph=multisite_world), multisite_world, strict_ambiguity=False
    )
    item = advice(summary="Shelter 1 is full.")
    assert [i.kind for i in strict.check_advice(item).issues] == ["ambiguous"]
    assert lenient.check_advice(item).ok
