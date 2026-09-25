"""Timeline follows real causal inputs and measured executions without synthesising activity."""

import json

from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.core.config import Settings
from atlas.core.events import Event, EventType
from atlas.dashboard.dev.timeline import event_detail, planning_detail, timeline
from atlas.reasoning.contracts import Action, PlanningResult
from atlas.reasoning.tracing import record_result
from tests.dev.test_trace import loop


async def test_pages_partition_ledger_without_duplicate_or_missing_events(system, runtime, clock):
    await loop(system, runtime, clock)
    all_events = timeline(runtime, limit=500)["events"]
    pages, cursor = [], None
    while True:
        page = timeline(runtime, limit=7, before_seq=cursor)
        pages = page["events"] + pages
        if not page["has_older"]:
            break
        cursor = page["oldest_seq"]
    assert [e["id"] for e in pages] == [e["id"] for e in all_events]
    assert [e["seq"] for e in pages] == sorted({e["seq"] for e in pages})


async def test_incident_keeps_its_sensor_ancestors_but_not_unrelated_inputs(system, runtime, clock):
    await loop(system, runtime, clock)
    incident = "incident:ratnapura-demo-river"
    unrelated = Event(
        type=EventType.INFORMATION_GAP, source="test", payload={"reason": "unrelated"}
    )
    await runtime.bus.publish(unrelated)
    page = timeline(runtime, incident_id=incident, limit=500)
    assert any(e["type"] == "observation" and e["incident_id"] is None for e in page["events"])
    assert any(e["category"] == "outcome" for e in page["events"])
    assert unrelated.id not in {e["id"] for e in page["events"]}
    assert timeline(runtime, incident_id="missing")["events"] == []


async def test_measured_activity_includes_handlers_and_executors(system, runtime, clock):
    await loop(system, runtime, clock)
    page = timeline(runtime, limit=500)
    activity = [a for e in page["events"] for a in e["activity"]]
    assert {f"a{i}_" for i in range(1, 10)} <= {a["agent"][:3] for a in activity}
    assert {"handler", "executor"} <= {a["kind"] for a in activity}
    assert all(a["duration_ms"] >= 0 and a["started_at"] > 0 for a in activity)
    item = page["events"][0]
    assert event_detail(runtime, item["id"]) == item
    assert event_detail(runtime, "missing") is None


async def test_advice_remains_labelled_template_with_actual_validation_trace(
    system, runtime, clock
):
    await loop(system, runtime, clock)
    system.intelligence.provider = None
    await system.intelligence.tick()
    advice = [e for e in timeline(runtime, limit=500)["events"] if e["category"] == "reasoning"]
    advisor = next(e for e in advice if e["source"] == "intelligence_advisor")
    assert advisor["payload"]["source"] == "template"
    assert advisor["payload"]["trace"]["steps"]
    assert advisor["payload"]["trace"]["model"] is None
    assert advisor["activity"] == []  # no fabricated model execution


def test_timeline_permissions_validation_and_empty_state(monkeypatch):
    monkeypatch.setenv("ATLAS_AUTH", "strict")
    monkeypatch.setenv(
        "ATLAS_TOKENS", json.dumps([{"token": "viewer-key", "name": "Viewer", "role": "viewer"}])
    )
    with TestClient(create_app(Settings(database_path=":memory:"))) as client:
        assert client.get("/dev/timeline").status_code == 401
        assert client.get("/dev/timeline/missing").status_code == 401
        assert client.get("/dev/timeline/missing/planning").status_code == 401
        headers = {"X-Operator-Token": "viewer-key"}
        response = client.get("/dev/timeline", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"events": [], "has_older": False, "oldest_seq": None}
        assert client.get("/dev/timeline?limit=501", headers=headers).status_code == 422
        assert client.get("/dev/timeline?before_seq=0", headers=headers).status_code == 422
        assert client.get("/dev/timeline/missing", headers=headers).status_code == 404
        assert client.get("/dev/timeline/missing/planning", headers=headers).status_code == 404
        assert client.post("/dev/timeline", headers=headers).status_code == 405


async def test_planner_trace_resolves_only_the_recorded_source(runtime):
    result = PlanningResult(
        agent="a1_sentinel",
        action=Action(id="monitor"),
        snapshot_version=2,
        evidence_ids=[],
        assumptions=["test assumption"],
        simulations=5,
        stop_reason="simulation_budget",
    )
    event = record_result(runtime.ledger, result)
    await runtime.bus.publish(event)
    assert planning_detail(runtime, event.id) == result.model_dump(mode="json")
    assert planning_detail(runtime, "missing") is None
    wrong_source = Event(
        type=EventType.REASONING_COMPLETED,
        source="a2_hazard_intel",
        payload={"reasoning_id": result.id},
    )
    await runtime.bus.publish(wrong_source)
    assert planning_detail(runtime, wrong_source.id) is None
