"""Running evaluators, storing scores, monitors and experiments.  (Owner: Dinuka)

Given traces, run the evaluators and record what they found.

* ``evaluate``   score the live system's traces (or some of them) now;
* monitors       a stored schedule that does that repeatedly (``MonitorWorker``): online evaluation;
* experiments    replay a scenario in a sandbox as recorded and with a change, score both, compare.

Scores are stored so a trend can be drawn, but a monitor overwrites nothing: each run is its own row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from typing import Any
from uuid import uuid4

from atlas.dashboard.dev.runs import Span
from atlas.manager.evaluation import builtin  # noqa: F401  (registers the built-ins)
from atlas.manager.evaluation.base import (
    REGISTRY,
    Level,
    Mode,
    Score,
    Summary,
    describe,
)
from atlas.manager.evaluation.view import TraceView, build_views

log = logging.getLogger(__name__)
MAX_FAILING_SPANS = 20


class EvalStore:
    def __init__(self, db):
        self.db = db
        db.connection.executescript("""
            CREATE TABLE IF NOT EXISTS eval_runs (id TEXT PRIMARY KEY, at REAL NOT NULL, mode TEXT,
                trigger TEXT, traces INTEGER, summary TEXT);
            CREATE TABLE IF NOT EXISTS eval_scores (run_id TEXT NOT NULL, evaluator TEXT, level TEXT,
                target TEXT, incident_id TEXT, value REAL, passed INTEGER, threshold REAL, unit TEXT,
                detail TEXT, critical INTEGER, at REAL);
            CREATE INDEX IF NOT EXISTS eval_scores_run ON eval_scores(run_id);
            CREATE TABLE IF NOT EXISTS monitors (id TEXT PRIMARY KEY, name TEXT NOT NULL,
                evaluators TEXT NOT NULL, interval_s REAL NOT NULL, enabled INTEGER NOT NULL,
                last_run REAL, last_run_id TEXT);
            CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, at REAL, scenario TEXT,
                variant TEXT, result TEXT);
        """)

    # ---- runs
    def save_run(
        self, run_id, mode, trigger, traces, summary: Summary, scores: list[Score]
    ) -> None:
        with self.db.transaction():
            self.db.connection.execute(
                "INSERT INTO eval_runs VALUES (?,?,?,?,?,?)",
                (run_id, time.time(), mode, trigger, traces, summary.model_dump_json()),
            )
            self.db.connection.executemany(
                "INSERT INTO eval_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        run_id,
                        s.evaluator,
                        s.level.value,
                        s.target,
                        s.incident_id,
                        s.value,
                        None if s.passed is None else int(s.passed),
                        s.threshold,
                        s.unit,
                        s.detail,
                        int(s.critical),
                        s.at,
                    )
                    for s in scores
                ],
            )

    def runs(self, limit: int = 50) -> list[dict]:
        rows = self.db.connection.execute(
            "SELECT * FROM eval_runs ORDER BY at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [{**dict(r), "summary": json.loads(r["summary"])} for r in rows]

    def scores(self, run_id: str, *, failed_only: bool = False) -> list[dict]:
        rows = self.db.connection.execute(
            "SELECT * FROM eval_scores WHERE run_id=?"
            + (" AND passed=0" if failed_only else "")
            + " ORDER BY evaluator, target",
            (run_id,),
        ).fetchall()
        return [
            {
                **dict(r),
                "passed": None if r["passed"] is None else bool(r["passed"]),
                "critical": bool(r["critical"]),
            }
            for r in rows
        ]

    def latest_run(self) -> dict | None:
        found = self.runs(1)
        return found[0] if found else None

    # ---- monitors
    def monitors(self) -> list[dict]:
        return [
            {**dict(r), "evaluators": json.loads(r["evaluators"]), "enabled": bool(r["enabled"])}
            for r in self.db.connection.execute("SELECT * FROM monitors ORDER BY name")
        ]

    def save_monitor(
        self, name: str, evaluators: list[str], interval_s: float, enabled: bool = True
    ) -> dict:
        unknown = [e for e in evaluators if e != "*" and e not in REGISTRY]
        if unknown:
            raise ValueError(f"unknown evaluator(s): {unknown}")
        if interval_s < 5:
            raise ValueError("a monitor must run at most every 5 seconds")
        monitor_id = "mon-" + uuid4().hex[:8]
        self.db.connection.execute(
            "INSERT INTO monitors VALUES (?,?,?,?,?,NULL,NULL)",
            (monitor_id, name, json.dumps(evaluators), interval_s, int(enabled)),
        )
        return next(m for m in self.monitors() if m["id"] == monitor_id)

    def set_monitor(self, monitor_id: str, *, enabled: bool) -> None:
        if not self.db.connection.execute(
            "UPDATE monitors SET enabled=? WHERE id=?", (int(enabled), monitor_id)
        ).rowcount:
            raise LookupError(f"unknown monitor {monitor_id}")

    def delete_monitor(self, monitor_id: str) -> None:
        if not self.db.connection.execute(
            "DELETE FROM monitors WHERE id=?", (monitor_id,)
        ).rowcount:
            raise LookupError(f"unknown monitor {monitor_id}")

    # ---- experiments
    def save_experiment(self, scenario: str, variant: dict, result: dict) -> str:
        experiment_id = "exp-" + uuid4().hex[:8]
        self.db.connection.execute(
            "INSERT INTO experiments VALUES (?,?,?,?,?)",
            (experiment_id, time.time(), scenario, json.dumps(variant), json.dumps(result)),
        )
        return experiment_id

    def experiments(self, limit: int = 30) -> list[dict]:
        return [
            {**dict(r), "variant": json.loads(r["variant"]), "result": json.loads(r["result"])}
            for r in self.db.connection.execute(
                "SELECT * FROM experiments ORDER BY at DESC LIMIT ?", (limit,)
            )
        ]


# ------------------------------------------------------------------------------- scoring


def _select(names: list[str] | None, mode: Mode):
    unknown = [n for n in (names or []) if n != "*" and n not in REGISTRY]
    if unknown:
        raise ValueError(f"unknown evaluator(s): {unknown}")
    chosen = [REGISTRY[n] for n in names] if names and "*" not in names else list(REGISTRY.values())
    return [e for e in chosen if mode in e.modes]


def _score(e, target, result, incident_id=None, at=0.0, detail=None) -> Score:
    return Score(
        evaluator=e.name,
        level=e.level,
        target=target,
        incident_id=incident_id,
        value=result.value,
        passed=result.passed,
        threshold=e.threshold,
        unit=e.unit,
        detail=detail if detail is not None else result.detail,
        critical=e.critical,
        at=at,
    )


def score_views(views: list[TraceView], evaluators, *, at: float | None = None) -> list[Score]:
    at = time.time() if at is None else at
    scores: list[Score] = []
    by_agent: dict[str, list[Span]] = defaultdict(list)
    for v in views:
        for s in v.spans:
            by_agent[s.agent].append(s)
    for e in evaluators:
        try:
            if e.level is Level.TRACE:
                for v in views:
                    scores.append(_score(e, v.trace_id, e.fn(v), v.incident_id, at))
            elif e.level is Level.SPAN:
                for v in views:
                    results = [(s, e.fn(s, v)) for s in v.spans]
                    applicable = [(s, r) for s, r in results if r.passed is not None]
                    if not applicable:
                        continue
                    bad = [(s, r) for s, r in applicable if not r.passed]
                    worst = max(
                        (r.value for _, r in applicable if r.value is not None), default=None
                    )
                    scores.append(
                        Score(
                            evaluator=e.name,
                            level=e.level,
                            target=v.trace_id,
                            incident_id=v.incident_id,
                            value=worst,
                            passed=not bad,
                            threshold=e.threshold,
                            unit=e.unit,
                            at=at,
                            critical=e.critical,
                            detail=f"{len(applicable) - len(bad)}/{len(applicable)} steps ok; worst {worst}",
                        )
                    )
                    for s, r in bad[:MAX_FAILING_SPANS]:
                        scores.append(
                            _score(
                                e,
                                s.id,
                                r,
                                v.incident_id,
                                at,
                                detail=f"{s.agent} {s.event_type}: {r.detail or r.value}",
                            )
                        )
            else:
                for agent, spans in sorted(by_agent.items()):
                    scores.append(_score(e, agent, e.fn(agent, spans), None, at))
        except Exception as exc:  # a broken evaluator must not hide the others' results
            log.exception("evaluator %s failed", e.name)
            scores.append(
                Score(
                    evaluator=e.name,
                    level=e.level,
                    target="*",
                    value=None,
                    passed=None,
                    threshold=e.threshold,
                    unit=e.unit,
                    at=at,
                    detail=f"evaluator error: {type(exc).__name__}: {exc}",
                )
            )
    return scores


def summarise(scores: list[Score]) -> Summary:
    summary = Summary()
    per: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"scored": 0, "passed": 0, "failed": 0, "skipped": 0, "critical": False}
    )
    for s in scores:
        row = per[s.evaluator]
        row["critical"] = s.critical
        row["level"] = s.level.value
        if s.passed is None:
            row["skipped"] += 1
            summary.skipped += 1
        elif s.passed:
            row["scored"] += 1
            row["passed"] += 1
            summary.scored += 1
            summary.passed += 1
        else:
            row["scored"] += 1
            row["failed"] += 1
            summary.scored += 1
            summary.failed += 1
            summary.critical_failures += int(s.critical)
    for row in per.values():
        row["pass_rate"] = round(row["passed"] / row["scored"], 3) if row["scored"] else None
    summary.evaluators = dict(per)
    summary.pass_rate = round(summary.passed / summary.scored, 3) if summary.scored else None
    return summary


def evaluate(
    system,
    store: EvalStore | None = None,
    *,
    evaluators: list[str] | None = None,
    incident_ids: list[str] | None = None,
    mode: Mode = Mode.MONITOR,
    trigger: str = "manual",
) -> dict[str, Any]:
    """Score the system's traces now. Persists the run when a store is given."""
    chosen = _select(evaluators, mode)
    views = build_views(system, incident_ids=incident_ids)
    scores = score_views(views, chosen)
    summary = summarise(scores)
    run_id = "eval-" + uuid4().hex[:10]
    if store is not None:
        store.save_run(run_id, mode.value, trigger, len(views), summary, scores)
    return {"run_id": run_id, "traces": len(views), "summary": summary, "scores": scores}


# ------------------------------------------------------------------------------- monitors


class MonitorWorker:
    """Runs due monitors. ``tick`` is what tests call; ``start`` runs it in the background."""

    def __init__(self, system, store: EvalStore):
        self.system, self.store = system, store
        self.task: asyncio.Task | None = None

    def tick(self, now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        ran = []
        for m in self.store.monitors():
            if not m["enabled"] or (m["last_run"] and now - m["last_run"] < m["interval_s"]):
                continue
            result = evaluate(
                self.system, self.store, evaluators=m["evaluators"], trigger=f"monitor:{m['name']}"
            )
            self.store.db.connection.execute(
                "UPDATE monitors SET last_run=?, last_run_id=? WHERE id=?",
                (now, result["run_id"], m["id"]),
            )
            ran.append(m["id"])
        return ran

    def start(self, every: float = 5.0) -> None:
        async def loop():
            while True:
                try:
                    self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("monitor tick failed")
                await asyncio.sleep(every)

        if self.task is None:
            self.task = asyncio.get_running_loop().create_task(loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None


# ----------------------------------------------------------------------------- experiments


def catalogue() -> list[dict]:
    return [describe(e) for e in REGISTRY.values()]
