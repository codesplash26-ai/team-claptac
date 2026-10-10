"""The evidence check: real ids are found, made-up ones are not, and nothing is read or leaked."""

import pytest

from atlas.intelligence.contracts import Advice, GroundedClaim
from atlas.intelligence.grounding import ground
from atlas.knowledge.evidence import EvidenceIndex
from atlas.knowledge.ontology import Claim, YamlOntology
from atlas.knowledge.world import WorldProjector

RSH1 = "shelter:ratnapura-shelter-01"


@pytest.fixture
def index(live, runtime):
    return EvidenceIndex(runtime)


def first_event(runtime, kind):
    return next(
        r["payload"] for r in runtime.ledger.records("event") if r["payload"]["type"] == kind
    )


def flip(text):
    """The same text with its last character changed: a near miss that must not count."""
    return text[:-1] + ("0" if text[-1] != "0" else "1")


# ================================================================================ what is real


async def test_evidence_store_ids_are_real(index, runtime):
    validated = first_event(runtime, "observation_validated")
    (ref,) = validated["payload"]["evidence_ids"][:1]
    assert index.exists(ref) and index.explain(ref).kind == "evidence"


async def test_ledger_events_are_real_by_prefixed_or_bare_id(index, runtime):
    event = first_event(runtime, "trigger")
    assert (
        index.exists(f"event:{event['id']}")
        and index.explain(f"event:{event['id']}").kind == "event"
    )
    assert index.exists(event["id"])  # the bare id, as an event's own id is written
    assert index.explain(event["id"]).kind == "event"


async def test_actions_are_real_with_or_without_a_phase(index, runtime):
    record = next(r for r in runtime.ledger.records("action"))
    _, key, phase = record["record_id"].split(":", 2)
    assert index.exists(f"action:{key}") and index.exists(f"action:{key}:{phase}")
    assert index.explain(f"action:{key}").kind == "action"
    assert not index.exists(f"action:{key}:no-such-phase") and not index.exists(
        f"action:{flip(key)}"
    )


async def test_reasoning_runs_are_real(index, runtime):
    runtime.ledger.append("reasoning:run-1", "reasoning", {"id": "run-1"})
    assert index.exists("reasoning:run-1") and index.explain("reasoning:run-1").kind == "reasoning"
    assert not index.exists("reasoning:run-2")


async def test_cop_beliefs_are_real_even_after_they_expire(index, runtime):
    subject = next(
        b["subject"] for b in runtime.cop.snapshot()["beliefs"] if b["subject"].startswith("edge:")
    )
    assert (
        index.exists(f"cop:{subject}/belief_open")
        and index.explain(f"cop:{subject}/belief_open").kind == "cop_belief"
    )
    assert index.exists("cop:area:ratnapura-demo-river/risk_level")
    assert not index.exists(f"cop:{subject}/no_such_attribute") and not index.exists(
        "cop:edge:1-2/belief_open"
    )
    assert not index.exists("cop:/belief_open") and not index.exists("cop:edge:1-2")


async def test_cop_versions_are_real_up_to_the_current_one(index, runtime):
    current = runtime.cop.version
    assert index.exists(f"cop-version:{current}") and index.exists("cop-version:1")
    assert index.exists(f"a4-routing@cop-v{current}")
    assert not index.exists(f"cop-version:{current + 1}") and not index.exists("cop-version:-3")
    assert not index.exists("cop-version:latest")


async def test_missions_and_incidents_are_real(index, live):
    mission = live.control.dispatch.list()[0]
    (incident,) = live.orchestration.commander.incidents()
    assert (
        index.exists(f"mission:{mission.id}")
        and index.explain(f"mission:{mission.id}").kind == "mission"
    )
    assert (
        index.explain(incident["incident_id"]).kind == "incident"
    )  # written as A3 stores it    assert not index.exists("mission:m-000000000000") and not index.exists("incident:nowhere")


async def test_a_plan_is_real_if_a_specialist_answered_that_task(index, runtime):
    reply = first_event(runtime, "candidate_plan")
    task_id = reply["payload"]["task_id"]
    assert index.exists(f"plan:{task_id}") and index.explain(f"plan:{task_id}").kind == "plan"
    assert not index.exists("plan:incident:nowhere:c9:routes")


async def test_sensor_map_areas_are_real(index):
    assert index.exists("sensor_map:ratnapura-demo-river")
    assert index.explain("sensor_map:ratnapura-demo-river").kind == "sensor_map"
    assert not index.exists("sensor_map:atlantis")


@pytest.mark.parametrize(
    "path",
    [
        "data/reference/shelters.json",
        "data/osm/ratnapura.json",
        "atlas/datasets/areas.py",
        "config/ontology.yaml",
    ],
)
async def test_repository_reference_files_are_real(index, path):
    assert index.exists(path) and index.explain(path).kind == "reference_file"


# ============================================================================ what is not real


@pytest.mark.parametrize(
    "made_up",
    [
        "",
        " ",
        "e1",
        "evidence",
        "the river gauge",
        "ev-1",
        "0" * 64,
        "f" * 64,
        "0" * 32,  # well-formed hashes of nothing
        "event:",
        "event:nope",
        "action:",
        "action:nope",
        "reasoning:",
        "cop:",
        "mission:",
        "plan:",
        "http://example.com/report",
        "shelter:ratnapura-shelter-01",  # an entity is not evidence
    ],
)
async def test_made_up_evidence_is_not_real(index, made_up):
    assert not index.exists(made_up)
    assert index.explain(made_up) is None


