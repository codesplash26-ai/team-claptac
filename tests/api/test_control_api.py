import time
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.core.config import Settings
from atlas.core.events import Event, EventType, ProposedAction

READING = {
    "node_id": "rtp-01",
    "sensor_id": "wl",
    "type": "water_level",
    "unit": "cm",
    "location": {"lat": 6.6828, "lon": 80.3992, "area_id": "ratnapura-river"},
}


@pytest.fixture
def client():
    with TestClient(create_app(Settings(database_path=":memory:"))) as c:
        yield c


def propose(client, *actions, incident="inc-1"):
    plan = Event(
        type=EventType.PLAN_PROPOSED,
        source="a3_incident_commander",
        incident_id=incident,
        payload={"actions": [a.model_dump(mode="json") for a in actions]},
    )
    client.portal.call(client.app.state.runtime.bus.publish, plan)


def action(key, executor, action_type, params=None):
    return ProposedAction(
        idempotency_key=key,
        incident_id="inc-1",
        proposed_by="a3_incident_commander",
        executor=executor,
        action_type=action_type,
        params=params or {},
    )


def wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def status_of(client, key):
    entries = client.get("/incidents/inc-1").json()["actions"]
    return next(e["decision"]["status"] for e in entries if e["action"]["idempotency_key"] == key)


def test_health_and_existing_ingest_still_work(client):
    assert client.get("/health").json() == {"status": "ok"}
    now = datetime.now(timezone.utc).isoformat()
    assert (
        client.post("/ingest/sensor", json={**READING, "value": 12, "timestamp": now}).status_code
        == 202
    )


