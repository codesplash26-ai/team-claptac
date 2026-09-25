"""Playground: red team, what-if, live replay, inject. Sandboxes never touch the live ledger."""

import time

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.core.config import Settings
from atlas.dashboard.dev import playground as pg


@pytest.fixture
def client():
    with TestClient(create_app(Settings(database_path=":memory:"))) as c:
        yield c


def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("timed out")


# --------------------------------------------------------------------- red team


def test_every_attack_gets_exactly_the_outcome_policy_promises():
    result = pg.run_redteam()
    failed = [r for r in result["rows"] if not r["passed"]]
    assert not failed, failed
    assert result["catch_rate"] == 1.0 and result["blocked"] == result["should_block"] >= 10
    outcomes = {r["expected"] for r in result["rows"]}
    assert outcomes == {"blocked", "escalated", "authorised"}  # the guard is not just "no to all"
    assert not any(r["executed"] for r in result["rows"])


def test_red_team_runs_in_a_sandbox_and_leaves_no_trace_in_the_live_system(client):
    before = client.get("/ledger").json()
    assert client.post("/play/redteam").json()["catch_rate"] == 1.0
    assert client.get("/ledger").json() == before
    assert client.get("/incidents/redteam").status_code == 404


# ---------------------------------------------------------------------- what-if


def test_a_human_who_says_no_changes_the_outcome():
    scenario = pg.load_scenario("flood")
    out = pg.whatif(
        scenario, pg.Variant(label="strict, human rejects", strict_policy=True, human="reject")
    )
    assert out["baseline"]["missions"] == 5 and out["changed"]["missions"] == 0
    assert out["diff"]["missions"] == {"baseline": 5, "variant": 0}
    assert out["changed"]["actions_by_status"].get("rejected") == 5
    assert out["changed"]["needs_human"] and not out["baseline"]["needs_human"]


def test_an_ignored_escalation_leaves_actions_waiting():
    out = pg.whatif(
        pg.load_scenario("flood"),
        pg.Variant(label="strict, nobody answers", strict_policy=True, human="ignore"),
    )
    assert out["changed"]["pending_approvals"] == 5 and out["changed"]["missions"] == 0


def test_losing_a_sensor_is_visible_and_an_identical_variant_changes_nothing():
    scenario = pg.load_scenario("ratnapura_ditwah_rain_only")
    lost = pg.whatif(scenario, pg.Variant(label="no soil", drop_types=["soil_moisture"]))
    assert lost["changed"]["triggers"] < lost["baseline"]["triggers"]
    same = pg.whatif(pg.load_scenario("flood"), pg.Variant(label="nothing"))
    assert same["diff"] == {}


def test_perturbation_is_deterministic_and_never_edits_the_original():
    scenario = pg.load_scenario("ratnapura_ditwah_2025")
    before = len(scenario["events"])
    variant = pg.Variant(drop_fraction=0.3)
    a, b = pg._perturb(scenario["events"], variant), pg._perturb(scenario["events"], variant)
    assert a == b and 0.5 * before < len(a) < before and len(scenario["events"]) == before


def test_whatif_over_http_and_unknown_scenario(client):
    r = client.post(
        "/play/whatif",
        json={"scenario": "flood", "variant": {"label": "no gauge", "drop_types": ["water_level"]}},
    )
    assert r.status_code == 200 and "diff" in r.json()
    assert client.post("/play/whatif", json={"scenario": "nope", "variant": {}}).status_code == 404


# ------------------------------------------------------------------ live replay


def test_the_catalogue_lists_real_and_synthetic_scenarios_but_not_planner_files(client):
    keys = {s["key"]: s for s in client.get("/play/scenarios").json()}
    assert {"flood", "ratnapura_ditwah_2025", "ratnapura_ditwah_rain_only"} <= set(keys)
    assert "planning" not in keys
    assert keys["ratnapura_ditwah_2025"]["real"] and keys["ratnapura_ditwah_2025"]["assumptions"]
    assert not keys["flood"]["real"]


def test_a_scenario_replays_into_the_live_system(client):
    started = client.post("/play/scenarios/start", json={"scenario": "flood", "speed": 60}).json()
    assert started["state"] == "running" and started["total"] == 5
    wait_for(lambda: client.get("/play/scenarios/status").json()["state"] == "finished")
    assert client.get("/play/scenarios/status").json()["sent"] == 5
    types = {e["type"] for e in client.get("/dev/trace?limit=1000").json()}
    assert {"observation_validated", "trigger", "plan_proposed"} <= types
    wait_for(lambda: client.get("/dev/runs").json())
    (run,) = client.get("/dev/runs").json()
    assert run["spans"] > 10 and run["errors"] == 0


def test_replay_rejects_bad_input_and_can_be_stopped(client):
    assert client.post("/play/scenarios/start", json={"scenario": "nope"}).status_code == 404
    assert (
        client.post("/play/scenarios/start", json={"scenario": "flood", "speed": 999}).status_code
        == 422
    )
    assert (
        client.post("/play/scenarios/start", json={"scenario": "../../etc/passwd"}).status_code
        == 404
    )
    started = client.post(
        "/play/scenarios/start", json={"scenario": "ratnapura_ditwah_2025", "speed": 0.1}
    ).json()
    assert started["state"] == "running"
    again = client.post("/play/scenarios/start", json={"scenario": "flood", "speed": 5})
    assert again.status_code == 409  # one replay at a time
    assert client.post("/play/scenarios/stop").json()["state"] == "stopped"


def test_inject_a_reading_goes_through_the_normal_pipeline(client):
    r = client.post(
        "/play/inject/now",
        params={
            "node_id": "n1",
            "sensor_id": "wl",
            "type": "water_level",
            "value": 12,
            "unit": "cm",
            "area_id": "ratnapura-demo-river",
        },
    )
    assert r.status_code == 202
    assert any(e["type"] == "observation_validated" for e in client.get("/dev/trace").json())


def test_actions_need_the_operator_token_when_one_is_set(client, monkeypatch):
    monkeypatch.setenv("ATLAS_OPERATOR_TOKEN", "s3cret")
    assert client.post("/play/scenarios/start", json={"scenario": "flood"}).status_code == 401
    assert client.post("/play/redteam").status_code == 401
    assert client.post("/play/whatif", json={"scenario": "flood", "variant": {}}).status_code == 401
    assert client.get("/play/scenarios").status_code == 200  # reading the catalogue stays open
    assert client.post("/play/redteam", headers={"X-Operator-Token": "s3cret"}).status_code == 200


def test_runs_spans_and_metrics_over_http(client):
    client.post("/play/scenarios/start", json={"scenario": "flood", "speed": 60})
    wait_for(lambda: client.get("/play/scenarios/status").json()["state"] == "finished")
    wait_for(lambda: client.get("/dev/runs").json())
    (run,) = client.get("/dev/runs").json()
    spans = client.get(f"/dev/runs/{run['incident_id']}/spans").json()
    assert spans and spans[0]["depth"] == 0
    detail = client.get("/dev/span", params={"id": spans[3]["id"]}).json()
    assert detail["input"]["payload"] is not None and "metadata" in detail
    assert client.get("/dev/span", params={"id": "nope:x:handler"}).status_code == 404
    assert client.get("/dev/runs/nope/spans").status_code == 404
    m = client.get("/dev/metrics").json()
    assert m["agents"] and "control" in m and m["llm"]["calls"] == 0
