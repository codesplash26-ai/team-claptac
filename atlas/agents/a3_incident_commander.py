"""Agent 3 - Incident Commander & Planner. The only agent that delegates.

Wakes on a Sentinel TRIGGER, works one incident at a time through a persistent state machine, asks the
specialists for plans by event, reconciles what comes back, and submits ONE plan to the Action Gateway.
It never computes a route, a score or an allocation: those come from A4-A7. The LLM is not on this
path (see ``atlas.orchestration.advisor``), so the loop runs with the LLM down.

    TRIGGER -> assessing:  TASK_ASSIGNED(a5 triage)            -> CANDIDATE_PLAN
            -> planning:   TASK_ASSIGNED(a4 routes)            -> CANDIDATE_PLAN
                           TASK_ASSIGNED(a7 shelter, a6 units) -> CANDIDATE_PLAN x2
                           TASK_ASSIGNED(a6 resupply)          -> CANDIDATE_PLAN (only if A7 asks)
            -> awaiting_gateway: PLAN_PROPOSED{actions}        -> ACTION_AUTHORISED / ESCALATED / BLOCKED
            -> active -> standby (every mission completed)
    and back through REPLANNING when roads change, a mission fails, the ranking moves, or the Gateway
    reports a stale or failed action. Replans are bounded; past the bound a human is asked.

State lives in the shared database (``a3_incidents``), so incidents survive a restart and several run
at once, one row each. Handlers only read/write the database and return events: no publishing, no
network, as the bus requires.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ValidationError

from atlas.agents.a8_dispatch import mission_id
from atlas.agents.base import Agent
from atlas.agents.contracts import (
    EVACUATION_ASSIGNEE,
    LOGISTICS_ASSIGNEE,
    SHELTER_ASSIGNEE,
    TRIAGE_ASSIGNEE,
    CandidatePlanPayload,
    EvacuationTask,
    EvacueeGroup,
    LatLon,
    LogisticsTask,
    RescueJob,
    ShelterTask,
    TriageTask,
)
from atlas.core.events import Event, EventType, ProposedAction
from atlas.orchestration.reconcile import ActionSpec, Reconciled, reconcile

log = logging.getLogger(__name__)
NAME = "a3_incident_commander"


class CycleState(str, Enum):
    STANDBY = "standby"
    ASSESSING = "assessing"
    PLANNING = "planning"
    AWAITING_GATEWAY = "awaiting_gateway"
    ACTIVE = "active"
    REPLANNING = "replanning"


BUSY = {CycleState.ASSESSING, CycleState.PLANNING, CycleState.REPLANNING}


class CommanderConfig(BaseModel):
    max_replans: int = 4  # then a human decides; keeps a flapping sensor from looping forever
    group_size: int = 40  # people per evacuation group (one wave)
    max_groups: int = 3  # groups per wave; the rest wait for the next replan
    rescue_standby: bool = True  # hold one rescue team and vehicle at the incident


def _key(*parts: str) -> str:
    return ":".join(parts)


class IncidentCommander(Agent):
    name = NAME
    subscribes_to = (
        EventType.TRIGGER,
        EventType.CANDIDATE_PLAN,
        EventType.RISK_UPDATE,
        EventType.MISSION_STATUS,
        EventType.ACTION_AUTHORISED,
        EventType.ACTION_ESCALATED,
        EventType.ACTION_BLOCKED,
    )

    def __init__(self, bus, cop, ledger, *, config: CommanderConfig | None = None, clock=None):
        super().__init__(bus, cop, ledger)
        self.config = config or CommanderConfig()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.db = cop.db
        self.db.connection.execute(
            "CREATE TABLE IF NOT EXISTS a3_incidents (incident_id TEXT PRIMARY KEY,"
            " body TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )

    # -------------------------------------------------------------------- state

    def incidents(self) -> list[dict[str, Any]]:
        rows = self.db.connection.execute("SELECT body FROM a3_incidents ORDER BY incident_id")
        return [json.loads(r["body"]) for r in rows]

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        row = self.db.connection.execute(
            "SELECT body FROM a3_incidents WHERE incident_id=?", (incident_id,)
        ).fetchone()
        return json.loads(row["body"]) if row else None

    def _save(self, st: dict) -> None:
        now = self.clock().isoformat()
        st["updated_at"] = now
        self.db.connection.execute(
            "INSERT OR REPLACE INTO a3_incidents VALUES (?,?,?)",
            (st["incident_id"], json.dumps(st, sort_keys=True), now),
        )

    def _note(self, st: dict, text: str) -> None:
        st["notes"].append(f"c{st['cycle']}: {text}")
        del st["notes"][:-40]

    def _event(
        self,
        parent: Event,
        kind: EventType,
        tag: str,
        incident_id: str,
        payload: dict,
    ) -> Event:
        # Deterministic id: a retried delivery must produce the same event, not a duplicate.
        return Event(
            id=sha256(f"{parent.id}:{kind.value}:{tag}".encode()).hexdigest(),
            type=kind,
            source=self.name,
            created_at=self.clock(),
            incident_id=incident_id,
            payload=payload,
        )

    # ------------------------------------------------------------------- events

    async def handle(self, event: Event) -> list[Event]:
        t = event.type
        if t is EventType.TRIGGER:
            return self._on_trigger(event)
        if t is EventType.CANDIDATE_PLAN:
            return self._on_plan(event)
        if t is EventType.RISK_UPDATE:
            return self._on_risk(event)
        if t is EventType.MISSION_STATUS:
            return self._on_mission(event)
        return self._on_gateway(event)

    # ---- wake

    def _on_trigger(self, event: Event) -> list[Event]:
        area = event.payload.get("area_id")
        if not area:
            return []
        iid = event.incident_id or f"incident:{area}"
        st = self.get_incident(iid)
        if st is not None and CycleState(st["state"]) in BUSY | {CycleState.AWAITING_GATEWAY}:
            self._note(st, "trigger while a cycle is in progress")
            self._save(st)
            return []
        if st is not None and CycleState(st["state"]) is CycleState.ACTIVE:
            return self._replan(event, st, "trigger_escalation")
        st = st or self._new(iid, area, event)
        st["cycle"] += 1
        return self._begin(event, st)

    def _new(self, iid: str, area: str, event: Event) -> dict:
        hazard = self.cop.get(f"area:{area}", "hazard", include_stale=True)
        level = self.cop.get(f"area:{area}", "risk_level", include_stale=True)
        return {
            "incident_id": iid,
            "area_id": area,
            "area_name": area,
            "hazard": hazard.value if hazard else "flood",
            "level": level.value if level else "warning",
            "state": CycleState.STANDBY.value,
            "cycle": 0,
            "replans": 0,
            "phase": "",
            "pending": {},
            "plans": {},
            "missions": {},
            "outcomes": {},
            "alerts": [],
            "notes": [],
            "needs_human": False,
            "summary": "",
            "opened_at": self.clock().isoformat(),
            "trigger": dict(event.payload),
            "top_priority": None,
        }

    def _begin(self, event: Event, st: dict) -> list[Event]:
        level = self.cop.get(f"area:{st['area_id']}", "risk_level", include_stale=True)
        if level:
            st["level"] = level.value
        st.update(state=CycleState.ASSESSING.value, phase="triage", plans={}, pending={})
        events = self._delegate(
            event,
            st,
            [
                self._task(
                    st,
                    TRIAGE_ASSIGNEE,
                    TriageTask(task_id=_key(st["incident_id"], f"c{st['cycle']}", "triage")),
                )
            ],
        )
        self._save(st)
        return events

    # ---- delegation (only A3 emits TASK_ASSIGNED)

    def _task(self, st: dict, assignee: str, task: BaseModel) -> tuple[str, dict]:
        return assignee, task.model_dump(mode="json")

    def _delegate(self, parent: Event, st: dict, tasks: list[tuple[str, dict]]) -> list[Event]:
        events = []
        for assignee, payload in tasks:
            st["pending"][payload["task_id"]] = assignee
            events.append(
                self._event(
                    parent, EventType.TASK_ASSIGNED, payload["task_id"], st["incident_id"], payload
                )
            )
        return events

    # ---- specialists reply

    def _on_plan(self, event: Event) -> list[Event]:
        try:
            reply = CandidatePlanPayload.model_validate(event.payload)
        except ValidationError:
            return []
        if reply.task_id.startswith("rerank:"):
            return self._on_rerank(event, reply)
        st = self.get_incident(event.incident_id or "")
        if st is None or reply.task_id not in st["pending"]:
            return []  # late, duplicate, or for a cycle that was replaced
        st["pending"].pop(reply.task_id)
        key = reply.agent + (":resupply" if reply.task_id.endswith(":resupply") else "")
        st["plans"][key] = reply.plan
        st["plans"][key]["status"] = reply.status
        if st["pending"]:
            self._save(st)
            return []
        return self._advance(event, st)

    def _advance(self, event: Event, st: dict) -> list[Event]:
        phase = st["phase"]
        if phase == "triage":
            return self._plan_routes(event, st)
        if phase == "routes":
            return self._plan_allocation(event, st)
        if phase == "allocate":
            return self._plan_resupply(event, st)
        return self._propose(event, st)

    def _need(self, st: dict) -> dict | None:
        ranked = (st["plans"].get(TRIAGE_ASSIGNEE) or {}).get("ranked_needs") or []
        return next((n for n in ranked if n["area_id"] == st["area_id"]), None)

    def _plan_routes(self, event: Event, st: dict) -> list[Event]:
        need = self._need(st)
        st["top_priority"] = (st["plans"][TRIAGE_ASSIGNEE].get("ranked_needs") or [{}])[0].get(
            "area_id"
        )
        if need is None or need.get("lat") is None:
            self._note(st, "no location for the incident area; cannot plan routes")
            st["needs_human"] = True
            return self._propose(event, st)
        st["area_name"] = need.get("name") or st["area_id"]
        st["phase"], st["state"] = "routes", CycleState.PLANNING.value
        task = EvacuationTask(
            task_id=_key(st["incident_id"], f"c{st['cycle']}", "routes"),
            origin=LatLon(lat=need["lat"], lon=need["lon"]),
        )
        events = self._delegate(event, st, [self._task(st, EVACUATION_ASSIGNEE, task)])
        self._save(st)
        return events

    def _plan_allocation(self, event: Event, st: dict) -> list[Event]:
        route = st["plans"].get(EVACUATION_ASSIGNEE) or {}
        need = self._need(st)
        if route.get("status") != "ok" or need is None:
            return self._propose(event, st)  # nowhere safe to send anyone: reconcile will escalate
        cfg, origin = self.config, LatLon(lat=need["lat"], lon=need["lon"])
        people = int(need.get("estimated_vulnerable") or 0)
        sizes = []
        while people > 0 and len(sizes) < cfg.max_groups:
            sizes.append(min(cfg.group_size, people))
            people -= sizes[-1]
        st["phase"] = "allocate"
        cid = _key(st["incident_id"], f"c{st['cycle']}")
        tasks: list[tuple[str, dict]] = []
        priority = max(float(need["score"]), 0.01)
        jobs = []
        if sizes:
            tasks.append(
                self._task(
                    st,
                    SHELTER_ASSIGNEE,
                    ShelterTask(
                        task_id=_key(cid, "shelter"),
                        groups=[
                            EvacueeGroup(group_id=f"g{i}", people=n, origin=origin)
                            for i, n in enumerate(sizes, start=1)
                        ],
                    ),
                )
            )
            jobs += [
                RescueJob(job_id=f"g{i}", priority=priority, location=origin, needs={"vehicle": 1})
                for i in range(1, len(sizes) + 1)
            ]
        if cfg.rescue_standby:
            jobs.append(
                RescueJob(
                    job_id=f"rescue:{st['area_id']}",
                    priority=priority,
                    location=origin,
                    needs={"rescue_team": 1, "vehicle": 1},
                )
            )
        if jobs:
            tasks.append(
                self._task(
                    st, LOGISTICS_ASSIGNEE, LogisticsTask(task_id=_key(cid, "logistics"), jobs=jobs)
                )
            )
        if not tasks:
            return self._propose(event, st)
        events = self._delegate(event, st, tasks)
        self._save(st)
        return events

    def _plan_resupply(self, event: Event, st: dict) -> list[Event]:
        """A7 asked for resupply: only A3 may delegate it, so A3 turns it into A6 vehicle jobs."""
        requests = (st["plans"].get(SHELTER_ASSIGNEE) or {}).get("resupply_requests") or []
        placements = (st["plans"].get(SHELTER_ASSIGNEE) or {}).get("placements") or []
        end_node = {p["shelter_id"]: p["route_nodes"][-1] for p in placements if p["route_nodes"]}
        jobs = [
            RescueJob(
                job_id=f"resupply:{r['shelter_id']}",
                location=end_node[r["shelter_id"]],
                priority=1.0 if r["urgency"] == "high" else 0.3,
                needs={"vehicle": 1},
            )
            for r in requests
            if r["shelter_id"] in end_node
        ]
        if not jobs:
            return self._propose(event, st)
        st["phase"] = "resupply"
        task = LogisticsTask(
            task_id=_key(st["incident_id"], f"c{st['cycle']}", "logistics", "resupply"), jobs=jobs
        )
        events = self._delegate(event, st, [self._task(st, LOGISTICS_ASSIGNEE, task)])
        self._save(st)
        return events

    # ---- reconcile and propose

    def _propose(self, event: Event, st: dict) -> list[Event]:
        stale = [
            k
            for k, p in st["plans"].items()
            if p.get("snapshot_version", self.cop.version) != self.cop.version
        ]
        if stale:
            self._note(st, f"plans from {stale} went stale before reconciling")
            return self._replan(event, st, "stale_inputs")
        result = self._reconcile(st)
        st["needs_human"] = st["needs_human"] or result.escalate
        st["summary"] = result.summary
        for reason in result.reasons:
            self._note(st, reason)
        actions = [self._to_action(st, spec) for spec in result.actions]
        self._track_missions(st, result, actions)
        st["state"] = CycleState.AWAITING_GATEWAY.value
        st["phase"] = "proposed"
        self._save(st)
        if not actions:
            return []
        return [
            self._event(
                event,
                EventType.PLAN_PROPOSED,
                f"c{st['cycle']}",
                st["incident_id"],
                {
                    "actions": [a.model_dump(mode="json") for a in actions],
                    "conflicts": result.conflicts,
                    "escalate": result.escalate,
                    "cycle": st["cycle"],
                },
            )
        ]

    def _reconcile(self, st: dict) -> Reconciled:
        plans = dict(st["plans"])
        logistics = dict(plans.get(LOGISTICS_ASSIGNEE) or {})
        resupply = plans.pop(LOGISTICS_ASSIGNEE + ":resupply", None)
        if resupply:  # one logistics view for the reconciler
            logistics["assignments"] = [
                *(logistics.get("assignments") or []),
                *(resupply.get("assignments") or []),
            ]
            logistics["unserved"] = [
                *(logistics.get("unserved") or []),
                *(resupply.get("unserved") or []),
            ]
            plans[LOGISTICS_ASSIGNEE] = logistics
        return reconcile(
            incident_id=st["incident_id"],
            area_id=st["area_id"],
            area_name=st["area_name"],
            hazard=st["hazard"],
            level=st["level"],
            plans=plans,
            previous_missions={
                g: m
                for g, m in st["missions"].items()
                if m.get("status") not in ("failed", "cancelled", "completed")
            },
            cycle=st["cycle"],
            alerted=st.setdefault("alerts", []),
        )

    def _to_action(self, st: dict, spec: ActionSpec) -> ProposedAction:
        stable = spec.slot.split(":")[0] in ("mission", "alert")  # same intent across replans
        key = (
            _key(st["incident_id"], spec.slot)
            if stable
            else _key(st["incident_id"], f"c{st['cycle']}", spec.slot)
        )
        return ProposedAction(
            idempotency_key=key,
            incident_id=st["incident_id"],
            proposed_by=self.name,
            executor=spec.executor,
            action_type=spec.action_type,
            params=spec.params,
            rationale=spec.rationale,
            snapshot_version=self.cop.version,
        )

    def _track_missions(self, st: dict, result: Reconciled, actions: list[ProposedAction]) -> None:
        for action in actions:
            if action.action_type == "publish_alert":
                st.setdefault("alerts", []).append(action.params["severity"])
            if action.action_type != "create_mission":
                continue
            gid = action.params.get("group_id")
            if gid in result.missions:
                mission = result.missions[gid]
                st["missions"][gid] = {
                    "mission_id": mission_id(action.idempotency_key),
                    "status": "proposed",
                    "shelter_id": mission["shelter_id"],
                    "route": mission["route"],
                    "people": mission["people"],
                    "units": mission["units"],
                }
        for gid, mission in result.missions.items():
            prior = st["missions"].get(gid)
            if prior and mission.get("mission_id") and prior["route"] != mission["route"]:
                prior["route"] = mission["route"]  # a reroute_group was proposed

    # ---- replan

    def _replan(self, event: Event, st: dict, reason: str) -> list[Event]:
        if st["replans"] >= self.config.max_replans:
            st["needs_human"] = True
            self._note(
                st,
                f"replan limit ({self.config.max_replans}) reached after {reason}; "
                "a human must decide",
            )
            self._save(st)
            return []
        st["replans"] += 1
        st["cycle"] += 1
        st["state"] = CycleState.REPLANNING.value
        self._note(st, f"replanning: {reason}")
        return self._begin(event, st)

    def _on_rerank(self, event: Event, reply: CandidatePlanPayload) -> list[Event]:
        ranked = reply.plan.get("ranked_needs") or []
        top = ranked[0]["area_id"] if ranked else None
        out: list[Event] = []
        for st in [i for i in self.incidents() if i["state"] == CycleState.ACTIVE.value]:
            if top and st["top_priority"] and top != st["top_priority"]:
                out += self._replan(event, st, f"triage priority moved to {top}")
        return out

    def _on_risk(self, event: Event) -> list[Event]:
        area, kind = event.payload.get("area_id"), event.payload.get("kind")
        out: list[Event] = []
        for st in self.incidents():
            if st["area_id"] != area or st["state"] != CycleState.ACTIVE.value:
                continue
            if kind == "road_access_changed":
                out += self._replan(event, st, "road access changed")
            elif (
                kind == "hazard_risk"
                and event.payload.get("area_level") == "severe"
                and st["level"] != "severe"
            ):
                st["level"] = "severe"
                out += self._replan(event, st, "risk rose to severe")
        return out

    # ---- feedback from A8 and the Gateway

    def _on_mission(self, event: Event) -> list[Event]:
        st = self.get_incident(event.incident_id or "")
        if st is None:
            return []
        mid, status = event.payload.get("mission_id"), event.payload.get("status")
        group = next((g for g, m in st["missions"].items() if m["mission_id"] == mid), None)
        if group is None:
            return []
        st["missions"][group]["status"] = status
        if event.payload.get("exception"):
            self._note(st, f"mission {mid}: {event.payload['exception']}")
        if status == "failed":
            return self._replan(event, st, f"mission {mid} failed")
        if all(m["status"] in ("completed", "cancelled") for m in st["missions"].values()):
            st["state"] = CycleState.STANDBY.value
            self._note(st, "every mission finished; standing by")
        self._save(st)
        return []

    def _on_gateway(self, event: Event) -> list[Event]:
        st = self.get_incident(event.incident_id or "")
        decision = event.payload.get("decision") or {}
        action = event.payload.get("action") or {}
        if st is None or not decision:
            return []
        key, status, reason = (
            decision.get("idempotency_key"),
            decision.get("status"),
            decision.get("reason"),
        )
        st["outcomes"][key] = {
            "status": status,
            "reason": reason,
            "action_type": action.get("action_type"),
        }
        gid = (action.get("params") or {}).get("group_id")
        if event.type is EventType.ACTION_AUTHORISED:
            if st["state"] == CycleState.AWAITING_GATEWAY.value:
                st["state"] = CycleState.ACTIVE.value
            self._save(st)
            return []
        if event.type is EventType.ACTION_ESCALATED:
            self._note(st, f"{action.get('action_type')} is waiting for the Incident Commander")
            self._save(st)
            return []
        # blocked, rejected, or failed after authorisation
        if action.get("action_type") == "create_mission" and gid:
            st["missions"].pop(gid, None)
        if action.get("action_type") == "publish_alert":
            severity = (action.get("params") or {}).get("severity")
            if severity in st.get("alerts", []):
                st["alerts"].remove(severity)  # it never went out; a later cycle may retry
        if reason == "stale_snapshot" or status in ("compensated", "failed_uncompensated"):
            self._note(st, f"{action.get('action_type')} {status}: {reason}")
            return self._replan(event, st, f"gateway {status}: {reason}")
        st["needs_human"] = True
        self._note(st, f"{action.get('action_type')} {status}: {reason}")
        self._save(st)
        return []
