from atlas.agents.a2_hazard_intel import HazardIntel, level_for
from atlas.agents.a5_triage import Triage, score_need
from atlas.core.config import SentinelConfig
from atlas.core.events import Event, EventType
from atlas.system import attach_all
from atlas.tools.demo import validated_observation
from tests.conftest import events
from tests.control.conftest import fast_policy

AREA = "ratnapura-demo-river"


def rebuild(runtime):
    """Only A2 and A5 on the bus, so their events are easy to read."""
    a2 = HazardIntel(
        runtime.bus, runtime.cop, runtime.ledger, config=SentinelConfig(), clock=runtime.clock
    )
    a5 = Triage(runtime.bus, runtime.cop, runtime.ledger, clock=runtime.clock)
    runtime.register(a2)
    runtime.register(a5)
    return a2, a5


def risk_events(runtime):
    return [
        e["payload"]
        for e in events(runtime, "risk_update")
        if e["payload"].get("kind") == "hazard_risk"
    ]


def test_levels_are_ratios_of_the_sentinel_threshold():
    assert [level_for(r) for r in (0.5, 0.79, 0.8, 0.99, 1.0, 1.49, 1.5, 3)] == [
        "normal",
        "normal",
        "watch",
        "watch",
        "warning",
        "warning",
        "severe",
        "severe",
    ]


async def test_measurement_becomes_a_risk_update_with_vulnerability(runtime, clock):
    rebuild(runtime)
    await runtime.bus.publish(validated_observation(clock, 26))
    (risk,) = risk_events(runtime)
    assert (risk["hazard"], risk["level"], risk["area_id"]) == ("flood", "warning", AREA)
    assert risk["vulnerability"]["known"] and risk["estimated_vulnerable"] == 1176
    assert risk["metric"]["threshold"] == 25 and risk["evidence_ids"] == ["ev-1"]


async def test_only_level_changes_are_announced(runtime, clock):
    rebuild(runtime)
    for i, cm in enumerate((26, 27, 28, 40)):  # warning, warning, warning, severe
        await runtime.bus.publish(validated_observation(clock, cm, offset=i, evidence=f"e{i}"))
    assert [r["level"] for r in risk_events(runtime)] == ["warning", "severe"]


async def test_cop_is_written_only_when_the_belief_changes(runtime, clock):
    rebuild(runtime)
    await runtime.bus.publish(validated_observation(clock, 26))
    version = runtime.cop.version
    await runtime.bus.publish(validated_observation(clock, 27, offset=1, evidence="e2"))
    assert runtime.cop.version == version  # same level: no write, no stale plans
    assert runtime.cop.get(f"area:{AREA}", "risk_level").value == "warning"


async def test_raw_observations_are_ignored(runtime, clock):
    a2, _ = rebuild(runtime)
    assert EventType.OBSERVATION not in a2.subscribes_to
    assert EventType.OBSERVATION_VALIDATED in a2.subscribes_to


async def test_unknown_area_is_flagged_not_invented(runtime, clock):
    rebuild(runtime)
    await runtime.bus.publish(validated_observation(clock, 30, area="atlantis"))
    (risk,) = risk_events(runtime)
    assert not risk["vulnerability"]["known"] and risk["estimated_vulnerable"] == 0
    assert "no_vulnerability_profile" in risk["information_gaps"]


async def test_trigger_raises_an_area_that_is_below_warning(runtime, clock):
    rebuild(runtime)
    await runtime.bus.publish(validated_observation(clock, 10))
    await runtime.bus.publish(
        Event(
            type=EventType.TRIGGER,
            source="a1_sentinel",
            incident_id="i",
            payload={"area_id": AREA, "confidence": 0.8, "evidence_ids": ["t"]},
        )
    )
    assert [r["level"] for r in risk_events(runtime)] == ["normal", "warning"]


async def test_conflicting_reports_mark_the_risk_uncertain(runtime, clock):
    rebuild(runtime)
    await runtime.bus.publish(validated_observation(clock, 26))
    await runtime.bus.publish(
        Event(
            type=EventType.CONFLICT_FLAG,
            source="a1_sentinel",
            payload={"area_id": AREA, "attribute": "water_level", "evidence_ids": ["c"]},
        )
    )
    risk = risk_events(runtime)[-1]
    assert risk["uncertain"] and "conflicting_reports" in risk["information_gaps"]
    assert risk["level"] == "warning"  # the level is kept; only our confidence drops


async def test_malformed_observation_is_dropped_not_fatal(runtime):
    rebuild(runtime)
    await runtime.bus.publish(
        Event(type=EventType.OBSERVATION_VALIDATED, source="x", payload={"nonsense": 1})
    )
    assert risk_events(runtime) == []


