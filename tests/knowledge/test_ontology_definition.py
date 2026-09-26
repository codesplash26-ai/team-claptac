"""The vocabulary file: it loads, it is internally consistent, and it agrees with the sensors."""

import copy

import pytest
import yaml
from pydantic import ValidationError

from atlas.ingest.sensors import SensorReading
from atlas.knowledge.ontology import DEFAULT_PATH, OntologyDefinition, YamlOntology

NOW = "2026-09-26T10:00:00+00:00"


@pytest.fixture(scope="module")
def raw():
    return yaml.safe_load(DEFAULT_PATH.read_text(encoding="utf-8"))


def broken(raw, edit):
    data = copy.deepcopy(raw)
    edit(data)
    return data


def test_the_shipped_vocabulary_loads_and_is_consistent():
    d = YamlOntology.read()
    assert {"Area", "Road", "Junction", "Shelter", "Gauge", "Incident", "Mission"} <= set(d.classes)
    assert {"Resource", "Group", "Hazard"} <= set(d.classes)
    assert {"uses", "routed_over", "destined_for", "placed_at", "assigned_to"} <= set(d.relations)
    assert set(d.hazards) == {"flood", "landslide", "cyclone", "earthquake", "tsunami", "drought"}


def test_every_class_prefix_is_unique_and_edge_is_an_alias_for_road():
    d = YamlOntology.read()
    prefixes = [spec["prefix"] for spec in d.classes.values()]
    assert len(set(prefixes)) == len(prefixes)
    assert d.class_of_prefix("edge") == "Road" == d.class_of_prefix("road")
    assert d.normalise_id("edge:12-34") == "road:12-34"
    assert d.normalise_id("  shelter:x ") == "shelter:x"
    assert d.normalise_id("no-colon") == "no-colon"


def test_dependency_relations_are_the_ones_impact_follows():
    d = YamlOntology.read()
    dependency = {n for n, r in d.relations.items() if r.get("dependency")}
    rollup = {n for n, r in d.relations.items() if r.get("rollup")}
    assert dependency == {"uses", "routed_over", "destined_for", "placed_at", "assigned_to"}
    assert rollup == {"part_of"}


def test_predicates_for_a_class_lists_relations_and_attributes():
    o = YamlOntology.vocabulary_only()
    shelter = o.relations("Shelter")
    assert {"capacity", "occupancy", "serves", "reachable_from", "located_in"} <= set(shelter)
    assert "uses" not in shelter and "uses" in o.relations("Mission")
    assert o.relations("Nonsense") == []


# ---------------------------------------------------- consistency is enforced


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (lambda d: d["relations"]["uses"].update(range=["Roadd"]), "unknown class 'Roadd'"),
        (lambda d: d["relations"]["uses"].update(domain=[]), "non-empty list"),
        (lambda d: d["relations"]["uses"].pop("range"), "`range` must be a non-empty list"),
        (
            lambda d: d["relations"]["reachable_from"].update(attributes=["speed"]),
            "'speed' is not an attribute",
        ),
        (lambda d: d["relations"]["uses"].update(rollup=True), "both `dependency` and `rollup`"),
        (
            lambda d: d["attributes"]["capacity"].update(applies_to=["Shelterr"]),
            "unknown class 'Shelterr'",
        ),
        (lambda d: d["attributes"]["capacity"].update(type="quantity"), "type must be one of"),
        (lambda d: d["attributes"]["risk_level"].pop("values"), "an enum needs `values`"),
        (lambda d: d["attributes"]["water_level"].update(min=10, max=1), "min is above max"),
        (
            lambda d: d["attributes"].update(uses={"applies_to": ["Road"], "type": "number"}),
            "both a relation and an attribute",
        ),
        (lambda d: d["classes"]["Shelter"].update(prefix="area"), "share the prefix 'area'"),
        (lambda d: d["classes"]["Shelter"].pop("prefix"), "needs a lowercase `prefix`"),
        (lambda d: d["classes"]["Road"].update(key_pattern="["), "not a valid regex"),
        (lambda d: d["id_aliases"].update(edge="rod"), "unknown prefix 'rod'"),
        (lambda d: d["id_aliases"].update(shelter="area"), "also a class prefix"),
        (lambda d: d["hazard_status"].update(meteor={"status": "live"}), "unknown hazard 'meteor'"),
        (lambda d: d["hazard_status"]["flood"].update(status="mostly"), "status must be one of"),
        (
            lambda d: d["hazard_status"]["flood"].update(signals=["wind"]),
            "'wind' is not an attribute",
        ),
        (lambda d: d["hazards"].append("tsunami"), "duplicates"),
        (lambda d: d["hazards"].append("volcano"), "'volcano' has no hazard_status"),
        (lambda d: d["aliases"].update({"harbour:x": ["y"]}), "not `class:key` with a known class"),
    ],
)
def test_an_inconsistent_vocabulary_is_rejected_at_load_time(raw, edit, expected):
    with pytest.raises(ValidationError, match=expected):
        OntologyDefinition.model_validate(broken(raw, edit))


