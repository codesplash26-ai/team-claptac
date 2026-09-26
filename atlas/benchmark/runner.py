from __future__ import annotations

import asyncio
import hashlib
import json
import operator
import platform
import sqlite3
import time
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas.benchmark.schedule import materialize
from atlas.benchmark.schema import DuplicateStep, MissionStep, ReportStep, ReviewStep, SensorStep
from atlas.control.policy_engine import Policy, PolicyEngine
from atlas.core.config import SentinelConfig, Settings
from atlas.ingest.feeds import FeedReport
from atlas.ingest.feeds import to_event as report_event
from atlas.ingest.sensors import SensorReading, to_event
from atlas.ingest.simulator import ReplayClock
from atlas.runtime import Runtime
from atlas.system import attach_all

START = datetime(2026, 9, 20, tzinfo=timezone.utc)
UNITS = {"water_level": "cm", "rainfall": "mm", "soil_moisture": "%", "temperature": "C"}
OUTPUTS = Path(__file__).resolve().parents[2] / "outputs/benchmarks"


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n")
    tmp.replace(path)


def append_jsonl(path, items):
    with Path(path).open("a") as stream:
        for item in items:
            stream.write(json.dumps(item, allow_nan=False, default=str) + "\n")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Recorder:
    def __init__(self, folder, runtime):
        self.folder, self.runtime, self.seq, self.span_row = folder, runtime, 0, 0
        for name in ("ledger.jsonl", "events.jsonl", "spans.jsonl", "inputs.jsonl"):
            (folder / name).touch()

    def flush(self):
        conn = self.runtime.db.connection
        rows = conn.execute("SELECT * FROM ledger WHERE seq>? ORDER BY seq", (self.seq,)).fetchall()
        records = [{**dict(r), "payload": json.loads(r["body"])} for r in rows]
        append_jsonl(self.folder / "ledger.jsonl", records)
        append_jsonl(
            self.folder / "events.jsonl",
            [{"seq": r["seq"], **r["payload"]} for r in records if r["kind"] == "event"],
        )
        if rows:
            self.seq = rows[-1]["seq"]
        spans = conn.execute(
            "SELECT rowid AS sequence,* FROM event_spans WHERE rowid>? ORDER BY rowid",
            (self.span_row,),
        ).fetchall()
        append_jsonl(self.folder / "spans.jsonl", [dict(r) for r in spans])
        if spans:
            self.span_row = spans[-1]["sequence"]
        # A complete point-in-time DB for the existing timeline/evidence inspector, including
        # failures. Replace atomically so readers never see a half-written checkpoint.
        tmp = self.folder / "state.tmp.sqlite"
        out = sqlite3.connect(tmp)
        try:
            conn.backup(out)
        finally:
            out.close()  # `with connect()` only commits; an open file cannot be renamed on Windows
        for attempt in range(20):  # on Windows a reader holding the old checkpoint blocks the swap
            try:
                tmp.replace(self.folder / "state.sqlite")
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)


async def review(system, decision, limit=100):
    changed = 0
    for item in system.control.approvals.pending()[:limit]:
        gateway = system.control.gateway
        fn = gateway.approve if decision == "approve" else gateway.reject
        await gateway.emit(fn(item.id, "simulated-commander", "Scripted benchmark decision"))
        changed += 1
    return changed


async def settle(system, auto_review):
    # Bounded fixed-point drain. Does not start workers or use wall-time sleeps.
    for _ in range(12):
        before = system.runtime.db.connection.execute("SELECT MAX(seq) FROM ledger").fetchone()[0]
        await system.control.tick()
        if auto_review != "ignore":
            await review(system, auto_review)
        after = system.runtime.db.connection.execute("SELECT MAX(seq) FROM ledger").fetchone()[0]
        if before == after:
            await system.intelligence.tick()
            return
    raise RuntimeError("Control loop did not settle within 12 passes")


def reading_event(task, step):
    at = START + timedelta(
        seconds=step.observed_seconds if step.observed_seconds is not None else step.at_seconds
    )
    if isinstance(step, SensorStep):
        return to_event(
            SensorReading(
                node_id=step.node_id,
                sensor_id=step.sensor_type,
                measurement_id=f"{task.id}:{step.id}",
                type=step.sensor_type,
                value=step.value,
                unit=UNITS[step.sensor_type],
                timestamp=at,
                location={"lat": 6.6828, "lon": 80.3992, "area_id": task.area_id},
            )
        )
    return report_event(
        FeedReport(
            report_id=f"{task.id}:{step.id}",
            source=step.source,
            kind=step.report_kind,
            area_id=task.area_id,
            attribute=step.attribute,
            value=step.value,
            unit=UNITS[step.attribute],
            timestamp=at,
            text=step.text,
        )
    )


