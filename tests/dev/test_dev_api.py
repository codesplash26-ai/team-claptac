import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.core.config import Settings

LOC = {"lat": 6.6828, "lon": 80.3992, "area_id": "ratnapura-demo-river"}


@pytest.fixture
def client():
    with TestClient(create_app(Settings(database_path=":memory:"))) as c:
        yield c


def post(client, value, ago, mid):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=ago)).isoformat()
    return client.post(
        "/ingest/sensor",
        json={
            "node_id": "rtp-01",
            "sensor_id": "wl",
            "type": "water_level",
            "unit": "cm",
            "value": value,
            "timestamp": stamp,
            "measurement_id": mid,
            "location": LOC,
        },
    )


def wait_for(fn, timeout=8):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("timed out")


def test_empty_system_serves_the_page_and_empty_data(client):
    page = client.get("/dev")
    assert page.status_code == 200 and "ATLAS developer view" in page.text
    assert client.get("/dev/trace").json() == [] and client.get("/dev/flow").json() == []
    assert client.get("/dev/incidents").json() == []
    names = {a["name"] for a in client.get("/dev/agents").json()}
    assert {"a1_sentinel", "a3_incident_commander", "action_gateway", "human"} <= names


def test_a_live_flood_shows_up_with_causes(client):
    post(client, 10, 200, "m1")
    post(client, 26, 20, "m2")
    post(client, 26, 10, "m3")
    events = wait_for(
        lambda: [
            e for e in client.get("/dev/trace?limit=1000").json() if e["type"] == "plan_proposed"
        ]
        and client.get("/dev/trace?limit=1000").json()
    )
    proposal = next(e for e in events if e["type"] == "plan_proposed")
    chain = client.get(f"/dev/chain/{proposal['id']}").json()
    assert chain["event"]["id"] == proposal["id"]
    assert "trigger" in [e["type"] for e in chain["ancestors"]]
    assert client.get("/dev/incidents").json()[0]["incident_id"].startswith("incident:")
    edges = {(e["source"], e["target"]) for e in client.get("/dev/flow").json()}
    assert ("a3_incident_commander", "action_gateway") in edges


def test_unknown_event_is_a_404_and_the_view_is_read_only(client):
    assert client.get("/dev/chain/nope").status_code == 404
    assert client.post("/dev/trace").status_code == 405
    assert client.post("/dev").status_code == 405


def test_map_and_history_endpoints(client):
    m = client.get("/dev/map").json()
    assert m["site"] == "ratnapura" and len(m["edges"]) > 300 and m["shelters"]
    assert m["bounds"][0][0] < m["bounds"][1][0] and m["observed_edges"] == 0
    hist = client.get("/dev/history").json()
    assert hist["sources"] and len(hist["events"]) > 100
    only = client.get("/dev/history", params={"hazard": "tsunami"}).json()["events"]
    assert only and {e["hazard"] for e in only} == {"tsunami"}
