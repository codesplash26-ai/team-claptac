"""The scaffold's contracts import, validate, and fail loudly where nothing is built yet."""

import importlib
from datetime import datetime, timezone

import pytest

from atlas.datasets.to_scenario import readings_to_scenario
from atlas.ingest.field import FieldReport, MissionAck
from atlas.ingest.lora import (
    SENSOR_TYPE_IDS,
    FrameType,
    LoraFrame,
    LoraGateway,
    MockLoraLink,
)
from atlas.ingest.sensors import SensorReading, SensorType
from atlas.intelligence.advisor import advise_without_llm
from atlas.intelligence.contracts import Advice, GroundedClaim
from atlas.intelligence.grounding import ground
from atlas.knowledge import Claim, InMemoryWorldGraph, PermissiveOntology
from atlas.knowledge.ontology import OntologyDefinition

MODULES = [
    "atlas.ingest.lora",
    "atlas.ingest.field",
    "atlas.knowledge",
    "atlas.knowledge.ontology",
    "atlas.knowledge.graph",
    "atlas.knowledge.linking",
    "atlas.intelligence",
    "atlas.intelligence.contracts",
    "atlas.intelligence.providers",
    "atlas.intelligence.advisor",
    "atlas.intelligence.grounding",
    "atlas.intelligence.nlquery",
    "atlas.intelligence.extraction",
    "atlas.datasets",
    "atlas.datasets.base",
    "atlas.datasets.lk_irrigation",
    "atlas.datasets.open_meteo",
    "atlas.datasets.usgs",
    "atlas.datasets.coolr",
    "atlas.datasets.to_scenario",
    "atlas.dashboard.dev",
    "atlas.dashboard.dev.trace",
    "atlas.dashboard.dev.catalog",
]
NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


@pytest.mark.parametrize("module", MODULES)
def test_every_scaffold_module_imports(module):
    importlib.import_module(module)


def test_every_sensor_type_has_an_on_air_id():
    assert set(SENSOR_TYPE_IDS) == set(SensorType)
    assert len(set(SENSOR_TYPE_IDS.values())) == len(SENSOR_TYPE_IDS)


def test_lora_frame_types_are_distinct_bytes():
    assert len({int(t) for t in FrameType}) == len(FrameType)
    assert all(0 <= int(t) <= 255 for t in FrameType)


def test_unbuilt_lora_gateway_fails_loudly_not_silently():
    gateway = LoraGateway(MockLoraLink(), codec=None, key=b"k", on_event=lambda e: None)
    with pytest.raises(NotImplementedError, match="Hasindu"):
        gateway.poll_once()
    frame = LoraFrame(1, 1, FrameType.READING, b"")
    with pytest.raises(NotImplementedError, match="Hasindu"):
        gateway.readings_from(frame, NOW)


def test_mock_link_round_trips_bytes():
    link = MockLoraLink()
    link.inbox.append(b"abc")
    assert link.receive(0.1) == b"abc" and link.receive(0.1) is None
    link.send(b"ack")
    assert link.sent == [b"ack"]


def test_field_messages_validate():
    FieldReport(node_id="n1", timestamp=NOW, report_id="r1", area_id="a", kind="need", people=3)
    MissionAck(node_id="n1", timestamp=NOW, mission_id="m-1", change="progress", percent=40)
    with pytest.raises(ValueError):
        FieldReport(
            node_id="n1", timestamp=datetime(2026, 1, 1), report_id="r", area_id="a", kind="need"
        )
    with pytest.raises(ValueError):
        MissionAck(node_id="n", timestamp=NOW, mission_id="m", change="progress", percent=101)


