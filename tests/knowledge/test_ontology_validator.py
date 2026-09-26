"""The validator: true claims pass, and everything a hallucinating model might write is rejected."""

import pytest

from atlas.knowledge.graph import InMemoryWorldGraph
from atlas.knowledge.ontology import Claim, PermissiveOntology, YamlOntology

SHELTER = "shelter:ratnapura-shelter-01"
AREA = "area:ratnapura-demo-river"
GAUGE = "gauge:rtp-node-01"
EV = ["e1"]


def claim(subject, predicate, obj, unit=None, evidence=EV):
    return Claim(
        subject=subject, predicate=predicate, object=obj, unit=unit, evidence_ids=list(evidence)
    )


def codes(ontology, c):
    return sorted(v.code for v in ontology.validate(c))


# ------------------------------------------------------------------ good claims

GOOD = [
    ("a shelter is in an area", claim(SHELTER, "located_in", AREA)),
    ("a shelter serves an area", claim(SHELTER, "serves", AREA)),
    ("a shelter is reachable from an area", claim(SHELTER, "reachable_from", AREA)),
    ("a shelter's capacity", claim(SHELTER, "capacity", 120, "people")),
    ("occupancy", claim(SHELTER, "occupancy", 34, "people")),
    ("a water level in cm", claim(GAUGE, "water_level", 26, "cm")),
    ("a water level in m", claim(GAUGE, "water_level", 0.26, "m")),
    ("a measured soil saturation at the top of its range", claim(GAUGE, "soil_moisture", 100, "%")),
    ("a sub-zero temperature", claim(GAUGE, "temperature", -3.5, "C")),
    ("rain on an area", claim(AREA, "rainfall", 42.5, "mm")),
    ("a road's belief of being open (no unit)", claim("road:10-20", "belief_open", 0.2)),
    ("a hard closure", claim("road:10-20", "closed", True)),
    ("a road named with the COP's edge: prefix", claim("edge:10-20", "closed", True)),
    ("a mission uses a road (edge: object)", claim("mission:m-abc123", "uses", "edge:10-20")),
    ("a mission is destined for a shelter", claim("mission:m-abc123", "destined_for", SHELTER)),
    ("a group is placed at a shelter", claim("group:g-1", "placed_at", SHELTER)),
    ("a group is routed over a road", claim("group:g-1", "routed_over", "road:20-30")),
    (
        "a resource is assigned to a mission",
        claim("resource:ratnapura-rescue-team-01", "assigned_to", "mission:m-abc123"),
    ),
    (
        "a mission is part of an incident",
        claim("mission:m-abc123", "part_of", "incident:ratnapura-demo-river"),
    ),
    (
        "an incident is about a hazard",
        claim("incident:ratnapura-demo-river", "about", "hazard:flood"),
    ),
    ("a hazard threatens an area", claim("hazard:flood", "threatens", AREA)),
    ("a hazard threatens a road", claim("hazard:landslide", "threatens", "road:10-20")),
    ("a risk level", claim(AREA, "risk_level", "warning")),
    ("a mission status", claim("mission:m-abc123", "status", "in_progress")),
    ("a road runs between junctions", claim("road:10-20", "from_junction", "junction:10")),
    (
        "a hazard the live loop cannot act on is still speakable",
        claim("hazard:earthquake", "threatens", AREA),
    ),
    ("with a confidence", Claim(subject=SHELTER, predicate="serves", object=AREA, confidence=0.7)),
]


@pytest.mark.parametrize(("why", "c"), GOOD, ids=[w for w, _ in GOOD])
def test_a_true_well_formed_claim_is_accepted(ontology, why, c):
    assert ontology.validate(c) == [], why


# ------------------------------------------------------------------- bad claims