def test_escalated_action_is_listed_and_approval_executes_it(client):
    propose(client, action("c1", "a8_dispatch", "close_major_route", {"road_id": "A4"}))
    (item,) = client.get("/approvals").json()
    assert item["action"]["action_type"] == "close_major_route" and item["status"] == "pending"
    assert client.get("/incidents").json()[0]["pending_approvals"] == 1

    r = client.post(f"/approvals/{item['id']}/approve", json={"reviewer": "cmdr", "note": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "authorised"
    wait_for(lambda: status_of(client, "c1") == "executed")
    assert client.get("/approvals").json() == []
    assert client.get("/approvals", params={"status": "approved"}).json()[0]["reviewer"] == "cmdr"


def test_reject_and_double_resolution_and_unknown_ids(client):
    propose(client, action("c2", "a8_dispatch", "cancel_mission", {"mission_id": "m"}))
    (item,) = client.get("/approvals").json()
    assert (
        client.post(f"/approvals/{item['id']}/reject", json={"reviewer": "cmdr"}).json()["status"]
        == "rejected"
    )
    assert (
        client.post(f"/approvals/{item['id']}/approve", json={"reviewer": "cmdr"}).status_code
        == 409
    )
    assert client.post("/approvals/ap-nope/approve", json={"reviewer": "cmdr"}).status_code == 404
    assert client.post(f"/approvals/{item['id']}/approve", json={"reviewer": ""}).status_code == 422


def test_edit_runs_the_edited_action(client):
    propose(
        client,
        action("c3", "a8_dispatch", "create_mission", {"title": "Big", "resources_committed": 6}),
    )
    (item,) = client.get("/approvals").json()
    r = client.post(
        f"/approvals/{item['id']}/edit",
        json={"reviewer": "cmdr", "params": {"title": "Small", "resources_committed": 1}},
    )
    assert r.json()["reason"] == "human_edited"
    (mission,) = wait_for(lambda: client.get("/missions").json())
    assert mission["title"] == "Small" and mission["resources_committed"] == 1


def test_blocked_action_never_reaches_a_human_or_an_executor(client):
    propose(client, action("x1", "a9_comms", "create_mission", {"title": "t"}))
    assert client.get("/approvals").json() == [] and client.get("/missions").json() == []
    (entry,) = client.get("/incidents/inc-1").json()["actions"]
    assert entry["decision"]["status"] == "blocked"
    assert entry["decision"]["reason"] == "action_not_permitted_for_executor"


def test_mission_field_updates_over_http(client):
    propose(
        client,
        action(
            "m1", "a8_dispatch", "create_mission", {"title": "Boat run", "resources_committed": 1}
        ),
    )
    (mission,) = wait_for(lambda: client.get("/missions").json())
    mid = mission["id"]
    assert client.post(f"/missions/{mid}/progress", json={"percent": 10}).status_code == 409
    assert (
        client.post(f"/missions/{mid}/acknowledge", json={"by": "lead"}).json()["status"]
        == "acknowledged"
    )
    assert client.post(f"/missions/{mid}/progress", json={"percent": 50}).status_code == 200
    assert client.post(f"/missions/{mid}/progress", json={"percent": 500}).status_code == 422
    assert (
        client.post(f"/missions/{mid}/exception", json={"description": "flat tyre"}).json()[
            "exception"
        ]
        == "flat tyre"
    )
    assert client.post(f"/missions/{mid}/complete").json()["status"] == "completed"
    detail = client.get(f"/missions/{mid}").json()
    assert detail["progress"] == 100 and len(detail["history"]) >= 3
    assert client.get("/missions/m-nope").status_code == 404
    assert client.post("/missions/m-nope/acknowledge", json={}).status_code == 404


def test_alerts_cap_download_and_sitreps(client):
    propose(
        client,
        action(
            "a1",
            "a9_comms",
            "publish_alert",
            {"area": "Ratnapura", "event": "flood", "scope": "local", "instruction": "avoid_area"},
        ),
        action("s1", "a9_comms", "publish_sitrep", {}),
    )
    (alert,) = wait_for(lambda: client.get("/alerts").json())
    assert "cap_xml" not in alert and alert["status"] == "active"
    cap = client.get(f"/alerts/{alert['id']}/cap")
    assert cap.headers["content-type"].startswith("application/cap+xml")
    assert "urn:oasis:names:tc:emergency:cap:1.2" in cap.text
    assert client.get("/alerts/nope/cap").status_code == 404
    assert len(wait_for(lambda: client.get("/sitreps").json())) == 1


def test_ledger_endpoints_and_incident_detail(client):
    propose(client, action("l1", "a8_dispatch", "update_mission", {"mission_id": "x"}))
    assert client.get("/ledger/verify").json() == {"valid": True}
    kinds = {r["kind"] for r in client.get("/ledger").json()}
    assert {"event", "action"} <= kinds
    actions = client.get("/ledger", params={"kind": "action", "limit": 1}).json()
    assert len(actions) == 1 and actions[0]["payload"]["idempotency_key"] == "l1"
    assert client.get("/incidents/nope").status_code == 404
    assert client.get("/incidents/inc-1").json()["actions"][0]["action"]["idempotency_key"] == "l1"


def test_operator_token_protects_mutating_routes(client, monkeypatch):
    monkeypatch.setenv("ATLAS_OPERATOR_TOKEN", "s3cret")
    propose(client, action("t1", "a8_dispatch", "cancel_mission", {"mission_id": "m"}))
    (item,) = client.get("/approvals").json()
    url, body = f"/approvals/{item['id']}/approve", {"reviewer": "cmdr"}
    assert client.post(url, json=body).status_code == 401
    assert client.post(url, json=body, headers={"X-Operator-Token": "wrong"}).status_code == 401
    assert client.get("/approvals").status_code == 200  # reads stay open
    assert client.post(url, json=body, headers={"X-Operator-Token": "s3cret"}).status_code == 200


def test_dashboard_and_sensor_panel(client):
    base = datetime.now(timezone.utc)
    for i, value in enumerate([10, 14, 20, 27]):
        stamp = base.replace(microsecond=i * 1000).isoformat()
        client.post(
            "/ingest/sensor",
            json={**READING, "value": value, "timestamp": stamp, "measurement_id": f"m{i}"},
        )
    (sensor,) = client.get("/sensors").json()
    assert sensor["value"] == 27 and sensor["online"] is True and sensor["threshold"] == 25
    assert sensor["history"] == [10, 14, 20, 27]
    propose(client, action("d1", "a8_dispatch", "close_major_route", {"road_id": "<b>x</b>"}))
    page = client.get("/dashboard")
    assert page.status_code == 200
    for expected in (
        "Live sensors",
        "rtp-01",
        "Pending approvals (1)",
        "Mission board",
        "&lt;b&gt;x&lt;/b&gt;",
        "polyline",
    ):
        assert expected in page.text
    assert "<b>x</b>" not in page.text  # escaped
    assert client.get("/dashboard/map").status_code == 200


def test_empty_dashboard_renders(client):
    page = client.get("/dashboard").text
    assert "No sensor readings received yet" in page and "Nothing awaiting approval" in page
