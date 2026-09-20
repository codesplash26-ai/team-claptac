import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.core.config import Settings
from atlas.core.events import Event, EventType
from atlas.ingest.simulator import replay
from tests.conftest import events


async def test_replay_reaches_commander_boundary_and_persists_feedback(runtime, clock):
    async def commander_stub(trigger):
        # Integration contract only: the real A3/Gateway remain other workstreams.
        return [
            Event(
                type=EventType.CANDIDATE_PLAN,
                source="a3-test",
                incident_id=trigger.incident_id,
                payload={"trigger_id": trigger.id},
            )
        ]

    runtime.bus.subscribe("a3-test", [EventType.TRIGGER], commander_stub)
    await replay(runtime, json.loads(Path("data/scenarios/flood.json").read_text()), clock)
    assert len(events(runtime, "trigger")) == 1
    assert len(events(runtime, "candidate_plan")) == 1
    assert len(events(runtime, "conflict_flag")) == 1
    assert runtime.ledger.verify()
    assert events(runtime, "observation_rejected")[-1]["payload"]["reason"] == "stale_or_future"


def test_http_drives_a1_and_duplicate_is_idempotent():
    payload = {
        "node_id": "one",
        "sensor_id": "water",
        "type": "water_level",
        "value": 26,
        "unit": "cm",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "measurement_id": "fixed",
        "location": {"lat": 6.6, "lon": 80.3, "area_id": "river"},
    }
    with TestClient(create_app(Settings(database_path=":memory:"))) as client:
        first = client.post("/ingest/sensor", json=payload)
        second = client.post("/ingest/sensor", json=payload)
        assert first.status_code == second.status_code == 202
        assert second.json()["duplicate"]
        assert len(events(client.app.state.runtime, "trigger")) == 1
        changed = client.post("/ingest/sensor", json={**payload, "value": 99})
        assert changed.status_code == 409


def test_invalid_units_nan_and_naive_time_rejected():
    payload = {
        "node_id": "one",
        "sensor_id": "water",
        "type": "water_level",
        "value": 26,
        "unit": "cm",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with TestClient(create_app(Settings(database_path=":memory:"))) as client:
        for change in ({"unit": "F"}, {"value": "NaN"}, {"timestamp": "2026-09-20T00:00:00"}):
            assert client.post("/ingest/sensor", json={**payload, **change}).status_code == 422