BAD = [
    # --- things that do not exist
    (
        "an invented shelter",
        claim("shelter:ratnapura-shelter-99", "capacity", 80, "people"),
        ["unknown_entity"],
    ),
    (
        "an invented shelter as the object",
        claim("mission:m-abc123", "destined_for", "shelter:kalutara-shelter-07"),
        ["unknown_entity"],
    ),
    (
        "a road that is not in the graph",
        claim("road:99-100", "belief_open", 0.5),
        ["unknown_entity"],
    ),
    (
        "a gauge nobody installed",
        claim("gauge:rtp-node-99", "water_level", 10, "cm"),
        ["unknown_entity"],
    ),
    ("an invented area", claim(SHELTER, "located_in", "area:atlantis"), ["unknown_entity"]),
    (
        "a kind of thing that is not in the vocabulary",
        claim("bridge:kuruwita", "closed", True),
        ["unknown_entity"],
    ),
    (
        "a hazard that is not a hazard",
        claim("hazard:meteor", "threatens", AREA),
        ["unknown_entity"],
    ),
    # --- ids that are not even shaped like ids
    ("free text instead of an id", claim("Shelter 1", "capacity", 80, "people"), ["malformed_id"]),
    ("an empty key", claim("shelter:", "capacity", 80, "people"), ["malformed_id"]),
    ("a road key that is not two node ids", claim("road:banana", "closed", True), ["malformed_id"]),
    ("a road key with three parts", claim("road:1-2-3", "closed", True), ["malformed_id"]),
    (
        "a junction that is not a number",
        claim("junction:market", "located_in", AREA),
        ["malformed_id"],
    ),
    # --- predicates that are not in the vocabulary
    ("an invented predicate", claim(SHELTER, "is_flooded", True), ["unknown_predicate"]),
    ("a near miss on a real predicate", claim(SHELTER, "located", AREA), ["unknown_predicate"]),
    # --- a predicate used on the wrong class
    ("capacity on a road", claim("road:10-20", "capacity", 50, "people"), ["wrong_type"]),
    ("a shelter that 'uses' a road", claim(SHELTER, "uses", "road:10-20"), ["wrong_type"]),
    ("water level on a shelter", claim(SHELTER, "water_level", 5, "cm"), ["wrong_type"]),
    (
        "a relation with the wrong object class",
        claim(SHELTER, "located_in", SHELTER),
        ["wrong_type"],
    ),
    # wrong twice over: the subject is not in the domain and the object is not in the range
    (
        "a relation pointing the wrong way",
        claim(AREA, "serves", SHELTER),
        ["wrong_type", "wrong_type"],
    ),
    ("a relation whose object is not an id", claim(SHELTER, "located_in", 5), ["wrong_type"]),
    # --- units
    ("a water level in feet", claim(GAUGE, "water_level", 26, "ft"), ["wrong_unit"]),
    ("a water level with no unit", claim(GAUGE, "water_level", 26), ["wrong_unit"]),
    ("a rainfall in cm", claim(AREA, "rainfall", 4, "cm"), ["wrong_unit"]),
    ("capacity in vehicles", claim(SHELTER, "capacity", 5, "vehicles"), ["wrong_unit"]),
    ("a unit on a belief", claim("road:10-20", "belief_open", 0.3, "cm"), ["wrong_unit"]),
    ("a unit on a relation", claim(SHELTER, "located_in", AREA, "cm"), ["wrong_unit"]),
    # --- values out of range or not allowed
    ("a belief above 1", claim("road:10-20", "belief_open", 1.4), ["bad_value"]),
    ("a negative belief", claim("road:10-20", "belief_open", -0.1), ["bad_value"]),
    (
        "soil more than 100 percent saturated",
        claim(GAUGE, "soil_moisture", 130, "%"),
        ["bad_value"],
    ),
    ("a negative water level", claim(GAUGE, "water_level", -5, "cm"), ["bad_value"]),
    ("a negative capacity", claim(SHELTER, "capacity", -1, "people"), ["bad_value"]),
    ("a risk level nobody defined", claim(AREA, "risk_level", "apocalypse"), ["bad_value"]),
    (
        "a mission status nobody defined",
        claim("mission:m-abc123", "status", "exploded"),
        ["bad_value"],
    ),
    # --- values of the wrong kind
    ("a fractional head count", claim(SHELTER, "capacity", 12.5, "people"), ["wrong_type"]),
    ("a head count in words", claim(SHELTER, "capacity", "many", "people"), ["wrong_type"]),
    ("a flag as text", claim("road:10-20", "closed", "yes"), ["wrong_type"]),
    ("a flag as a number", claim("road:10-20", "closed", 1), ["wrong_type"]),
    (
        "a boolean where a level is expected",
        claim(GAUGE, "water_level", True, "cm"),
        ["wrong_type"],
    ),
    ("not a number", claim(GAUGE, "water_level", float("nan"), "cm"), ["wrong_type"]),
    ("infinity", claim(GAUGE, "water_level", float("inf"), "cm"), ["wrong_type"]),
]


@pytest.mark.parametrize(("why", "c", "expected"), BAD, ids=[w for w, *_ in BAD])
def test_what_a_hallucinating_model_writes_is_rejected_with_the_right_reason(
    ontology, why, c, expected
):
    assert codes(ontology, c) == expected, why


def test_every_violation_says_what_is_wrong_in_words(ontology):
    (v,) = ontology.validate(claim("shelter:ratnapura-shelter-99", "capacity", 80, "people"))
    assert v.code == "unknown_entity" and "ratnapura-shelter-99" in v.message
    assert (
        v.message.endswith("does not exist") and v.claim.subject == "shelter:ratnapura-shelter-99"
    )


