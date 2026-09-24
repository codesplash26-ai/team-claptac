"""The whole loop on the real platform: sensor -> A1 -> A2 -> A3 -> A4..A7 -> Gateway -> A8/A9."""

import pytest

from atlas.agents.a3_incident_commander import CommanderConfig, CycleState
from atlas.core.config import SentinelConfig, Settings
from atlas.core.events import Event, EventType
from atlas.ingest.simulator import ReplayClock, replay
from atlas.runtime import Runtime
from atlas.system import attach_all
from atlas.tools.demo import validated_observation
from tests.conftest import events
from tests.control.conftest import fast_policy
from tests.orchestration.conftest import reading, scenario

RISING = scenario(reading(0, 10), reading(120, 26), reading(130, 26))
INCIDENT = "incident:ratnapura-demo-river"


async def settle(system, rounds=3):
    for _ in range(rounds):  # executors publish events that can start more work
        await system.control.gateway.run_pending()


async def run_loop(system, runtime, clock, story=RISING):
    await runtime.start()
    await replay(runtime, story, clock)
    await settle(system)


def sequence(runtime):
    return [(r["payload"]["source"], r["payload"]["type"]) for r in runtime.ledger.records("event")]


async def test_rising_water_drives_the_full_loop(system, runtime, clock):
    await run_loop(system, runtime, clock)
    (incident,) = system.orchestration.commander.incidents()
    assert incident["state"] == CycleState.ACTIVE.value and not incident["needs_human"]
    assert {
        "a1_sentinel",
        "a2_hazard_intel",
        "a3_incident_commander",
        "a4_evacuation",
        "a5_triage",
        "a6_logistics",
        "a7_shelter",
        "action_gateway",
        "a8_dispatch",
        "a9_comms",
    } <= {source for source, _ in sequence(runtime)}
    missions = system.control.dispatch.list()
    assert len(missions) == 5  # 3 evacuation groups, 1 rescue standby, 1 resupply run
    assert all(m.resources_committed <= 2 for m in missions)  # routine: no human needed
    (alert,) = system.control.comms.list_alerts()
    assert alert["status"] == "active" and set(alert["texts"]) == {"si", "ta", "en"}
    assert system.control.comms.list_sitreps()
    assert runtime.ledger.verify()


async def test_specialists_answer_in_the_order_a3_asked(system, runtime, clock):
    await run_loop(system, runtime, clock)
    task_ids = [
        r["payload"]["payload"]["task_id"]
        for r in runtime.ledger.records("event")
        if r["payload"]["type"] == "task_assigned"
    ]
    assert [t.split(":", 2)[2] for t in task_ids] == [
        "c1:triage",
        "c1:routes",
        "c1:shelter",
        "c1:logistics",
        "c1:logistics:resupply",
    ]
    assert {s for s, t in sequence(runtime) if t == "task_assigned"} == {"a3_incident_commander"}


async def test_every_event_is_recorded_once_by_the_bus(system, runtime, clock):
    await run_loop(system, runtime, clock)
    ids = [r["payload"]["id"] for r in runtime.ledger.records("event")]
    assert len(ids) == len(set(ids))


async def test_a3_plan_is_one_gateway_submission(system, runtime, clock):
    await run_loop(system, runtime, clock)
    (proposal,) = events(runtime, "plan_proposed")
    actions = proposal["payload"]["actions"]
    assert {a["action_type"] for a in actions} == {
        "create_mission",
        "publish_alert",
        "publish_sitrep",
    }
    assert all(
        a["proposed_by"] == "a3_incident_commander" and a["snapshot_version"] is not None
        for a in actions
    )
    assert all(a["rationale"] for a in actions)
    assert len({a["idempotency_key"] for a in actions}) == len(actions)


