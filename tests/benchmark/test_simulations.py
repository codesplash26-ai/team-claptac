"""Exercise the real system, persistence, interruption and the read-only trace API."""

import asyncio
import json
import sqlite3
import threading
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from atlas.api.main import create_app
from atlas.benchmark.runner import run_case, run_suite
from atlas.benchmark.schedule import materialize
from atlas.benchmark.schema import ROOT, Task, load_tasks
from atlas.core.config import Settings


@pytest.fixture(scope="module")
def saved_suite(tmp_path_factory):
    folder = tmp_path_factory.mktemp("benchmarks") / "test-run"
    state = asyncio.run(run_suite(load_tasks(), [42], folder))
    return folder, state


def test_ten_real_scenarios_pass_and_exercise_all_agents(saved_suite):
    folder, state = saved_suite
    assert state["state"] == "completed"
    assert state["total"] == state["completed"] == state["passed"] == 10, [
        (r["task_id"], r["error"], [c for c in r["checks"] if not c["passed"]])
        for r in state["results"]
        if not r["passed"]
    ]
    agents = {a[:3] for r in state["results"] for a in r["metrics"]["agent_activity"]}
    assert {f"a{i}_" for i in range(1, 10)} <= agents
    for result in state["results"]:
        case = folder / result["case_id"]
        inputs = [json.loads(s) for s in (case / "inputs.jsonl").read_text().splitlines()]
        events = [json.loads(s) for s in (case / "events.jsonl").read_text().splitlines()]
        ledger = [json.loads(s) for s in (case / "ledger.jsonl").read_text().splitlines()]
        spans = [json.loads(s) for s in (case / "spans.jsonl").read_text().splitlines()]
        assert len(inputs) == len(json.loads((case / "schedule.json").read_text())["steps"])
        assert len(events) == sum(result["metrics"]["events"].values())
        assert len({e["id"] for e in events}) == len(events)
        assert len(ledger) == len({r["seq"] for r in ledger})
        assert result["metrics"]["inputs"]["random"] == 5
        assert spans and all(s["ok"] for s in spans)
        assert all(
            e["payload"]["source"] == "template"
            for e in events
            if e["source"] == "intelligence_advisor"
        )
        with sqlite3.connect(case / "state.sqlite") as conn:
            assert conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == len(ledger)
            assert conn.execute("SELECT COUNT(*) FROM event_spans").fetchone()[0] == len(spans)
        assert (case / "report.html").read_text().startswith("<!doctype html>")
    with zipfile.ZipFile(folder / "artifacts.zip") as archive:
        assert archive.testzip() is None
        assert "report.html" in archive.namelist()
        assert len([n for n in archive.namelist() if n.endswith("state.sqlite")]) == 10
        assert json.loads(archive.read("summary.json"))["passed"] == 10


def test_seed_reproduction_and_template_validation():
    task = load_tasks()[1]
    assert materialize(task, 42) == materialize(task, 42)
    assert materialize(task, 42) != materialize(task, 43)
    assert materialize(task, 42, False) == task.steps
    Task.model_validate_json((ROOT / "data/benchmarks/task-template.json").read_text())
    schema = json.loads((ROOT / "data/benchmarks/task.schema.json").read_text())
    assert schema == Task.model_json_schema()


@pytest.mark.parametrize(
    "problem", ["unknown_metric", "bad_reference", "duplicate_id", "out_of_order", "typo"]
)
def test_bad_tasks_fail_before_execution(problem):
    task = load_tasks()[1].model_dump(mode="json")
    if problem == "unknown_metric":
        task["expectations"][0]["metric"] = "missions.completted"
    elif problem == "bad_reference":
        task["steps"].append(
            {"kind": "duplicate", "id": "bad", "target": "missing", "at_seconds": 700}
        )
    elif problem == "duplicate_id":
        task["steps"][1]["id"] = task["steps"][0]["id"]
    elif problem == "out_of_order":
        task["steps"].reverse()
    else:
        task["random_eventz"] = {}
    with pytest.raises(ValidationError):
        Task.model_validate(task)


async def test_cancel_keeps_partial_trace_and_does_not_claim_a_pass(tmp_path):
    stop = threading.Event()
    result = await run_case(
        load_tasks()[1], 42, tmp_path / "cancelled", stop=stop, progress=lambda *args: stop.set()
    )
    assert result["status"] == "cancelled" and not result["passed"]
    assert result["metrics"]["inputs"]["total"] == 1
    assert (tmp_path / "cancelled/state.sqlite").exists()
    assert (tmp_path / "cancelled/report.html").exists()


async def test_execution_failure_keeps_diagnostics(tmp_path, monkeypatch):
    async def broken(*args):
        raise RuntimeError("injected benchmark failure")

    monkeypatch.setattr("atlas.benchmark.runner.apply_step", broken)
    folder = tmp_path / "error"
    result = await run_case(load_tasks()[0], 42, folder)
    assert result["status"] == "error" and not result["passed"]
    assert "injected benchmark failure" in result["error"]
    assert "injected benchmark failure" in (folder / "inputs.jsonl").read_text()
    assert (folder / "report.html").exists()


def wait_finished(client, run_id, headers):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        state = client.get(f"/benchmark/runs/{run_id}", headers=headers).json()
        if state["state"] not in {"starting", "running"}:
            return state
        time.sleep(0.03)
    pytest.fail("Benchmark did not finish")