def test_all_the_problems_are_reported_together_not_one_at_a_time(raw):
    def edit(d):
        d["relations"]["uses"].update(range=["Nope"])
        d["hazards"].append("volcano")

    with pytest.raises(ValidationError) as caught:
        OntologyDefinition.model_validate(broken(raw, edit))
    message = str(caught.value)
    assert "unknown class 'Nope'" in message and "'volcano' has no hazard_status" in message


# ------------------------------------------- it agrees with the rest of the system


SENSOR_UNITS_TO_PROBE = ["cm", "m", "mm", "%", "C", "hPa", "deg", "ft", "F", "people", "min"]


@pytest.mark.parametrize(
    "attribute",
    ["water_level", "rainfall", "soil_moisture", "temperature", "humidity", "pressure", "tilt"],
)
def test_sensor_attribute_units_are_exactly_the_ones_a_reading_accepts(attribute):
    """One set of units for a measurement: the ontology's and the sensor message's must not drift."""
    accepted = set()
    for unit in SENSOR_UNITS_TO_PROBE:
        try:
            SensorReading(
                node_id="n", sensor_id="s", type=attribute, value=1, unit=unit, timestamp=NOW
            )
        except ValidationError:
            continue
        accepted.add(unit)
    assert accepted == set(YamlOntology.read().attributes[attribute]["units"])


def test_soil_and_humidity_bounds_match_the_sensor_percentage_rule():
    attributes = YamlOntology.read().attributes
    for name in ("soil_moisture", "humidity"):
        assert (attributes[name]["min"], attributes[name]["max"]) == (0, 100)


def test_mission_statuses_are_the_ones_dispatch_uses():
    from atlas.agents.a8_dispatch import MissionStatus

    assert set(YamlOntology.read().attributes["status"]["values"]) == {
        s.value for s in MissionStatus
    }


def test_risk_levels_are_the_ones_the_hazard_agent_uses():
    from atlas.agents.a2_hazard_intel import LEVELS

    assert YamlOntology.read().attributes["risk_level"]["values"] == list(LEVELS)


def test_hazard_status_matches_what_the_live_loop_can_do():
    """docs/data-and-hazards.md: flood is live, landslide and cyclone partly, the rest history only."""
    status = {h: s["status"] for h, s in YamlOntology.read().hazard_status.items()}
    assert status == {
        "flood": "live",
        "landslide": "live",
        "cyclone": "partial",
        "earthquake": "live",
        "tsunami": "history_only",
        "drought": "history_only",
    }


def test_the_hazards_the_hazard_agent_can_emit_are_all_in_the_vocabulary():
    from atlas.agents.a2_hazard_intel import HAZARD_OF

    assert set(HAZARD_OF.values()) <= set(YamlOntology.read().hazards)
