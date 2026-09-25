"""Agent 8 - Dispatch & Mission Execution.

Turns Gateway-authorised actions into trackable mission tickets and follows them until
they finish. A8 never decides anything: it is an *executor* the Gateway calls after an
action is authorised (execute / verify / compensate), plus a small API for field
updates (acknowledge, progress, exception, complete).

Deterministic; no LLM. Every method is idempotent on the action's idempotency key.

Inputs : authorised create_mission / update_mission / cancel_mission / reroute_group /
         close_major_route actions; field updates.
Outputs: MISSION_STATUS events (returned, for the caller to publish); the ``missions``
         table; ``edge:<road>.open`` beliefs in the COP for closed routes.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, Field

from atlas.agents.base import Agent
from atlas.control.models import ExecutionResult, Receipt
from atlas.core.events import Belief, Event, EventType, ProposedAction


class MissionStatus(str, Enum):
    CREATED = "created"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE = (MissionStatus.CREATED, MissionStatus.ACKNOWLEDGED, MissionStatus.IN_PROGRESS)
TRANSITIONS = {
    "acknowledge": ({MissionStatus.CREATED}, MissionStatus.ACKNOWLEDGED),
    "progress": (
        {MissionStatus.ACKNOWLEDGED, MissionStatus.IN_PROGRESS},
        MissionStatus.IN_PROGRESS,
    ),
    "complete": ({MissionStatus.ACKNOWLEDGED, MissionStatus.IN_PROGRESS}, MissionStatus.COMPLETED),
    "fail": (set(ACTIVE), MissionStatus.FAILED),
    "cancel": (set(ACTIVE), MissionStatus.CANCELLED),
}


class MissionError(ValueError):
    """Unknown mission, or a transition its current state does not allow."""


class Mission(BaseModel):
    id: str
    incident_id: str
    action_key: str
    title: str
    team: str | None = None
    status: MissionStatus
    progress: int = Field(ge=0, le=100, default=0)
    priority: str = "normal"
    destination: str | None = None
    route: list[Any] = Field(default_factory=list)
    resources_committed: int = 0
    exception: str | None = None
    created_at: datetime
    updated_at: datetime
    acknowledged_at: datetime | None = None


def mission_id(action_key: str) -> str:
    return "m-" + sha256(action_key.encode()).hexdigest()[:12]


class Dispatch(Agent):
    name = "a8_dispatch"
    subscribes_to = ()  # acts only when the Gateway executes an authorised action

    def __init__(self, bus, cop, ledger, *, clock=None, ack_timeout_seconds: int = 300):
        super().__init__(bus, cop, ledger)
        self.db = cop.db
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ack_timeout = timedelta(seconds=ack_timeout_seconds)
        self.db.connection.executescript("""
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, action_key TEXT NOT NULL,
                title TEXT NOT NULL, team TEXT, status TEXT NOT NULL, progress INTEGER NOT NULL,
                priority TEXT NOT NULL, destination TEXT, route TEXT NOT NULL,
                resources_committed INTEGER NOT NULL, exception TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, acknowledged_at TEXT);
            CREATE INDEX IF NOT EXISTS missions_incident ON missions(incident_id, status);
            CREATE TABLE IF NOT EXISTS mission_log (
                mission_id TEXT NOT NULL, action_key TEXT NOT NULL, at TEXT NOT NULL,
                status TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (mission_id, action_key));
        """)

    async def handle(self, event: Event) -> list[Event]:
        return []

    # ------------------------------------------------------------ reads

    def get(self, mission: str) -> Mission | None:
        row = self.db.connection.execute("SELECT * FROM missions WHERE id=?", (mission,)).fetchone()
        return self._model(row) if row else None

    def list(self, incident_id: str | None = None, status: MissionStatus | None = None):
        rows = self.db.connection.execute(
            "SELECT * FROM missions WHERE (? IS NULL OR incident_id=?) AND (? IS NULL OR status=?)"
            " ORDER BY created_at, id",
            (incident_id, incident_id, status and status.value, status and status.value),
        )
        return [self._model(row) for row in rows]

    def history(self, mission: str) -> list[dict]:
        return [
            dict(r)
            for r in self.db.connection.execute(
                "SELECT at, status, note, action_key FROM mission_log WHERE mission_id=? ORDER BY at, "
                "rowid",
                (mission,),
            )
        ]

    @staticmethod
    def _model(row) -> Mission:
        return Mission(**{**dict(row), "route": json.loads(row["route"])})

    # ------------------------------------------- Gateway executor protocol

    def execute(self, action: ProposedAction) -> ExecutionResult:
        now, p = self.clock(), action.params
        handler = {
            "create_mission": self._create,
            "update_mission": self._update,
            "cancel_mission": self._cancel,
            "reroute_group": self._reroute,
            "close_major_route": self._close_route,
        }.get(action.action_type)
        if handler is None:
            raise MissionError(f"A8 cannot execute {action.action_type!r}")
        with self.db.transaction():
            ref, events = handler(action, p, now)
        return ExecutionResult(
            receipt=Receipt(ref=ref, executed_at=now, detail={"action_type": action.action_type}),
            events=events,
        )

    def verify(self, action: ProposedAction, result: ExecutionResult) -> bool:
        if action.action_type == "close_major_route":
            belief = self.cop.get(f"edge:{action.params['road_id']}", "open", include_stale=True)
            return bool(belief and belief.value is False)
        return (
            self.db.connection.execute(
                "SELECT 1 FROM mission_log WHERE mission_id=? AND action_key=?",
                (result.receipt.ref, action.idempotency_key),
            ).fetchone()
            is not None
        )

    def compensate(self, action: ProposedAction, result: ExecutionResult | None) -> None:
        """Undo a half-done create. Other actions are single atomic transactions."""
        if action.action_type != "create_mission":
            return
        mission = mission_id(action.idempotency_key)
        with self.db.transaction():
            row = self.db.connection.execute(
                "SELECT status FROM missions WHERE id=?", (mission,)
            ).fetchone()
            if row and row["status"] == MissionStatus.CREATED.value:
                self._set(
                    mission,
                    MissionStatus.CANCELLED,
                    self.clock(),
                    note="compensated: creation failed verification",
                    key=f"{action.idempotency_key}:compensate",
                )

    # ------------------------------------------------- action handlers

    def _create(self, action, p, now):
        title = self._need(p, "title", str)
        mid = mission_id(action.idempotency_key)
        if self.get(mid) is None:
            committed = p.get("resources_committed", 0)
            if isinstance(committed, bool) or not isinstance(committed, int) or committed < 0:
                raise MissionError("resources_committed must be a non-negative integer")
            self.db.connection.execute(
                "INSERT INTO missions(id, incident_id, action_key, title, team, status, progress,"
                " priority, destination, route, resources_committed, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mid,
                    action.incident_id,
                    action.idempotency_key,
                    title,
                    p.get("team"),
                    MissionStatus.CREATED.value,
                    0,
                    str(p.get("priority", "normal")),
                    p.get("destination"),
                    json.dumps(p.get("route", [])),
                    committed,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self._log(mid, action.idempotency_key, now, MissionStatus.CREATED, "created")
            return mid, [self._status_event(mid, "created")]
        return mid, []

    def _update(self, action, p, now):
        mid = self._existing(p)
        if self._logged(mid, action.idempotency_key):
            return mid, []
        sets = {k: p[k] for k in ("team", "priority", "destination") if k in p}
        if sets:
            cols = ", ".join(f"{k}=?" for k in sets)
            self.db.connection.execute(
                f"UPDATE missions SET {cols}, updated_at=? WHERE id=?",
                (*sets.values(), now.isoformat(), mid),
            )
        self._log(
            mid, action.idempotency_key, now, self.get(mid).status, str(p.get("note", "updated"))
        )
        return mid, [self._status_event(mid, "updated")]

    def _cancel(self, action, p, now):
        mid = self._existing(p)
        if self._logged(mid, action.idempotency_key):
            return mid, []
        self._transition(
            mid, "cancel", now, str(p.get("reason", "cancelled")), action.idempotency_key
        )
        return mid, [self._status_event(mid, "cancelled")]

    def _reroute(self, action, p, now):
        mid = self._existing(p)
        if self._logged(mid, action.idempotency_key):
            return mid, []
        route = p.get("route")
        if not isinstance(route, list) or not route:
            raise MissionError("reroute_group needs a non-empty route list")
        if self.get(mid).status not in ACTIVE:
            raise MissionError(f"mission {mid} is no longer active")
        self.db.connection.execute(
            "UPDATE missions SET route=?, updated_at=? WHERE id=?",
            (json.dumps(route), now.isoformat(), mid),
        )
        self._log(mid, action.idempotency_key, now, self.get(mid).status, "rerouted")
        return mid, [self._status_event(mid, "rerouted")]

    def _close_route(self, action, p, now):
        road = self._need(p, "road_id", str)
        self.cop.put(
            Belief(
                subject=f"edge:{road}",
                attribute="open",
                value=False,
                confidence=1.0,
                provenance=[f"action:{action.idempotency_key}"],
                updated_at=now,
            )
        )
        return road, []

    # ----------------------------------------------------- field updates
    # Return the MISSION_STATUS event; the caller publishes it (API, outside a handler).

    def acknowledge(self, mission: str, by: str = "") -> Event:
        with self.db.transaction():
            now = self.clock()
            self._transition(
                mission, "acknowledge", now, f"acknowledged by {by}".strip(), f"field:ack:{mission}"
            )
            self.db.connection.execute(
                "UPDATE missions SET acknowledged_at=?, exception=NULL WHERE id=?",
                (now.isoformat(), mission),
            )
        return self._status_event(mission, "acknowledged")

    def report_progress(self, mission: str, percent: int, note: str = "") -> Event:
        if isinstance(percent, bool) or not isinstance(percent, int) or not 0 <= percent <= 100:
            raise MissionError("progress must be an integer from 0 to 100")
        with self.db.transaction():
            now = self.clock()
            self._transition(
                mission,
                "progress",
                now,
                note or f"{percent}%",
                f"field:progress:{mission}:{now.isoformat()}",
            )
            self.db.connection.execute(
                "UPDATE missions SET progress=? WHERE id=?", (percent, mission)
            )
        return self._status_event(mission, "progress")

    def complete(self, mission: str, note: str = "") -> Event:
        with self.db.transaction():
            now = self.clock()
            self._transition(mission, "complete", now, note or "completed", f"field:done:{mission}")
            self.db.connection.execute("UPDATE missions SET progress=100 WHERE id=?", (mission,))
        return self._status_event(mission, "completed")

    def report_exception(self, mission: str, description: str, fatal: bool = False) -> Event:
        """Flag a problem to the Incident Commander. ``fatal`` also fails the mission."""
        if not description.strip():
            raise MissionError("an exception report needs a description")
        with self.db.transaction():
            now = self.clock()
            current = self._require(mission)
            if current.status not in ACTIVE:
                raise MissionError(f"mission {mission} is {current.status.value}")
            if fatal:
                self._transition(mission, "fail", now, description, f"field:fail:{mission}")
            else:
                self._log(
                    mission,
                    f"field:exception:{mission}:{now.isoformat()}",
                    now,
                    current.status,
                    description,
                )
            self.db.connection.execute(
                "UPDATE missions SET exception=?, updated_at=? WHERE id=?",
                (description, now.isoformat(), mission),
            )
        return self._status_event(mission, "exception")

    def check_overdue(self) -> list[Event]:
        """Flag missions nobody acknowledged in time. Call periodically; idempotent."""
        now = self.clock()
        events = []
        for m in self.list(status=MissionStatus.CREATED):
            if m.exception is None and now - m.created_at >= self.ack_timeout:
                events.append(
                    self.report_exception(
                        m.id, f"not acknowledged within {int(self.ack_timeout.total_seconds())}s"
                    )
                )
        return events

    # ------------------------------------------------------- internals

    def _need(self, params, name, kind):
        value = params.get(name)
        if not isinstance(value, kind) or (kind is str and not value.strip()):
            raise MissionError(f"parameter {name!r} is required")
        return value

    def _existing(self, params) -> str:
        mid = self._need(params, "mission_id", str)
        self._require(mid)
        return mid

    def _require(self, mission: str) -> Mission:
        found = self.get(mission)
        if found is None:
            raise MissionError(f"unknown mission {mission}")
        return found

    def _logged(self, mission, key) -> bool:
        return (
            self.db.connection.execute(
                "SELECT 1 FROM mission_log WHERE mission_id=? AND action_key=?", (mission, key)
            ).fetchone()
            is not None
        )

    def _log(self, mission, key, now, status, note=""):
        self.db.connection.execute(
            "INSERT OR IGNORE INTO mission_log VALUES (?,?,?,?,?)",
            (mission, key, now.isoformat(), status.value, note),
        )

    def _set(self, mission, status, now, *, note, key):
        self.db.connection.execute(
            "UPDATE missions SET status=?, updated_at=? WHERE id=?",
            (status.value, now.isoformat(), mission),
        )
        self._log(mission, key, now, status, note)

    def _transition(self, mission, verb, now, note, key):
        current = self._require(mission)
        allowed, target = TRANSITIONS[verb]
        if current.status not in allowed:
            raise MissionError(f"cannot {verb} a mission that is {current.status.value}")
        self._set(mission, target, now, note=note, key=key)

    def _status_event(self, mission: str, change: str) -> Event:
        m = self._require(mission)
        return Event(
            type=EventType.MISSION_STATUS,
            source=self.name,
            created_at=self.clock(),  # the runtime's clock, like every other event
            incident_id=m.incident_id,
            payload={
                "mission_id": m.id,
                "change": change,
                "status": m.status.value,
                "progress": m.progress,
                "exception": m.exception,
                "title": m.title,
            },
        )