async def test_a_hash_one_character_off_a_real_one_is_not_evidence(index, runtime):
    real = first_event(runtime, "observation_validated")["payload"]["evidence_ids"][0]
    assert index.exists(real) and not index.exists(flip(real))
    event_id = first_event(runtime, "trigger")["id"]
    assert not index.exists(f"event:{flip(event_id)}")


@pytest.mark.parametrize(
    "hostile",
    [
        "data/../.env",
        "data/../../etc/passwd",
        "../.env",
        "/etc/passwd",
        "data/reference/../../.env",
        "atlas/../.gitignore",
        "data/reference",
        "data/reference/",
        "data/reference/nope.json",
        "docs/control-boundary.md",  # a real file, but not in a folder that holds reference data
    ],
)
async def test_paths_cannot_be_used_to_probe_files_outside_the_reference_folders(index, hostile):
    assert not index.exists(hostile)


@pytest.mark.parametrize("not_text", [None, 5, 3.2, b"abc", ["event:x"], {"a": 1}])
async def test_only_text_can_be_evidence(index, not_text):
    assert index.exists(not_text) is False


async def test_ids_with_stray_whitespace_are_not_forgiven(index, runtime):
    event_id = first_event(runtime, "trigger")["id"]
    assert index.exists(f"event:{event_id}")
    assert not index.exists(f" event:{event_id}") and not index.exists(f"event:{event_id}\n")


# ========================================================================= safe by construction


async def test_looking_things_up_changes_nothing(index, runtime):
    version = runtime.cop.version
    tail = runtime.db.connection.execute("SELECT MAX(seq) FROM ledger").fetchone()[0]
    for ref in (
        "event:x",
        "action:y",
        "cop:edge:1-2/belief_open",
        "plan:z",
        "data/reference/shelters.json",
    ):
        index.exists(ref)
    assert runtime.cop.version == version
    assert runtime.db.connection.execute("SELECT MAX(seq) FROM ledger").fetchone()[0] == tail


async def test_it_never_returns_content_only_whether_an_id_is_real(index, runtime):
    ref = first_event(runtime, "observation_validated")["payload"]["evidence_ids"][0]
    assert index.exists(ref) is True and set(index.explain(ref).model_dump()) == {"ref", "kind"}


async def test_it_can_be_passed_wherever_a_yes_or_no_function_is_expected(index):
    assert callable(index) and index("sensor_map:ratnapura-demo-river") is True


def test_a_system_without_dispatch_or_the_commander_just_finds_nothing_there(runtime):
    """No A8 missions table, no A3 incidents table: those lookups say 'not found', they do not crash."""
    index = EvidenceIndex(runtime)
    assert not index.exists("mission:m-1") and not index.exists("incident:x")
    assert index.exists("sensor_map:ratnapura-demo-river")


# ======================================================= with the real grounding (Aeshan's)


def advice_with(*claims):
    from datetime import datetime, timezone

    return Advice(
        incident_id="incident:ratnapura-demo-river",
        cycle=1,
        created_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        summary="s",
        claims=[GroundedClaim(text=text, claim=claim) for text, claim in claims],
    )


async def test_the_real_grounding_accepts_a_true_claim_and_rejects_the_invented_ones(live, runtime):
    world = WorldProjector.from_system(live).world()
    ontology = YamlOntology.load(graph=world)
    index = EvidenceIndex(runtime)
    real_evidence = first_event(runtime, "observation_validated")["payload"]["evidence_ids"][0]

    true_claim = Claim(
        subject=RSH1, predicate="capacity", object=120, unit="people", evidence_ids=[real_evidence]
    )
    invented_shelter = Claim(
        subject="shelter:ratnapura-shelter-99",
        predicate="capacity",
        object=80,
        unit="people",
        evidence_ids=[real_evidence],
    )
    fake_evidence = Claim(
        subject=RSH1, predicate="capacity", object=120, unit="people", evidence_ids=["made-up"]
    )
    no_evidence = Claim(subject=RSH1, predicate="capacity", object=120, unit="people")
    wrong_unit = Claim(
        subject=RSH1,
        predicate="capacity",
        object=120,
        unit="vehicles",
        evidence_ids=[real_evidence],
    )

    advice = ground(
        advice_with(
            ("true", true_claim),
            ("invented shelter", invented_shelter),
            ("fake evidence", fake_evidence),
            ("no evidence", no_evidence),
            ("wrong unit", wrong_unit),
        ),
        ontology,
        index.exists,
    )
    verdicts = {c.text: (c.accepted, sorted({v.code for v in c.violations})) for c in advice.claims}
    assert verdicts == {
        "true": (True, []),
        "invented shelter": (False, ["unknown_entity"]),
        "fake evidence": (False, ["no_evidence"]),
        "no evidence": (False, ["no_evidence"]),
        "wrong unit": (False, ["wrong_unit"]),
    }


async def test_a_claim_grounded_in_a_real_cop_belief_is_accepted(live, runtime):
    world = WorldProjector.from_system(live).world()
    ontology = YamlOntology.load(graph=world)
    road = next(r for r in world.entities("Road") if world.attribute(r, "belief_open"))
    belief = world.attribute(road, "belief_open")
    claim = Claim(
        subject=road,
        predicate="belief_open",
        object=belief.value,
        evidence_ids=[f"cop:edge:{road.split(':', 1)[1]}/belief_open"],
    )
    (item,) = ground(advice_with(("road", claim)), ontology, EvidenceIndex(runtime).exists).claims
    assert item.accepted and item.violations == []
