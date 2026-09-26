"""Bounded background benchmark runs and read-only access to saved simulation traces."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import Field, model_validator

from atlas.benchmark.runner import OUTPUTS, run_suite
from atlas.benchmark.schema import ROOT, Slug, Strict, Task, load_tasks
from atlas.dashboard.dev.runs import span_detail
from atlas.dashboard.dev.timeline import event_detail, planning_detail, timeline
from atlas.dashboard.dev.trace import chain, get_event
from atlas.manager.rbac import SCENARIO_READ, SCENARIO_RUN, TRACE_READ, requires

router = APIRouter(prefix="/benchmark", tags=["benchmark"])
SAFE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,150}$")
FILES = {
    "artifacts.zip",
    "report.html",
    "summary.json",
    "status.json",
    "manifest.json",
    "results.csv",
    "task.json",
    "schedule.json",
    "result.json",
    "configuration.json",
    "inputs.jsonl",
    "events.jsonl",
    "spans.jsonl",
    "ledger.jsonl",
    "final-state.json",
    "timeline.json",
    "reasoning.json",
    "evidence.json",
    "state.sqlite",
}


class Start(Strict):
    task_ids: list[Slug] = Field(default_factory=list, max_length=30)
    seeds: list[int] = Field(default_factory=lambda: [42], min_length=1, max_length=10)
    random_enabled: bool = True
    step_delay_ms: int = Field(default=150, ge=0, le=1000)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.seeds)) != len(self.seeds) or any(
            not 0 <= s <= 2147483647 for s in self.seeds
        ):
            raise ValueError("Use unique seeds between 0 and 2147483647")
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("Task ids must be unique")
        return self


class BenchmarkService:
    def __init__(self, root=OUTPUTS):
        self.root = Path(root)
        self.worker = None
        self.stop = threading.Event()
        self.active_id = None
        self.failure = None

    def directory(self, run_id, case_id=None):
        if not SAFE.fullmatch(run_id) or (case_id is not None and not SAFE.fullmatch(case_id)):
            raise HTTPException(404, "Unknown benchmark run or case")
        path = self.root / run_id
        if case_id:
            path /= case_id
        if not path.is_dir() or not path.resolve().is_relative_to(self.root.resolve()):
            raise HTTPException(404, "Unknown benchmark run or case")
        return path

    def status(self, run_id):
        if run_id == self.active_id and self.failure:
            return self.failure
        path = self.directory(run_id) / "status.json"
        if not path.exists():
            return {"id": run_id, "state": "starting", "results": []}
        state = json.loads(path.read_text())
        if state["state"] == "running" and run_id != self.active_id:
            state["state"] = "interrupted"
        return state

    def start(self, body):
        if self.worker is not None and not self.worker.done():
            raise HTTPException(409, "A benchmark is already running; wait or cancel it first")
        tasks = load_tasks()
        if body.task_ids:
            if set(body.task_ids) - {t.id for t in tasks}:
                raise HTTPException(422, "Unknown benchmark task id")
            tasks = [t for t in tasks if t.id in body.task_ids]
        if len(tasks) * len(body.seeds) > 100:
            raise HTTPException(422, "Limit each run to 100 cases")
        self.active_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
        )
        self.stop = threading.Event()
        self.failure = None
        run_id, stop = self.active_id, self.stop

        async def execute():
            try:
                await asyncio.to_thread(
                    lambda: asyncio.run(
                        run_suite(
                            tasks,
                            body.seeds,
                            self.root / run_id,
                            random_enabled=body.random_enabled,
                            step_delay_ms=body.step_delay_ms,
                            stop=stop,
                        )
                    )
                )
            except Exception as exc:
                # Include startup/storage failures that happen before a status file exists.
                self.failure = {
                    "id": run_id,
                    "state": "error",
                    "results": [],
                    "error": f"{type(exc).__name__}: {exc}",
                }

        self.worker = asyncio.create_task(execute())
        return {"id": self.active_id, "state": "starting"}

    async def close(self):
        self.stop.set()
        if self.worker:
            await self.worker


def service(request):
    return request.app.state.benchmark


@contextmanager
def checkpoint(request, run_id, case_id):
    path = service(request).directory(run_id, case_id) / "state.sqlite"
    if not path.exists():
        raise HTTPException(409, "The first simulation checkpoint is still being written")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield SimpleNamespace(db=SimpleNamespace(connection=conn))
    finally:
        conn.close()


@router.get("/tasks", dependencies=requires(SCENARIO_READ))
async def tasks():
    return [t.model_dump(mode="json") for t in load_tasks()]


@router.get("/template", dependencies=requires(SCENARIO_READ))
async def template():
    return json.loads((ROOT / "data/benchmarks/task-template.json").read_text())


@router.get("/schema", dependencies=requires(SCENARIO_READ))
async def schema():
    return Task.model_json_schema()


@router.post("/runs", dependencies=requires(SCENARIO_RUN), status_code=202)
async def start(body: Start, request: Request):
    return service(request).start(body)


@router.get("/runs", dependencies=requires(TRACE_READ))
async def runs(request: Request):
    svc = service(request)
    rows = []
    for path in sorted(
        svc.root.glob("*/status.json"), key=lambda p: p.stat().st_mtime, reverse=True
    )[:100]:
        state = svc.status(path.parent.name)
        rows.append(
            {
                **{k: v for k, v in state.items() if k != "results"},
                "passed": sum(r["passed"] for r in state.get("results", [])),
            }
        )
    return rows


@router.get("/runs/{run_id}", dependencies=requires(TRACE_READ))
async def status(run_id: str, request: Request):
    if run_id == service(request).active_id and service(request).failure:
        return service(request).failure
    if run_id == service(request).active_id and not (service(request).root / run_id).exists():
        return {"id": run_id, "state": "starting", "results": []}
    return service(request).status(run_id)


@router.post("/runs/{run_id}/cancel", dependencies=requires(SCENARIO_RUN))
async def cancel(run_id: str, request: Request):
    svc = service(request)
    if run_id != svc.active_id or not svc.worker or svc.worker.done():
        raise HTTPException(409, "This benchmark is not running")
    svc.stop.set()
    return {"id": run_id, "state": "cancelling"}


@router.get("/files/{run_id}/{artifact:path}", dependencies=requires(TRACE_READ))
async def artifact(run_id: str, artifact: str, request: Request):
    parts = artifact.split("/")
    if len(parts) not in {1, 2} or parts[-1] not in FILES:
        raise HTTPException(404, "Unknown artifact")
    folder = service(request).directory(run_id, parts[0] if len(parts) == 2 else None)
    path = folder / parts[-1]
    if not path.is_file() or not path.resolve().is_relative_to(service(request).root.resolve()):
        raise HTTPException(404, "Artifact not available yet")
    return FileResponse(path)


CASE = "/runs/{run_id}/cases/{case_id}"


@router.get(CASE + "/timeline", dependencies=requires(TRACE_READ))
async def case_timeline(
    run_id: str,
    case_id: str,
    request: Request,
    limit: int = Query(200, ge=1, le=500),
    before_seq: int | None = Query(None, ge=1),
    incident_id: str | None = None,
):
    with checkpoint(request, run_id, case_id) as runtime:
        return timeline(runtime, limit=limit, before_seq=before_seq, incident_id=incident_id)


@router.get(CASE + "/timeline/{event_id}", dependencies=requires(TRACE_READ))
async def case_event(run_id: str, case_id: str, event_id: str, request: Request):
    with checkpoint(request, run_id, case_id) as runtime:
        item = event_detail(runtime, event_id)
        if item is None:
            raise HTTPException(404, "Unknown event")
        return item


@router.get(CASE + "/timeline/{event_id}/planning", dependencies=requires(TRACE_READ))
async def case_planning(run_id: str, case_id: str, event_id: str, request: Request):
    with checkpoint(request, run_id, case_id) as runtime:
        item = planning_detail(runtime, event_id)
        if item is None:
            raise HTTPException(404, "No planner trace recorded")
        return item


@router.get(CASE + "/chain/{event_id}", dependencies=requires(TRACE_READ))
async def case_chain(run_id: str, case_id: str, event_id: str, request: Request):
    with checkpoint(request, run_id, case_id) as runtime:
        if get_event(runtime, event_id) is None:
            raise HTTPException(404, "Unknown event")
        return {
            k: [e.model_dump(mode="json") for e in v] for k, v in chain(runtime, event_id).items()
        }


@router.get(CASE + "/span", dependencies=requires(TRACE_READ))
async def case_span(run_id: str, case_id: str, id: str, request: Request):
    with checkpoint(request, run_id, case_id) as runtime:
        item = span_detail(runtime, id)
        if item is None:
            raise HTTPException(404, "Unknown span")
        return item


@router.get(CASE + "/incidents", dependencies=requires(TRACE_READ))
async def case_incidents(run_id: str, case_id: str, request: Request):
    with checkpoint(request, run_id, case_id) as runtime:
        return [
            dict(r)
            for r in runtime.db.connection.execute(
                "SELECT json_extract(body,'$.incident_id') AS incident_id,COUNT(*) AS events FROM ledger WHERE kind='event' AND json_extract(body,'$.incident_id') IS NOT NULL GROUP BY incident_id"
            )
        ]


@router.get(CASE + "/evidence/{ref}", dependencies=requires(TRACE_READ))
async def case_evidence(run_id: str, case_id: str, ref: str, request: Request):
    with checkpoint(request, run_id, case_id) as runtime:
        row = runtime.db.connection.execute(
            "SELECT body FROM intelligence_evidence WHERE id=?", (ref,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Unknown evidence")
        return json.loads(row[0])
