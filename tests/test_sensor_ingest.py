from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.core.events import EventType
from atlas.ingest.sensors import SensorReading, to_event

PAYLOAD = {
    "node_id": "rtp-node-01",
    "sensor_id": "wl-01",
    "type": "water_level",
    "value": 27.5,
    "unit": "cm",
    "timestamp": "2026-09-18T10:00:00+00:00",
    "location": {"lat": 6.6828, "lon": 80.3992, "area_id": "ratnapura-demo-river"},
}


def test_reading_becomes_observation_event():
    event = to_event(SensorReading.model_validate(PAYLOAD))
    assert event.type == EventType.OBSERVATION
    assert event.source == "sensor:rtp-node-01/wl-01"
    assert event.payload["value"] == 27.5


def test_http_ingest_accepts_valid_reading():
    r = TestClient(app).post("/ingest/sensor", json=PAYLOAD)
    assert r.status_code == 202


def test_http_ingest_rejects_unknown_sensor_type():
    r = TestClient(app).post("/ingest/sensor", json={**PAYLOAD, "type": "radiation"})
    assert r.status_code == 422
