"""The developer view: who sent what to whom, and what caused it."""

from atlas.dashboard.dev.catalog import CATALOG, cards
from atlas.dashboard.dev.trace import chain, flow, get_event, node_of, summarise, trace
from atlas.ingest.simulator import replay
from tests.orchestration.conftest import reading, scenario

RISING = scenario(reading(0, 10), reading(120, 26), reading(130, 26))


async def loop(system, runtime, clock):
    await runtime.start()
    await replay(runtime, RISING, clock)
    for _ in range(3):
        await system.control.gateway.run_pending()


async def test_every_event_has_a_readable_summary_and_handlers(system, runtime, clock):
    await loop(system, runtime, clock)
    events = trace(runtime, limit=1000)
    assert len(events) > 30 and all(e.summary for e in events)
    assert all(e.handled_by for e in events if e.type in ("trigger", "task_assigned"))
    trigger = next(e for e in events if e.type == "trigger")
    assert "a2_hazard_intel" in trigger.handled_by and "a3_incident_commander" in trigger.handled_by


async def test_causes_link_the_whole_story_from_trigger_to_mission(system, runtime, clock):
    await loop(system, runtime, clock)
    events = trace(runtime, limit=1000)
    mission = next(e for e in events if e.type == "mission_status")
    ancestors = chain(runtime, mission.id)["ancestors"]
    kinds = [e.type for e in ancestors]
    # sensor reading -> validated -> trigger -> task -> plan -> proposal -> gateway -> mission
    assert kinds[0] == "observation" or "observation_validated" in kinds
    for expected in (
        "trigger",
        "task_assigned",
        "candidate_plan",
        "plan_proposed",
        "action_authorised",
    ):
        assert expected in kinds, expected
    assert kinds.index("trigger") < kinds.index("plan_proposed") < kinds.index("action_authorised")


async def test_descendants_of_a_trigger_include_the_specialists_and_the_proposal(
    system, runtime, clock
):
    await loop(system, runtime, clock)
    trigger = next(e for e in trace(runtime, limit=1000) if e.type == "trigger")
    led_to = {(e.raw_source, e.type) for e in chain(runtime, trigger.id)["descendants"]}
    assert ("a4_evacuation", "candidate_plan") in led_to
    assert ("a3_incident_commander", "plan_proposed") in led_to
    assert ("a8_dispatch", "mission_status") in led_to


async def test_flow_shows_every_hop_including_executors_and_review(system, runtime, clock):
    await loop(system, runtime, clock)
    pairs = {(e.source, e.target, e.kind) for e in flow(runtime)}
    for expected in [
        ("sensors", "a1_sentinel", "delivered"),
        ("a1_sentinel", "a3_incident_commander", "delivered"),
        ("a3_incident_commander", "a4_evacuation", "delivered"),
        ("a4_evacuation", "a3_incident_commander", "delivered"),
        ("a3_incident_commander", "action_gateway", "delivered"),
        ("action_gateway", "a8_dispatch", "execute"),
        ("action_gateway", "a9_comms", "execute"),
    ]:
        assert expected in pairs, expected


async def test_only_a3_delegates_on_the_map(system, runtime, clock):
    await loop(system, runtime, clock)
    task_senders = {e.source for e in flow(runtime) if e.label == "task_assigned"}
    assert task_senders == {"a3_incident_commander"}


async def test_paging_and_incident_filter(system, runtime, clock):
    await loop(system, runtime, clock)
    first = trace(runtime, limit=5)
    rest = trace(runtime, after_seq=first[-1].seq, limit=1000)
    assert [e.seq for e in first] == sorted(e.seq for e in first)
    assert rest and rest[0].seq > first[-1].seq
    only = trace(runtime, incident_id="incident:ratnapura-demo-river", limit=1000)
    assert only and all(e.incident_id == "incident:ratnapura-demo-river" for e in only)
    assert len(flow(runtime, incident_id="incident:ratnapura-demo-river")) > 5


async def test_get_event_and_unknown_id(system, runtime, clock):
    await loop(system, runtime, clock)
    first = trace(runtime, limit=1)[0]
    assert get_event(runtime, first.id).id == first.id
    assert get_event(runtime, "nope") is None


async def test_catalog_describes_every_registered_agent_with_live_activity(system, runtime, clock):
    await loop(system, runtime, clock)
    live = {c.name: c for c in cards(runtime)}
    assert set(runtime.bus.handlers) <= set(live)
    assert {"a8_dispatch", "a9_comms", "action_gateway", "human", "sensors"} <= set(live)
    assert all(CATALOG[n].purpose for n in CATALOG)
    assert live["a4_evacuation"].received > 0 and live["a4_evacuation"].produced > 0
    assert live["a8_dispatch"].received > 0  # executed by the Gateway, not a bus subscriber
    assert "trigger" in live["a3_incident_commander"].subscribed
    assert not any(c.uses_llm for c in live.values())  # nothing on the critical path uses a model


def test_source_names_and_summaries():
    assert node_of("sensor:rtp-node-01/wl-01") == "sensors"
    assert node_of("a4_evacuation") == "a4_evacuation" and node_of("api") == "human"
    line = summarise(
        {
            "type": "task_assigned",
            "source": "a3_incident_commander",
            "payload": {"assignee": "a4_evacuation", "task": "evacuation"},
        }
    )
    assert "A3" in line and "a4_evacuation" in line
    assert "unknown" not in summarise({"type": "made_up", "source": "x", "payload": {}})