def test_permissive_ontology_and_graph_stand_in_until_the_real_ones_land():
    ontology, graph = PermissiveOntology(), InMemoryWorldGraph()
    graph.add_entity("area:a", "Area")
    graph.add_entity("road:1-2", "Road")
    graph.add_fact("road:1-2", "located_in", "area:a")
    claim = Claim(subject="road:1-2", predicate="located_in", object="area:a", evidence_ids=["e1"])
    assert ontology.validate(claim) == []
    assert graph.neighbors("road:1-2") == ["area:a"] and graph.impact("area:a") == ["road:1-2"]
    assert graph.path("road:1-2", "area:a") == ["road:1-2", "area:a"]
    assert graph.path("area:a", "road:1-2") is None and not graph.has_entity("ghost")


def test_ontology_file_parses_into_its_definition():
    import yaml

    data = yaml.safe_load(open("config/ontology.yaml", encoding="utf-8"))
    definition = OntologyDefinition.model_validate(data)
    assert {"Area", "Road", "Shelter"} <= set(definition.classes)
    assert all(
        set(rel["domain"] + rel["range"]) <= set(definition.classes)
        for rel in definition.relations.values()
    )


def test_grounding_rejects_claims_without_evidence():
    good = GroundedClaim(
        text="t", claim=Claim(subject="a:1", predicate="p", object="b:1", evidence_ids=["e1"])
    )
    no_evidence = GroundedClaim(text="t", claim=Claim(subject="a:1", predicate="p", object="b:1"))
    fake_evidence = GroundedClaim(
        text="t", claim=Claim(subject="a:1", predicate="p", object="b:1", evidence_ids=["missing"])
    )
    advice = Advice(
        incident_id="i",
        cycle=1,
        created_at=NOW,
        summary="s",
        claims=[good, no_evidence, fake_evidence],
    )
    ground(advice, PermissiveOntology(), lambda ref: ref == "e1")
    assert [c.accepted for c in advice.claims] == [True, False, False]


def test_template_advice_needs_no_llm():
    advice = advise_without_llm(
        {"incident_id": "i", "cycle": 2, "summary": "plan", "notes": ["n"]}, NOW
    )
    assert advice.source == "template" and advice.summary == "plan" and advice.trace.model is None


def test_readings_convert_to_a_replayable_scenario():
    def reading(seconds, value):
        return SensorReading(
            node_id="n",
            sensor_id="wl",
            type="water_level",
            value=value,
            unit="cm",
            timestamp=datetime.fromtimestamp(1_800_000_000 + seconds, timezone.utc),
            location={"lat": 6.68, "lon": 80.4, "area_id": "ratnapura-demo-river"},
        )

    scenario = readings_to_scenario([reading(600, 20), reading(0, 10)], "real-flood", speedup=60)
    assert [e["at_seconds"] for e in scenario["events"]] == [0, 10]
    assert scenario["events"][0]["payload"]["value"] == 10
    assert "timestamp" not in scenario["events"][0]["payload"]
    assert len({e["payload"]["measurement_id"] for e in scenario["events"]}) == 2
    with pytest.raises(ValueError):
        readings_to_scenario([], "x")
    with pytest.raises(ValueError):
        readings_to_scenario([reading(0, 1)], "x", speedup=0)


def test_converted_scenario_replays_through_the_real_pipeline(runtime, clock):
    import asyncio

    from atlas.ingest.simulator import replay

    def reading(seconds, value):
        return SensorReading(
            node_id="n",
            sensor_id="wl",
            type="water_level",
            value=value,
            unit="cm",
            timestamp=datetime.fromtimestamp(1_800_000_000 + seconds, timezone.utc),
            location={"lat": 6.68, "lon": 80.4, "area_id": "ratnapura-demo-river"},
        )

    scenario = readings_to_scenario([reading(0, 10), reading(120, 26), reading(130, 26)], "s")
    asyncio.run(_replay(runtime, scenario, clock, replay))
    types = [r["payload"]["type"] for r in runtime.ledger.records("event")]
    assert "observation_validated" in types and "trigger" in types


async def _replay(runtime, scenario, clock, replay):
    await runtime.start()
    await replay(runtime, scenario, clock)