async def test_road_change_triggers_a_bounded_replan_and_escalates(system, runtime, clock):
    await run_loop(system, runtime, clock)
    before = len(system.control.dispatch.list())
    await runtime.bus.publish(validated_observation(clock, 30, offset=200, evidence="ev-30"))
    await settle(system)
    (incident,) = system.orchestration.commander.incidents()
    assert incident["cycle"] == 2 and incident["replans"] == 1
    assert incident["needs_human"]  # nowhere safe left: a human decides
    assert any("safe route" in n for n in incident["notes"])
    assert len(system.control.dispatch.list()) == before  # nothing cancelled or duplicated
    assert len(system.control.comms.list_alerts()) == 1  # no softer repeat alert


async def test_replan_cap_stops_a_flapping_sensor(runtime, clock):
    system = attach_all(
        runtime, policy=fast_policy(), commander_config=CommanderConfig(max_replans=1)
    )
    await run_loop(system, runtime, clock)
    for i, level in enumerate((30, 24, 30, 24)):
        await runtime.bus.publish(
            validated_observation(clock, level, offset=300 + 100 * i, evidence=f"ev-{i}")
        )
        await settle(system)
    (incident,) = system.orchestration.commander.incidents()
    assert incident["replans"] == 1 and incident["needs_human"]
    assert any("replan limit" in n for n in incident["notes"])


async def test_two_incidents_run_side_by_side(system, runtime, clock):
    await run_loop(system, runtime, clock)
    trigger = Event(
        type=EventType.TRIGGER,
        source="a1_sentinel",
        incident_id="incident:gn12",
        payload={
            "area_id": "ratnapura-gn-12",
            "reason": "test",
            "score": 1.4,
            "confidence": 0.9,
            "evidence_ids": [],
            "snapshot_version": 0,
            "observed_at": clock.now.isoformat(),
            "detection_latency_seconds": 1,
        },
    )
    await runtime.bus.publish(trigger)
    await settle(system)
    incidents = {i["incident_id"]: i for i in system.orchestration.commander.incidents()}
    assert set(incidents) == {INCIDENT, "incident:gn12"}
    assert incidents["incident:gn12"]["state"] in ("active", "awaiting_gateway")
    assert incidents[INCIDENT]["state"] == "active"


async def test_incident_survives_a_restart(tmp_path):
    path = str(tmp_path / "atlas.db")
    clock = ReplayClock()

    def build():
        rt = Runtime(
            Settings(database_path=path, sentinel=SentinelConfig(reasoning_enabled=True)),
            clock=clock,
        )
        return rt, attach_all(rt, policy=fast_policy())

    first, system = build()
    await run_loop(system, first, clock)
    before = system.orchestration.commander.incidents()
    first.db.close()

    second, system2 = build()
    await second.start()  # recovery redelivers pending events; nothing may be planned twice
    await settle(system2)
    after = system2.orchestration.commander.incidents()
    assert [i["state"] for i in after] == [i["state"] for i in before] == ["active"]
    assert after[0]["cycle"] == before[0]["cycle"] == 1
    assert len(system2.control.dispatch.list()) == 5
    second.db.close()


async def test_loop_needs_no_llm(system, runtime, clock, monkeypatch):
    for key in ("LLM_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    await run_loop(system, runtime, clock)
    assert system.control.dispatch.list()


@pytest.mark.parametrize(
    "reason,status", [("stale_snapshot", "blocked"), ("execution_failed", "compensated")]
)
async def test_a_stale_or_failed_action_makes_a3_replan(system, runtime, clock, reason, status):
    await run_loop(system, runtime, clock)
    outcome = Event(
        type=EventType.ACTION_BLOCKED,
        source="action_gateway",
        incident_id=INCIDENT,
        payload={
            "decision": {"idempotency_key": "k", "status": status, "reason": reason},
            "action": {"action_type": "publish_sitrep", "params": {}},
        },
    )
    await runtime.bus.publish(outcome)
    await settle(system)
    (incident,) = system.orchestration.commander.incidents()
    assert incident["cycle"] == 2 and any(reason in n for n in incident["notes"])