def test_a_misspelt_predicate_suggests_the_real_one(ontology):
    (v,) = ontology.validate(claim(SHELTER, "located", AREA))
    assert "located_in" in v.message


def test_several_problems_in_one_claim_are_all_reported(ontology):
    found = ontology.validate(claim("shelter:ratnapura-shelter-99", "is_flooded", True))
    assert sorted(v.code for v in found) == ["unknown_entity", "unknown_predicate"]


def test_an_invented_object_is_reported_even_when_the_subject_is_real(ontology):
    found = ontology.validate(claim(SHELTER, "serves", "area:invented"))
    assert [v.code for v in found] == ["unknown_entity"] and "object" in found[0].message


def test_the_world_decides_what_class_an_id_really_is():
    """An id that says Shelter but that the world knows as a Road is a mismatch, not a shelter."""
    graph = InMemoryWorldGraph()
    graph.add_entity("shelter:odd", "Road")
    graph.add_entity(AREA, "Area")
    ontology = YamlOntology.load(graph=graph)
    (v,) = ontology.validate(claim("shelter:odd", "located_in", AREA))
    assert v.code == "wrong_type" and "is a Road in the world" in v.message


def test_an_edge_prefixed_id_is_the_same_road_as_a_road_prefixed_one(ontology):
    assert ontology.entity_type("edge:10-20") == ontology.entity_type("road:10-20") == "Road"
    assert codes(ontology, claim("edge:99-100", "closed", True)) == ["unknown_entity"]


# ------------------------------------------------------------------ entity_type


def test_entity_type_is_the_worlds_class_or_none(ontology):
    assert ontology.entity_type(SHELTER) == "Shelter"
    assert ontology.entity_type(GAUGE) == "Gauge"
    assert ontology.entity_type("hazard:flood") == "Hazard"
    for unknown in ("shelter:nope", "hazard:meteor", "bridge:x", "no-colon", "", "road:banana"):
        assert ontology.entity_type(unknown) is None


# --------------------------------------------------------------------- evidence


def test_evidence_is_left_to_grounding_unless_asked_for(world):
    assert (
        YamlOntology.load(graph=world).validate(claim(SHELTER, "serves", AREA, evidence=[])) == []
    )


def test_missing_evidence_is_a_violation_when_required(world):
    ontology = YamlOntology.load(graph=world, require_evidence=True)
    assert codes(ontology, claim(SHELTER, "serves", AREA, evidence=[])) == ["no_evidence"]
    assert codes(ontology, claim(SHELTER, "serves", AREA)) == []


def test_fake_evidence_is_a_violation_when_an_existence_check_is_given(world):
    ontology = YamlOntology.load(graph=world, evidence_exists=lambda ref: ref == "real")
    assert codes(ontology, claim(SHELTER, "serves", AREA, evidence=["real"])) == []
    assert codes(ontology, claim(SHELTER, "serves", AREA, evidence=["real", "made-up"])) == [
        "fake_evidence"
    ]


# ------------------------------------------------------------ failing closed


def test_an_ontology_without_a_graph_refuses_to_exist_rather_than_accept_everything():
    with pytest.raises(ValueError, match="needs a world graph"):
        YamlOntology.load()
    with pytest.raises(ValueError, match="vocabulary_only"):
        YamlOntology(YamlOntology.read(), None)


def test_vocabulary_only_checks_structure_and_values_but_not_existence():
    ontology = YamlOntology.vocabulary_only()
    assert ontology.validate(claim("shelter:anything-at-all", "capacity", 80, "people")) == []
    assert codes(ontology, claim("shelter:anything", "capacity", 80, "ft")) == ["wrong_unit"]
    assert codes(ontology, claim("shelter:anything", "is_flooded", True)) == ["unknown_predicate"]
    assert codes(ontology, claim("hazard:meteor", "threatens", AREA)) == ["unknown_entity"]
    assert ontology.entity_type("shelter:anything") is None  # it still does not claim to know it


def test_the_permissive_placeholder_is_still_what_it_was():
    """Unchanged, so nobody depending on it breaks; the real validator must reject what it accepts."""
    invented = claim("shelter:ratnapura-shelter-99", "capacity", 80, "ft")
    assert PermissiveOntology().validate(invented) != YamlOntology.vocabulary_only().validate(
        invented
    )
    assert PermissiveOntology().validate(invented) == []


def test_the_vocabulary_can_be_shown_to_a_model_or_a_person(ontology):
    vocab = ontology.vocabulary()
    assert {"classes", "relations", "attributes", "hazards"} == set(vocab)
    assert vocab["classes"]["Shelter"]["prefix"] == "shelter"
    assert vocab["relations"]["uses"]["dependency"] is True
    assert vocab["relations"]["part_of"]["rollup"] is True
    assert vocab["hazards"]["earthquake"]["status"] == "live"