async def apply_step(task, step, system, previous):
    runtime = system.runtime
    if isinstance(step, (SensorStep, ReportStep)):
        event = reading_event(task, step)
        previous[step.id] = event
        inserted = await runtime.bus.publish(event)
        return {"event_id": event.id, "accepted": inserted, "affected": 1}
    if isinstance(step, DuplicateStep):
        event = previous[step.target]
        inserted = await runtime.bus.publish(event)
        return {
            "event_id": event.id,
            "accepted": inserted,
            "duplicate": not inserted,
            "affected": 0 if not inserted else 1,
        }
    if isinstance(step, ReviewStep):
        return {"affected": await review(system, step.decision, step.limit)}
    if isinstance(step, MissionStep):
        dispatch = system.control.dispatch
        allowed = {
            "acknowledge": {"created"},
            "progress": {"acknowledged", "in_progress"},
            "complete": {"acknowledged", "in_progress"},
            "fail": {"created", "acknowledged", "in_progress"},
        }
        targets = [m for m in dispatch.list() if m.status.value in allowed[step.change]][
            : step.limit
        ]
        for mission in targets:
            if step.change == "acknowledge":
                event = dispatch.acknowledge(mission.id, "simulated-field-team")
            elif step.change == "progress":
                event = dispatch.report_progress(mission.id, step.percent, step.note)
            elif step.change == "complete":
                event = dispatch.complete(mission.id, step.note or "Simulated team completion")
            else:
                event = dispatch.report_exception(
                    mission.id, step.note or "Simulated vehicle failure", fatal=True
                )
            await runtime.bus.publish(event)
        return {
            "affected": len(targets),
            "mission_ids": [m.id for m in targets],
            "skipped_reason": "No eligible missions" if not targets else None,
        }
    return {"affected": 0, "checkpoint": True}


def collect(system, inputs):
    runtime, conn = system.runtime, system.runtime.db.connection
    records = runtime.ledger.records()
    events = [r["payload"] for r in records if r["kind"] == "event"]
    spans = [dict(r) for r in conn.execute("SELECT * FROM event_spans")]
    counts = Counter(e["type"] for e in events)
    missions = system.control.dispatch.list()
    actions = [dict(r) for r in conn.execute("SELECT * FROM gateway_actions")]
    approvals = [dict(r) for r in conn.execute("SELECT * FROM approvals")]
    approved = {a["id"] for a in approvals if a["status"] in {"approved", "edited"}}
    unsafe = [
        a
        for a in actions
        if a["receipt"]
        and (
            a["status"] in {"blocked", "rejected", "escalated"}
            or (a["consequence"] == "high" and a["approval_id"] not in approved)
        )
    ]
    candidates = [e["payload"] for e in events if e["type"] == "candidate_plan"]
    shelter_plans = [c.get("plan", {}) for c in candidates if c.get("agent") == "a7_shelter"]
    logistics_plans = [c.get("plan", {}) for c in candidates if c.get("agent") == "a6_logistics"]
    over_capacity = sum(
        s["occupancy_after"] > s["capacity"] for p in shelter_plans for s in p.get("shelters", [])
    )
    incident_states = system.orchestration.commander.incidents()
    by_agent = Counter(s["subscriber"] for s in spans)
    durations = sorted(s["duration_ms"] for s in spans)
    advice = system.intelligence.list()
    metrics = {
        "events": dict(counts),
        "inputs": {
            "total": len(inputs),
            "duplicates": sum(bool(i.get("result", {}).get("duplicate")) for i in inputs),
            "random": sum(i["step"]["id"].startswith("random-") for i in inputs),
        },
        "missions": {"total": len(missions), **dict(Counter(m.status.value for m in missions))},
        "actions": {
            "total": len(actions),
            "escalated_total": counts["action_escalated"],
            **dict(Counter(a["status"] for a in actions)),
        },
        "approvals": dict(Counter(a["status"] for a in approvals)),
        "incidents": {
            "total": len(incident_states),
            "replans": sum(i["replans"] for i in incident_states),
        },
        "shelters": {
            "unplaced": sum(len(p.get("unplaced", [])) for p in shelter_plans),
            "over_capacity": over_capacity,
        },
        "logistics": {"unserved": sum(len(p.get("unserved", [])) for p in logistics_plans)},
        "roads": {
            "closed": len(
                {
                    b["subject"]
                    for b in runtime.snapshot()["beliefs"]
                    if b["subject"].startswith("edge:")
                    and (
                        (b["attribute"] == "open" and b["value"] is False)
                        or (
                            b["attribute"] == "belief_open"
                            and b["value"] < system.routing.evacuation.config.close_threshold
                        )
                    )
                }
            )
        },
        "hazards": {
            "landslide_updates": sum(
                e["type"] == "risk_update" and e["payload"].get("hazard") == "landslide"
                for e in events
            )
        },
        "reasoning": {
            "planner_runs": sum(r["kind"] == "reasoning" for r in records),
            "advice": len(advice),
            "accepted_claims": sum(c.accepted for a in advice for c in a.claims),
            "rejected_claims": sum(not c.accepted for a in advice for c in a.claims),
        },
        "integrity": {
            "ledger_valid": int(runtime.ledger.verify()),
            "execution_errors": sum(not s["ok"] for s in spans),
            "unsafe_executions": len(unsafe),
        },
        "agent_activity": dict(by_agent),
        "timing": {
            "execution_p95_ms": durations[min(len(durations) - 1, int(len(durations) * 0.95))]
            if durations
            else 0
        },
    }
    return metrics, {
        "incidents": incident_states,
        "missions": [m.model_dump(mode="json") for m in missions],
        "actions": actions,
        "approvals": approvals,
        "snapshot": runtime.snapshot(),
        "advice": [a.model_dump(mode="json") for a in advice],
    }


