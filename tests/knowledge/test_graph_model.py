"""The projected graph's own model: entities, typed attributes, and facts with time and confidence."""

from datetime import datetime, timedelta, timezone

import pytest

from atlas.knowledge.graph import AttributeValue, Fact, InMemoryWorldGraph, ProjectedWorldGraph

T0 = datetime(2026, 9, 26, 10, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


@pytest.fixture
def g():
    graph = ProjectedWorldGraph()
    graph.add_entity("shelter:s1", "Shelter", label="Shelter one", lat=6.7)
    graph.add_entity("area:a1", "Area")
    graph.add_entity("area:a2", "Area")
    graph.add_entity("road:1-2", "Road")
    return graph


def test_it_is_still_an_in_memory_world_graph(g):
    assert isinstance(g, InMemoryWorldGraph)
    g.add_fact("road:1-2", "located_in", "area:a1")
    assert g.neighbors("road:1-2") == ["area:a1"] and g.path("road:1-2", "area:a1")
    assert g.has_entity("road:1-2") and g.entity_class("road:1-2") == "Road"


def test_entities_are_listed_by_class_in_order(g):
    assert g.entities() == ["area:a1", "area:a2", "road:1-2", "shelter:s1"]
    assert g.entities("Area") == ["area:a1", "area:a2"] and g.entities("Gauge") == []


def test_a_fact_about_an_unknown_thing_does_not_make_it_an_entity(g):
    g.add_fact("shelter:s1", "serves", "area:nowhere")
    assert g.has_entity("shelter:s1") and not g.has_entity("area:nowhere")
    assert "area:nowhere" not in g.entities()


def test_labels_and_details_are_kept_apart_from_ontology_attributes(g):
    assert g.label("shelter:s1") == "Shelter one" and g.label("area:a1") == "area:a1"
    assert g.label("ghost") == "" and g.meta("shelter:s1") == {"label": "Shelter one", "lat": 6.7}
    assert g.meta("ghost") == {} and g.attributes("shelter:s1") == {}


def test_adding_an_entity_again_updates_it_without_losing_its_attributes(g):
    g.set_attribute("shelter:s1", "capacity", 120, unit="people")
    g.add_entity("shelter:s1", "Shelter", label="Renamed")
    assert g.label("shelter:s1") == "Renamed" and g.attribute("shelter:s1", "capacity").value == 120


def test_attributes_carry_their_unit_confidence_time_and_provenance(g):
    g.set_attribute(
        "shelter:s1",
        "occupancy",
        34,
        unit="people",
        confidence=0.8,
        valid_from=T0,
        provenance=["ev-1", "ev-2"],
        source="cop",
    )
    value = g.attribute("shelter:s1", "occupancy")
    assert value == AttributeValue(34, "people", 0.8, T0, ("ev-1", "ev-2"), "cop")
    assert g.attribute("shelter:s1", "capacity") is None and g.attribute("ghost", "x") is None
    g.set_attribute("shelter:s1", "occupancy", 40, unit="people")
    assert g.attribute("shelter:s1", "occupancy").value == 40  # the latest value replaces it


def test_facts_come_back_with_where_they_came_from(g):
    g.add_fact(
        "road:1-2",
        "located_in",
        "area:a1",
        confidence=0.7,
        valid_from=T0,
        provenance=["data/osm"],
        source="derived",
        basis="nearest area centre",
    )
    (fact,) = g.facts("road:1-2")
    assert fact == Fact(
        "road:1-2", "located_in", "area:a1", 0.7, T0, None, ("data/osm",), "derived"
    )
    assert fact.attrs == {"basis": "nearest area centre"}


def test_facts_can_be_read_from_either_end(g):
    g.add_fact("road:1-2", "located_in", "area:a1")
    g.add_fact("shelter:s1", "located_in", "area:a1")
    assert [f.subject for f in g.facts("area:a1")] == []
    assert [f.subject for f in g.facts("area:a1", direction="in")] == ["road:1-2", "shelter:s1"]
    assert len(g.facts("area:a1", direction="both")) == 2
    assert g.facts("ghost") == []


def test_facts_can_be_filtered_by_predicate_and_come_back_sorted(g):
    g.add_fact("shelter:s1", "serves", "area:a2")
    g.add_fact("shelter:s1", "located_in", "area:a1")
    g.add_fact("shelter:s1", "serves", "area:a1")
    assert [(f.predicate, f.object) for f in g.facts("shelter:s1")] == [
        ("located_in", "area:a1"),
        ("serves", "area:a1"),
        ("serves", "area:a2"),
    ]
    assert [f.object for f in g.facts("shelter:s1", "serves")] == ["area:a1", "area:a2"]


def test_a_fact_with_a_lower_confidence_can_be_left_out(g):
    g.add_fact("shelter:s1", "serves", "area:a1", confidence=0.9)
    g.add_fact("shelter:s1", "serves", "area:a2", confidence=0.4)
    assert [f.object for f in g.facts("shelter:s1", min_confidence=0.5)] == ["area:a1"]
    assert len(g.facts("shelter:s1", min_confidence=0.4)) == 2


@pytest.mark.parametrize(
    ("valid_from", "valid_to", "when", "holds"),
    [
        (None, None, None, True),  # always true, asked "now"
        (None, None, T0, True),
        (T0, None, T0 - HOUR, False),  # not yet true
        (T0, None, T0, True),  # starts exactly then
        (T0, None, T0 + HOUR, True),
        (T0, T0 + HOUR, T0 + HOUR, False),  # ended exactly then
        (T0, T0 + HOUR, T0 + HOUR / 2, True),
        (None, T0, None, False),  # asked "now": only open-ended facts hold
        (None, T0, T0 - HOUR, True),
    ],
)
def test_when_a_fact_holds(valid_from, valid_to, when, holds):
    assert Fact("a", "p", "b", valid_from=valid_from, valid_to=valid_to).holds_at(when) is holds


def test_the_graph_can_be_read_as_it_stood_at_a_time(g):
    g.add_fact("shelter:s1", "serves", "area:a1", valid_from=T0)
    g.add_fact("shelter:s1", "serves", "area:a2", valid_from=T0, valid_to=T0 + HOUR)
    assert [f.object for f in g.facts("shelter:s1", as_of=T0 - HOUR)] == []
    assert [f.object for f in g.facts("shelter:s1", as_of=T0 + HOUR / 2)] == ["area:a1", "area:a2"]
    assert [f.object for f in g.facts("shelter:s1", as_of=T0 + 2 * HOUR)] == ["area:a1"]
    assert [f.object for f in g.facts("shelter:s1")] == ["area:a1"]  # "now": the open-ended one


def test_derived_structures_are_thrown_away_when_the_graph_changes(g):
    g.cache["topology"] = "stale"
    g.add_fact("road:1-2", "located_in", "area:a1")
    assert g.cache == {}
    g.cache["x"] = 1
    g.set_attribute("road:1-2", "minutes", 2.0)
    assert g.cache == {}
    g.cache["x"] = 1
    g.add_entity("road:3-4", "Road")
    assert g.cache == {}


def test_a_new_graph_starts_with_an_empty_report():
    assert ProjectedWorldGraph().report == {} and ProjectedWorldGraph().definition is None
