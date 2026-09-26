"""Entity linking: names resolve, ambiguity is returned not guessed, and inventions stay inventions."""

import pytest

from atlas.knowledge.linking import Link, find_mentions, link, normalise

RSH1 = "shelter:ratnapura-shelter-01"
KSH1 = "shelter:kandy-shelter-01"
RIVER = "area:ratnapura-demo-river"


# ============================================================================== normalisation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Ratnapura Shelter 1", "ratnapura shelter 1"),
        ("ratnapura-shelter-01", "ratnapura shelter 1"),  # leading zero dropped, hyphens are spaces
        ("  RATNAPURA   shelter #01 ", "ratnapura shelter 1"),
        ("Kalu Ganga", "kalu ganga"),
        ("Rathnapura", "rathnapura"),
        ("Colombo Fort (low-lying)", "colombo fort low lying"),
        ("Kaluthara", "kaluthara"),
        ("Café", "cafe"),
        ("road 007-0100", "road 7 100"),
        ("", ""),
    ],
)
def test_normalisation_ignores_case_punctuation_accents_and_leading_zeros(raw, expected):
    assert normalise(raw) == expected


# ================================================================================ exact ids


@pytest.mark.parametrize(
    "written",
    [RSH1, "edge:x", "gauge:rathnapura", "hazard:flood", "junction:5299475391", RIVER],
)
def test_a_real_id_links_to_itself_with_full_confidence(multisite_world, written):
    if written == "edge:x":
        written = "edge:" + next(iter(multisite_world.entities("Road"))).split(":", 1)[1]
    result = link(written, multisite_world)
    assert result.entity_id == multisite_world_normalised(written)
    assert result.confidence == 1.0 and result.reason == "exact id"
    assert result.entity_class == multisite_world.entity_class(result.entity_id)


def multisite_world_normalised(written):
    return written.replace("edge:", "road:")


def test_a_bare_osm_road_pair_is_a_road(multisite_world):
    road = next(iter(multisite_world.entities("Road")))
    pair = road.split(":", 1)[1]
    assert link(pair, multisite_world).entity_id == road
    assert link("99999999-88888888", multisite_world).entity_id is None


# ======================================================================== names and aliases


@pytest.mark.parametrize(
    ("written", "entity"),
    [
        ("Ratnapura shelter 1", RSH1),
        ("RATNAPURA SHELTER 01", RSH1),
        ("shelter 1 Ratnapura", RSH1),
        ("ratnapura-shelter-01", RSH1),
        ("Kandy shelter 1", KSH1),
        ("Kalu Ganga", RIVER),  # a configured alias
        ("Ratnapura riverside settlements", RIVER),  # the area's own name
        ("Rathnapura gauge", "gauge:rathnapura"),  # a generated name for a station
        ("Rathnapura station", "gauge:rathnapura"),
        ("flood", "hazard:flood"),
        ("floods", "hazard:flood"),
        ("Landslide", "hazard:landslide"),
    ],
)
def test_names_and_aliases_resolve_however_they_are_written(multisite_world, written, entity):
    result = link(written, multisite_world)
    assert result.entity_id == entity, result.reason
    assert 0.9 <= result.confidence < 1.0


def test_a_small_typo_in_a_name_still_links_but_at_lower_confidence(multisite_world):
    result = link("Ratnapra shelter 1", multisite_world)
    assert result.entity_id == RSH1 and result.reason == "fuzzy match"
    assert result.confidence < 0.9


# ============================================================================== ambiguity


def test_two_things_called_the_same_are_returned_not_guessed(multisite_world):
    result = link("shelter 1", multisite_world)
    assert result.entity_id is None and result.entity_class is None
    assert result.alternatives == [KSH1, RSH1] and "ambiguous" in result.reason


@pytest.mark.parametrize("context", [["ratnapura"], [RIVER], ["Ratnapura", "flood"]])
def test_context_can_settle_an_ambiguity(multisite_world, context):
    result = link("shelter 1", multisite_world, context=context)
    assert result.entity_id == RSH1 and result.reason == "resolved by context"


def test_context_that_does_not_help_leaves_it_ambiguous(multisite_world):
    for context in (["colombo"], [], ["shelter"]):
        assert link("shelter 1", multisite_world, context=context).entity_id is None


def test_context_is_only_used_to_choose_between_candidates_never_to_invent_one(multisite_world):
    result = link("shelter 7", multisite_world, context=["ratnapura"])
    assert result.entity_id is None


# ============================================================================ inventions


@pytest.mark.parametrize(
    "invented",
    [
        "shelter:ratnapura-shelter-99",  # well-formed, does not exist
        "shelter:ratnapura-shelter-09",  # one digit from a real one
        "hazard:meteor",
        "road:1-2",
        "gauge:rtp-node-99",
        "area:atlantis",
        # typos with no digits: fuzzy matching would "correct" these onto real entities
        "area:ratnapura-demo-rivr",
        "shelter:ratnapura-shelter-o1",
        "gauge:rathnapura-x",
        "shelter:ratnapura-sheltr-01",
    ],
)
def test_a_well_formed_id_that_does_not_exist_is_never_linked_to_something_real(
    multisite_world, invented
):
    result = link(invented, multisite_world)
    assert result.entity_id is None and result.alternatives == []
    assert result.reason in ("well-formed id but no such entity", "'meteor' is not a known hazard")


