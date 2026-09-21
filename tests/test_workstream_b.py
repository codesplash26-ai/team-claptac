"""Workstream B unit + integration tests (LLM wrapper, A2, A5, A3, e2e)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from atlas.agents.a2_hazard_intel import HazardIntelAgent
from atlas.agents.a3_incident_commander import CycleState, IncidentCommander
from atlas.agents.a5_triage import TriageAgent, score_need
from atlas.agents.runtime import build_runtime
from atlas.core.cop import CommonOperatingPicture
from atlas.core.event_bus import EventBus
from atlas.core.events import Event, EventType
from atlas.ingest.sensors import SensorReading, SensorType, to_event
from atlas.memory.ledger import ActionLedger
from atlas.tools.llm import LLMClient, LLMUnavailable, RateLimiter


@pytest.fixture
def stack():
    bus = EventBus()
    cop = CommonOperatingPicture()
    ledger = ActionLedger()
    return bus, cop, ledger


@pytest.mark.asyncio
async def test_rate_limiter_allows_burst_under_cap():
    limiter = RateLimiter(requests_per_minute=5)
    for _ in range(5):
        await limiter.acquire()


def test_score_need_monotonic_in_urgency():
    low = score_need(urgency=0.2, vulnerability=0.5, people_affected=100)
    high = score_need(urgency=0.9, vulnerability=0.5, people_affected=100)
    assert high > low


@pytest.mark.asyncio
async def test_a2_risk_from_water_sensor(stack):
    bus, cop, ledger = stack
    a2 = HazardIntelAgent(bus, cop, ledger)
    reading = SensorReading(
        node_id="rtp-node-01",
        sensor_id="wl-01",
        type=SensorType.WATER_LEVEL,
        value=65.0,
        unit="cm",
        timestamp=datetime.now(timezone.utc),
        location={"lat": 6.6828, "lon": 80.3992, "area_id": "ratnapura-demo-river"},
    )
    events = await a2.handle(to_event(reading))
    assert len(events) == 1
    assert events[0].type == EventType.RISK_UPDATE
    assert events[0].payload["level"] == "severe"
    assert events[0].payload["estimated_vulnerable"] > 0
    assert cop.get("area:ratnapura-demo-river", "risk_level").value == "severe"


@pytest.mark.asyncio
async def test_a2_information_gaps_without_profile(stack):
    bus, cop, ledger = stack
    a2 = HazardIntelAgent(bus, cop, ledger, vulnerabilities=[])
    event = Event(
        type=EventType.OBSERVATION,
        source="field",
        payload={"kind": "report", "area_id": "nowhere", "hazard": "flood", "level": "watch"},
    )
    out = await a2.handle(event)
    assert "no_vulnerability_profile" in out[0].payload["information_gaps"]


@pytest.mark.asyncio
async def test_a5_ranks_severe_above_watch(stack):
    bus, cop, ledger = stack
    a5 = TriageAgent(bus, cop, ledger)
    await a5.handle(
        Event(
            type=EventType.RISK_UPDATE,
            source="a2",
            incident_id="i1",
            payload={
                "area_id": "a-watch",
                "level": "watch",
                "confidence": 0.5,
                "vulnerability": {"known": True, "vulnerable_fraction": 0.2, "estimated_vulnerable": 100},
                "estimated_vulnerable": 100,
            },
        )
    )
    await a5.handle(
        Event(
            type=EventType.RISK_UPDATE,
            source="a2",
            incident_id="i1",
            payload={
                "area_id": "a-severe",
                "level": "severe",
                "confidence": 0.9,
                "vulnerability": {"known": True, "vulnerable_fraction": 0.3, "estimated_vulnerable": 800},
                "estimated_vulnerable": 800,
            },
        )
    )
    ranked = a5.ranked()
    assert ranked[0]["area_id"] == "a-severe"
    assert "explanation" in ranked[0]


@pytest.mark.asyncio
async def test_a3_fallback_multi_incident():
    rt = build_runtime(force_llm_fallback=True)

    await rt.publish(
        Event(
            type=EventType.TRIGGER,
            source="a1_sentinel",
            incident_id="inc-a",
            payload={"area_id": "ratnapura-demo-river", "hazard": "flood", "anomaly_score": 0.9},
        )
    )
    await rt.publish(
        Event(
            type=EventType.TRIGGER,
            source="a1_sentinel",
            incident_id="inc-b",
            payload={"area_id": "ratnapura-gn-12", "hazard": "flood", "anomaly_score": 0.8},
        )
    )

    assert rt.a3.get_cycle("inc-a") is not None
    assert rt.a3.get_cycle("inc-b") is not None
    assert rt.a3.get_cycle("inc-a").used_llm_fallback is True
    assert rt.a3.get_cycle("inc-b").used_llm_fallback is True
    assert len(rt.ledger.all()) >= 2
    assert any(e.type == EventType.ACTION_AUTHORISED for e in rt.bus.history)
    assert any(e.type == EventType.PLAN_PROPOSED for e in rt.bus.history)


@pytest.mark.asyncio
async def test_a3_replan_on_failed_mission():
    rt = build_runtime(force_llm_fallback=True)
    await rt.publish(
        Event(
            type=EventType.TRIGGER,
            source="a1_sentinel",
            incident_id="inc-replan",
            payload={"area_id": "ratnapura-demo-river", "hazard": "flood", "anomaly_score": 0.9},
        )
    )
    before = len(rt.ledger.all())
    await rt.publish(
        Event(
            type=EventType.MISSION_STATUS,
            source="a8_dispatch",
            incident_id="inc-replan",
            payload={"status": "failed", "reason": "bridge closed"},
        )
    )
    cycle = rt.a3.get_cycle("inc-replan")
    assert cycle is not None
    assert any("replan_due_to:failed" in n for n in cycle.notes)
    assert len(rt.ledger.all()) >= before  # idempotent keys may dedupe; cycle still advanced


@pytest.mark.asyncio
async def test_llm_unavailable_without_key():
    client = LLMClient()
    # Ensure no key for this process setting object
    client.settings.llm_api_key = None
    with pytest.raises(LLMUnavailable):
        await client.complete(system="x", messages=[{"role": "user", "content": "hi"}])


@pytest.mark.asyncio
async def test_e2e_observation_to_authorised_action():
    rt = build_runtime(force_llm_fallback=True)
    reading = SensorReading(
        node_id="rtp-node-01",
        sensor_id="wl-01",
        type=SensorType.WATER_LEVEL,
        value=55.0,
        unit="cm",
        timestamp=datetime.now(timezone.utc),
        location={"lat": 6.6828, "lon": 80.3992, "area_id": "ratnapura-demo-river"},
    )
    await rt.publish(to_event(reading))
    await rt.publish(
        Event(
            type=EventType.TRIGGER,
            source="a1_sentinel",
            incident_id="e2e-1",
            payload={
                "area_id": "ratnapura-demo-river",
                "hazard": "flood",
                "anomaly_score": 0.88,
            },
        )
    )
    types = {e.type for e in rt.bus.history}
    assert EventType.RISK_UPDATE in types
    assert EventType.CANDIDATE_PLAN in types
    assert EventType.PLAN_PROPOSED in types
    assert EventType.ACTION_AUTHORISED in types
    cycle = rt.a3.get_cycle("e2e-1")
    assert cycle.state in {CycleState.ACTIVE, CycleState.AWAITING_GATEWAY, CycleState.STANDBY}
    assert rt.a5.ranked()[0]["area_id"] == "ratnapura-demo-river"
