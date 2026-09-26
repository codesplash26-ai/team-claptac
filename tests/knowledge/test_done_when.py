"""The brief's "Done when", end to end, on the whole system after a flood, with Aeshan's real grounding.

1. A claim naming a shelter that does not exist is rejected, with a reason.
2. A true claim about a real shelter, with real evidence, is accepted.
3. impact("road:<u>-<v>") lists the missions, shelters and groups that depend on that road.
"""

from datetime import datetime, timezone

from atlas.intelligence.contracts import Advice, GroundedClaim
from atlas.intelligence.grounding import ground
from atlas.knowledge.evidence import EvidenceIndex
from atlas.knowledge.guard import EntityGuard
from atlas.knowledge.ontology import Claim, YamlOntology
from atlas.knowledge.world import WorldProjector

REAL_SHELTER = "shelter:ratnapura-shelter-01"


def advice_of(*claims):
    return Advice(
        incident_id="incident:ratnapura-demo-river",
        cycle=1,
        created_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
        summary="",
        claims=[GroundedClaim(text=text, claim=claim) for text, claim in claims],
    )


def real_evidence_id(runtime):
    for record in runtime.ledger.records("event"):
        if record["payload"]["type"] == "observation_validated":
            return record["payload"]["payload"]["evidence_ids"][0]
    raise AssertionError("the replay produced no validated observation")


async def test_a_claim_naming_a_shelter_that_does_not_exist_is_rejected_with_a_reason(
    live, runtime
):
    world = WorldProjector.from_system(live).world()
    ontology = YamlOntology.load(graph=world)
    invented = Claim(
        subject="shelter:ratnapura-shelter-99",
        predicate="capacity",
        object=200,
        unit="people",
        evidence_ids=[
            real_evidence_id(runtime)
        ],  # real evidence does not make an invented shelter real
    )
    (item,) = ground(
        advice_of(("Shelter 99 holds 200.", invented)), ontology, EvidenceIndex(runtime).exists
    ).claims
    assert not item.accepted
    assert [v.code for v in item.violations] == ["unknown_entity"]
    assert "shelter:ratnapura-shelter-99" in item.violations[0].message


async def test_a_true_claim_about_a_real_shelter_with_evidence_is_accepted(live, runtime):
    world = WorldProjector.from_system(live).world()
    ontology = YamlOntology.load(graph=world)
    stated = world.attribute(REAL_SHELTER, "capacity")  # what the reference data says it holds
    true_claim = Claim(
        subject=REAL_SHELTER,
        predicate="capacity",
        object=stated.value,
        unit="people",
        evidence_ids=[real_evidence_id(runtime)],
    )
    (item,) = ground(
        advice_of(("It holds people.", true_claim)), ontology, EvidenceIndex(runtime).exists
    ).claims
    assert item.accepted and item.violations == []


async def test_the_same_claim_without_evidence_is_still_rejected(live, runtime):
    world = WorldProjector.from_system(live).world()
    ontology = YamlOntology.load(graph=world)
    bare = Claim(subject=REAL_SHELTER, predicate="capacity", object=120, unit="people")
    (item,) = ground(advice_of(("x", bare)), ontology, EvidenceIndex(runtime).exists).claims
    assert not item.accepted and [v.code for v in item.violations] == ["no_evidence"]


async def test_impact_of_a_road_on_a_route_lists_its_missions_groups_and_shelter(live):
    world = WorldProjector.from_system(live).world()
    (state,) = live.orchestration.commander.incidents()
    group_id, info = next(
        (gid, i) for gid, i in state["missions"].items() if i.get("route") and gid.startswith("g")
    )
    route = [n for n in info["route"] if isinstance(n, int)]
    road = f"road:{route[0]}-{route[1]}"
    mission = f"mission:{info['mission_id']}"
    group = f"group:{state['incident_id'].removeprefix('incident:')}/{group_id}"
    shelter = f"shelter:{info['shelter_id']}"

    affected = world.impact(road)
    assert mission in affected and group in affected and shelter in affected
    assert affected == sorted(affected)  # the Protocol's flat, sorted list

    report = world.impact_report(road)
    assert mission in report.dependants["Mission"] and group in report.dependants["Group"]
    assert report.reasons[mission] == f"uses {road}"
    assert shelter in report.expecting
    assert report.summary()["entity"] == road


async def test_a_road_no_mission_uses_affects_no_mission(live):
    world = WorldProjector.from_system(live).world()
    used = {r for m in world.entities("Mission") for r in world.neighbors(m, "uses")}
    unused = next(r for r in world.entities("Road") if r not in used)
    assert not any(a.startswith(("mission:", "group:")) for a in world.impact(unused))


async def test_a_model_that_invents_a_place_in_its_advice_is_caught_before_a_person_sees_it(
    live, runtime
):
    world = WorldProjector.from_system(live).world()
    guard = EntityGuard(YamlOntology.load(graph=world), world)
    assert guard.check_text("Move families to Ratnapura shelter 1.").ok
    assert not guard.check_text("Move families to Ratnapura shelter 9.").ok