@pytest.mark.parametrize(
    "invented", ["Shelter 7", "ratnapura shelter 9", "Ratnapura shelter 11", "shelter 99"]
)
def test_a_name_with_the_wrong_number_is_never_fuzzy_matched_to_a_real_one(
    multisite_world, invented
):
    """'Shelter 7' must not quietly become 'shelter 1': the digits have to be identical."""
    result = link(invented, multisite_world)
    assert result.entity_id is None and result.alternatives == []


@pytest.mark.parametrize("nonsense", ["", "a", "zz", "the", "Kuruwita bridge", "bridge:kuruwita"])
def test_things_that_are_simply_not_there_do_not_link(multisite_world, nonsense):
    assert link(nonsense, multisite_world).entity_id is None


def test_a_number_written_as_a_word_is_not_matched(multisite_world):
    assert link("Ratnapura shelter one", multisite_world).entity_id is None


def test_a_weak_resemblance_is_offered_as_an_alternative_not_linked(multisite_world):
    result = link("Rathnapura area", multisite_world)
    assert result.entity_id is None  # close to a station name, but not the same thing


def test_the_result_is_a_plain_model(multisite_world):
    result = link("Kalu Ganga", multisite_world)
    assert isinstance(result, Link) and Link.model_validate(result.model_dump()) == result


def test_linking_against_the_toy_in_memory_world_works_too(world):
    assert link("shelter:ratnapura-shelter-01", world).entity_id == "shelter:ratnapura-shelter-01"
    assert link("Ratnapura shelter 1", world).entity_id == "shelter:ratnapura-shelter-01"
    assert link("Ratnapura shelter 2", world).entity_id == "shelter:ratnapura-shelter-02"
    assert link("Ratnapura shelter 3", world).entity_id is None


# ============================================================================ find_mentions

SENTENCES = [
    # (text, resolved ids, ambiguous texts, unresolved phrases)
    ("Shelter 7 is full.", [], [], ["Shelter 7"]),
    ("The bridge at Kuruwita washed out.", [], [], ["bridge at Kuruwita"]),
    ("Kuruwita bridge is closed.", [], [], ["Kuruwita bridge"]),
    ("ratnapura-shelter-01 still has room.", [RSH1], [], []),
    ("shelter:ratnapura-shelter-99 is full.", [], [], ["shelter:ratnapura-shelter-99"]),
    ("Reported at bridge:kuruwita.", [], [], ["bridge:kuruwita"]),
    ("Shelter 1 is full.", [], ["Shelter 1"], []),
    ("Flood warning for Ratnapura riverside settlements.", ["hazard:flood", RIVER], [], []),
    ("The river Kalu Ganga is rising.", [RIVER], [], []),
    ("12 families on the roof, water at 26 cm.", [], [], []),
    ("Everything is calm.", [], [], []),
    # a sentence-starting determiner plus a place word is not a named place
    ("The river is rising.", [], [], []),
    ("This bridge is closed.", [], [], []),
    ("The Shelter is full.", [], [], []),
    ("The Kuruwita bridge is closed.", [], [], ["Kuruwita bridge"]),
    ("Our Shelter 7 is full.", [], [], ["Shelter 7"]),
    ("Shelter 7 and shelter 7 again.", [], [], ["Shelter 7"]),
    ("Send help to junction 99999 and Shelter 8.", [], [], ["junction 99999", "Shelter 8"]),
]


@pytest.mark.parametrize(
    ("text", "resolved", "ambiguous", "unresolved"), SENTENCES, ids=[s[0] for s in SENTENCES]
)
def test_a_passage_is_split_into_what_resolves_what_is_ambiguous_and_what_is_invented(
    multisite_world, text, resolved, ambiguous, unresolved
):
    found = find_mentions(text, multisite_world)
    assert sorted(m.entity_id for m in found.resolved) == sorted(resolved)
    assert [m.text for m in found.ambiguous] == ambiguous
    assert found.unresolved == unresolved
    assert found.clean is (not ambiguous and not unresolved)


def test_context_resolves_an_ambiguous_mention_inside_a_passage(multisite_world):
    found = find_mentions("Shelter 1 is full.", multisite_world, context=["ratnapura"])
    assert [m.entity_id for m in found.resolved] == [RSH1] and found.clean


def test_a_real_junction_and_road_in_prose_resolve(multisite_world):
    road = next(iter(multisite_world.entities("Road")))
    text = f"Junction 5299475391 and {road} are on the route."
    found = find_mentions(text, multisite_world)
    assert {m.entity_id for m in found.resolved} == {"junction:5299475391", road} and found.clean


def test_the_longest_name_wins_so_a_name_is_not_split_into_smaller_ones(multisite_world):
    found = find_mentions("Ratnapura shelter 1 is open", multisite_world)
    assert [m.entity_id for m in found.resolved] == [RSH1] and found.ambiguous == []


def test_a_reference_is_reported_once_however_often_it_is_repeated(multisite_world):
    found = find_mentions("Shelter 7, then Shelter 7, then shelter 7.", multisite_world)
    assert found.unresolved == ["Shelter 7"]  # the first spelling, once


def test_scanning_leaves_the_graph_alone(multisite_world):
    before = sorted(multisite_world.graph.nodes)
    find_mentions("Shelter 7 and bridge at Kuruwita", multisite_world)
    assert sorted(multisite_world.graph.nodes) == before