def test_api_auth_saved_trace_pagination_and_isolation(saved_suite, monkeypatch):
    folder, state = saved_suite
    monkeypatch.setenv("ATLAS_AUTH", "strict")
    monkeypatch.setenv(
        "ATLAS_TOKENS",
        json.dumps(
            [
                {"token": "view", "name": "Viewer", "role": "viewer"},
                {"token": "dev", "name": "Developer", "role": "developer"},
            ]
        ),
    )
    viewer, developer = {"X-Operator-Token": "view"}, {"X-Operator-Token": "dev"}
    app = create_app(Settings(database_path=":memory:"))
    with TestClient(app) as client:
        app.state.benchmark.root = folder.parent
        assert client.get("/benchmark/tasks").status_code == 401
        assert client.get("/benchmark/tasks", headers=viewer).status_code == 200
        assert client.post("/benchmark/runs", json={}, headers=viewer).status_code == 403
        assert client.get("/benchmark/template", headers=viewer).status_code == 200
        assert client.get("/benchmark/schema", headers=viewer).json() == Task.model_json_schema()
        before = client.get("/dev/timeline", headers=viewer).json()
        assert (
            client.post(
                "/benchmark/runs", json={"task_ids": ["missing"]}, headers=developer
            ).status_code
            == 422
        )
        assert (
            client.post("/benchmark/runs", json={"seeds": [42, 42]}, headers=developer).status_code
            == 422
        )
        # Start a deliberately paced case, then cancel. A second active run is refused.
        response = client.post(
            "/benchmark/runs",
            json={"task_ids": ["02-river-evacuation"], "step_delay_ms": 1000},
            headers=developer,
        )
        assert response.status_code == 202
        run_id = response.json()["id"]
        assert client.post("/benchmark/runs", json={}, headers=developer).status_code == 409
        assert client.post(f"/benchmark/runs/{run_id}/cancel", headers=viewer).status_code == 403
        assert client.post(f"/benchmark/runs/{run_id}/cancel", headers=developer).status_code == 200
        assert wait_finished(client, run_id, developer)["state"] == "cancelled"
        assert client.get("/dev/timeline", headers=viewer).json() == before
        case = state["results"][6]
        base = f"/benchmark/runs/{folder.name}/cases/{case['case_id']}"
        events, cursor = [], None
        while True:
            url = base + "/timeline?limit=7" + (f"&before_seq={cursor}" if cursor else "")
            page = client.get(url, headers=viewer).json()
            events = page["events"] + events
            if not page["has_older"]:
                break
            cursor = page["oldest_seq"]
        assert len(events) == sum(case["metrics"]["events"].values())
        event = next(e for e in events if e["activity"])
        detail = client.get(base + f"/timeline/{event['id']}", headers=viewer).json()
        assert detail == event
        activity = event["activity"][0]
        assert (
            client.get(
                base + f"/span?id={event['id']}:{activity['agent']}:{activity['kind']}",
                headers=viewer,
            ).status_code
            == 200
        )
        assert client.get(base + f"/chain/{event['id']}", headers=viewer).status_code == 200
        planning = next(
            e
            for e in events
            if e["payload"].get("reasoning_id") and e["type"] == "reasoning_completed"
        )
        assert (
            client.get(base + f"/timeline/{planning['id']}/planning", headers=viewer).json()[
                "simulations"
            ]
            > 0
        )
        evidence = json.loads((folder / case["case_id"] / "evidence.json").read_text())
        ref = next(iter(evidence))
        assert client.get(base + f"/evidence/{ref}", headers=viewer).json() == evidence[ref]
        assert client.get(base + "/timeline?limit=501", headers=viewer).status_code == 422
        assert client.get(base + "/timeline/missing", headers=viewer).status_code == 404
        assert client.get(f"/benchmark/files/{folder.name}/artifacts.zip").status_code == 401
        assert (
            client.get(f"/benchmark/files/{folder.name}/artifacts.zip", headers=viewer).status_code
            == 200
        )
        for artifact in [".env", "../summary.json", "bad/path/result.json"]:
            assert (
                client.get(f"/benchmark/files/{folder.name}/{artifact}", headers=viewer).status_code
                == 404
            )


async def test_template_runs_and_pacing_preserves_simulated_outcomes(tmp_path):
    task = Task.model_validate_json((ROOT / "data/benchmarks/task-template.json").read_text())
    fast = await run_case(task, 42, tmp_path / "fast")
    paced = await run_case(task, 42, tmp_path / "paced", step_delay_ms=1)
    assert fast["passed"] and paced["passed"]
    assert fast["schedule_sha256"] == paced["schedule_sha256"]
    assert fast["simulated_seconds"] == paced["simulated_seconds"]
    for group in ["events", "missions", "approvals", "integrity"]:
        assert fast["metrics"][group] == paced["metrics"][group]


async def test_startup_failure_is_visible_and_shutdown_still_works(tmp_path, monkeypatch):
    from atlas.benchmark.api import BenchmarkService, Start

    async def broken(*args, **kwargs):
        raise OSError("Cannot create output directory")

    monkeypatch.setattr("atlas.benchmark.api.run_suite", broken)
    service = BenchmarkService(tmp_path)
    run = service.start(Start(task_ids=["01-quiet-watch"]))
    await service.close()
    status = service.status(run["id"])
    assert status["state"] == "error"
    assert "Cannot create output directory" in status["error"]