# ----------------------------------------------------------------------- A5


def test_score_is_transparent_and_monotonic():
    base = score_need(urgency=0.7, vulnerability=0.3)
    assert base == round(0.7 * 0.3 * 1.075, 4)  # 0.15 * 0.5 default confidence boost
    assert score_need(urgency=0.95, vulnerability=0.3) > base
    assert score_need(urgency=0.7, vulnerability=0.5) > base
    assert score_need(urgency=0.7, vulnerability=0.3, people_affected=4000) > base
    assert score_need(urgency=5, vulnerability=5, people_affected=10**6) <= 1.0


async def test_ranking_orders_areas_and_explains_itself(runtime, clock):
    _, a5 = rebuild(runtime)
    await runtime.bus.publish(validated_observation(clock, 40, area=AREA))
    await runtime.bus.publish(
        validated_observation(clock, 30, area="ratnapura-gn-12", offset=1, evidence="e2")
    )
    ranked = a5.needs()
    assert [n["area_id"] for n in ranked] == [AREA, "ratnapura-gn-12"]
    assert "urgency" in ranked[0]["explanation"] and "vulnerable" in ranked[0]["explanation"]


async def test_rerank_event_only_when_the_top_priority_moves(runtime, clock):
    rebuild(runtime)
    await runtime.bus.publish(validated_observation(clock, 30, area=AREA))
    await runtime.bus.publish(validated_observation(clock, 40, area=AREA, offset=1, evidence="e2"))
    await runtime.bus.publish(
        validated_observation(clock, 60, area="ratnapura-gn-12", offset=2, evidence="e3")
    )
    reranks = [
        e
        for e in events(runtime, "candidate_plan")
        if e["payload"]["task_id"].startswith("rerank:")
    ]
    assert len(reranks) == 1 or len(reranks) == 2  # first area, then possibly the new leader
    assert reranks[0]["payload"]["plan"]["ranked_needs"][0]["area_id"] == AREA


async def test_road_access_updates_do_not_touch_the_queue(runtime, clock):
    _, a5 = rebuild(runtime)
    await runtime.bus.publish(
        Event(
            type=EventType.RISK_UPDATE,
            source="a4_evacuation",
            payload={"kind": "road_access_changed", "area_id": AREA},
        )
    )
    assert a5.needs() == []


async def test_triage_task_gets_a_plan_and_bad_tasks_get_a_failed_plan(runtime, clock):
    _, a5 = rebuild(runtime)
    await runtime.bus.publish(validated_observation(clock, 30))

    def task(payload):
        return Event(
            type=EventType.TASK_ASSIGNED,
            source="a3_incident_commander",
            payload={"assignee": "a5_triage", **payload},
        )

    await runtime.bus.publish(task({"task_id": "t1"}))
    await runtime.bus.publish(task({"task_id": "t2", "area_ids": 7}))
    await runtime.bus.publish(
        Event(
            type=EventType.TASK_ASSIGNED,
            source="a3",
            payload={"assignee": "a4_evacuation", "task_id": "t3"},
        )
    )
    plans = {
        e["payload"]["task_id"]: e["payload"]
        for e in events(runtime, "candidate_plan")
        if not e["payload"]["task_id"].startswith("rerank")
    }
    assert plans["t1"]["status"] == "ok" and plans["t1"]["plan"]["ranked_needs"][0]["rank"] == 1
    assert plans["t2"]["status"] == "invalid_task"
    assert "t3" not in plans  # addressed to someone else


async def test_triage_queue_persists(runtime, clock):
    _, a5 = rebuild(runtime)
    await runtime.bus.publish(validated_observation(clock, 30))
    fresh = Triage(runtime.bus, runtime.cop, runtime.ledger, clock=clock)
    assert [n["area_id"] for n in fresh.needs()] == [AREA]


def test_field_need_seam_ranks_alongside_sensor_risk(runtime):
    a5 = Triage(runtime.bus, runtime.cop, runtime.ledger, clock=runtime.clock)
    a5.record_need("field-1", urgency=0.95, vulnerability=0.9, people=300)
    assert a5.top_priority() == "field-1"


def test_system_registers_every_specialist(runtime):
    system = attach_all(runtime, policy=fast_policy())
    assert {
        "a2_hazard_intel",
        "a3_incident_commander",
        "a4_evacuation",
        "a5_triage",
        "a6_logistics",
        "a7_shelter",
        "a1_sentinel",
        "action_gateway",
    } <= set(runtime.bus.handlers) | {"action_gateway"}
    assert system.orchestration.commander.name == "a3_incident_commander"