def score(task, metrics):
    checks = [
        ("integrity.ledger_valid", "eq", 1, "The complete hash-linked ledger must verify."),
        ("integrity.execution_errors", "eq", 0, "Agent executions must complete without errors."),
        (
            "integrity.unsafe_executions",
            "eq",
            0,
            "No blocked, rejected or unapproved high-consequence action may execute.",
        ),
        ("shelters.over_capacity", "eq", 0, "No individual shelter plan may exceed capacity."),
    ]
    checks += [(e.metric, e.operator, e.value, e.reason) for e in task.expectations]
    results = []
    for metric, op, expected, reason in checks:
        actual = metrics
        for key in metric.split("."):
            actual = actual.get(key, 0) if isinstance(actual, dict) else 0
        passed = {"eq": operator.eq, "gte": operator.ge, "lte": operator.le}[op](actual, expected)
        results.append(
            dict(
                metric=metric,
                operator=op,
                expected=expected,
                actual=actual,
                passed=passed,
                reason=reason,
            )
        )
    return results


async def run_case(
    task, seed, folder, *, random_enabled=True, step_delay_ms=0, stop=None, progress=None
):
    folder.mkdir(parents=True, exist_ok=False)
    steps = materialize(task, seed, random_enabled)
    frozen = [s.model_dump(mode="json") for s in steps]
    write_json(folder / "task.json", task.model_dump(mode="json"))
    write_json(
        folder / "schedule.json",
        {"seed": seed, "random_enabled": random_enabled, "sha256": digest(frozen), "steps": frozen},
    )
    clock = ReplayClock(START)
    runtime = Runtime(
        Settings(database_path=":memory:", sentinel=SentinelConfig(reasoning_enabled=True)),
        clock=clock,
    )
    policy = Policy.load().model_dump()
    policy["gateway"]["retry_backoff_seconds"] = 0
    if task.initial.strict_mission_approval:
        policy["actions"]["create_mission"] = {"consequence": "high", "when": {}}
    system = attach_all(runtime, policy=PolicyEngine(Policy.model_validate(policy)))
    system.intelligence.provider = None
    recorder = Recorder(folder, runtime)
    previous, inputs, status, error = {}, [], "completed", None
    began = time.perf_counter()
    try:
        await runtime.start()
        for shelter in system.routing.shelter.shelters.values():
            system.routing.shelter.record_occupancy(
                shelter.id,
                round(shelter.capacity * task.initial.shelter_occupancy_fraction),
                source="benchmark-initial-state",
                at=clock.now,
            )
        # Apply shortages within each resource kind, so a small mixed fleet retains
        # both rescuers and vehicles instead of depending on alphabetical IDs.
        units = system.routing.logistics.units
        for kind in sorted({u.kind for u in units.values()}):
            group = sorted(u.id for u in units.values() if u.kind == kind)
            keep = round(len(group) * task.initial.available_resource_fraction)
            for i, unit in enumerate(group):
                system.routing.logistics.record_availability(
                    unit, i < keep, source="benchmark-initial-state", at=clock.now
                )
        write_json(
            folder / "configuration.json",
            {
                "settings": runtime.settings.model_dump(mode="json"),
                "policy": policy,
                "initial": task.initial.model_dump(),
                "llm_mode": "offline_templates",
            },
        )
        for index, step in enumerate(steps):
            if stop is not None and stop.is_set():
                status = "cancelled"
                break
            clock.now = START + timedelta(seconds=step.at_seconds)
            item = {
                "index": index,
                "simulated_at": clock.now.isoformat(),
                "step": step.model_dump(mode="json"),
            }
            try:
                item["result"] = await apply_step(task, step, system, previous)
                await settle(system, task.initial.auto_review)
            except Exception as exc:
                item["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                inputs.append(item)
                append_jsonl(folder / "inputs.jsonl", [item])
                recorder.flush()
            if progress:
                progress(index + 1, len(steps), step.note or step.id)
            if step_delay_ms:
                # This runner owns a background thread; an interruptible pause changes only display pace.
                if stop is not None:
                    stop.wait(step_delay_ms / 1000)
                else:
                    await asyncio.sleep(step_delay_ms / 1000)
    except Exception as exc:
        status, error = "error", f"{type(exc).__name__}: {exc}"
    finally:
        try:
            recorder.flush()
            metrics, final = collect(system, inputs)
            checks = score(task, metrics)
            result = {
                "task_id": task.id,
                "title": task.title,
                "seed": seed,
                "case_id": folder.name,
                "status": status,
                "error": error,
                "passed": status == "completed" and all(c["passed"] for c in checks),
                "checks": checks,
                "metrics": metrics,
                "duration_seconds": round(time.perf_counter() - began, 3),
                "simulated_seconds": (clock.now - START).total_seconds(),
                "schedule_sha256": digest(frozen),
            }
            write_json(folder / "final-state.json", final)
            write_json(folder / "result.json", result)
            from atlas.benchmark.report import case_report

            case_report(folder, result, inputs, runtime)
        finally:
            await runtime.close()
    return result


async def run_suite(tasks, seeds, folder, *, random_enabled=True, step_delay_ms=0, stop=None):
    from atlas.benchmark.report import suite_report

    folder.mkdir(parents=True, exist_ok=False)
    source_root = Path(__file__).resolve().parents[2]
    files = sorted(
        [
            *source_root.glob("atlas/**/*.py"),
            *source_root.glob("config/*.yaml"),
            *source_root.glob("data/reference/*"),
            *source_root.glob("data/osm/*.json"),
        ]
    )
    source_hashes = {
        str(p.relative_to(source_root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in files
        if p.is_file()
    }
    state = {
        "id": folder.name,
        "state": "running",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "seeds": seeds,
        "random_enabled": random_enabled,
        "step_delay_ms": step_delay_ms,
        "total": len(tasks) * len(seeds),
        "completed": 0,
        "current": None,
        "results": [],
        "limitations": "Synthetic, offline software simulations; no calibrated hazard forecasts, radio hardware, real people, or external LLM calls.",
    }
    write_json(
        folder / "manifest.json",
        {
            "schema_version": 1,
            "python": platform.python_version(),
            "task_ids": [t.id for t in tasks],
            "task_hashes": {t.id: digest(t.model_dump(mode="json")) for t in tasks},
            "source_hashes": source_hashes,
            "seed_policy": "SHA256(task id + seed), local random.Random; timestamps and UUIDs in traces can vary",
        },
    )
    write_json(folder / "status.json", state)
    try:
        for seed in seeds:
            for task in tasks:
                if stop is not None and stop.is_set():
                    state["state"] = "cancelled"
                    break
                case_id = f"{task.id}--seed-{seed}"
                state["current"] = {
                    "case_id": case_id,
                    "title": task.title,
                    "step": 0,
                    "total_steps": 0,
                }

                def progress(done, total, note):
                    state["current"].update(step=done, total_steps=total, note=note)
                    write_json(folder / "status.json", state)

                progress(
                    0, len(materialize(task, seed, random_enabled)), "Starting isolated runtime"
                )
                result = await run_case(
                    task,
                    seed,
                    folder / case_id,
                    random_enabled=random_enabled,
                    step_delay_ms=step_delay_ms,
                    stop=stop,
                    progress=progress,
                )
                state["results"].append(result)
                state["completed"] += 1
                write_json(folder / "status.json", state)
                suite_report(folder, state)
            if state["state"] == "cancelled":
                break
        if state["state"] == "running":
            state["state"] = "cancelled" if stop is not None and stop.is_set() else "completed"
    except Exception as exc:
        state.update(state="error", error=f"{type(exc).__name__}: {exc}")
    finally:
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        state["passed"] = sum(r["passed"] for r in state["results"])
        state["current"] = None
        write_json(folder / "status.json", state)
        write_json(folder / "summary.json", state)
        suite_report(folder, state)
        # Unzip once to browse every linked report and trace without the API.
        archive = folder / "artifacts.tmp.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(folder.rglob("*")):
                if path.is_file() and path.suffix != ".zip" and not path.name.endswith(".tmp"):
                    bundle.write(path, path.relative_to(folder))
        archive.replace(folder / "artifacts.zip")
    return state
