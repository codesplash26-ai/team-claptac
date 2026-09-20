"""Agent 6 - Resource & Logistics: which rescue teams and vehicles go to which jobs.

Deterministic, no LLM. Units (from reference data, minus any marked committed in the COP) are matched
to jobs by constrained optimisation (`atlas.tools.optimisation`): serve the most valuable jobs fully,
then minimise travel. A job needs *all* its units or is not served (half a rescue crew is not a
crew), and a unit only goes where a safe route exists: one that crosses no road the COP believes is
likely closed (the same belief map A4 maintains).

Inputs
    TASK_ASSIGNED (from A3)   a `LogisticsTask` addressed to "a6_logistics". Job priorities come
                              from A5's triage ranking, via A3. A3 also turns A7's resupply requests
                              into vehicle jobs.
    COP                       `edge:<u>-<v>` beliefs and `resource:<id>` / `available`.
Outputs
    CANDIDATE_PLAN            a `LogisticsPlan`: assignments with routes, unserved jobs with a reason,
                              and shortage alerts for A3.
    assign()                  the same plan as a synchronous, read-only call for A3 to use inline.
    record_availability()     marks a unit committed or free in the COP.

A plan commits nothing: availability changes only through `record_availability`, so A3 submits one
reconciled plan and replans when the COP version moves. Handlers never raise on bad input (see A4).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from hashlib import sha256

from pydantic import BaseModel, Field, ValidationError

from atlas.agents.base import Agent
from atlas.agents.contracts import (
    LOGISTICS_ASSIGNEE,
    CandidatePlanPayload,
    LatLon,
    LogisticsPlan,
    LogisticsTask,
    RescueJob,
    ShortageAlert,
    UnitAssignment,
    UnservedJob,
)
from atlas.core.events import Belief, Event, EventType
from atlas.tools import geo, optimisation
from atlas.tools.cognitive_map.cop_bridge import RoutingConfig, load_map
from atlas.tools.cognitive_map.routing import alternatives, route_cost
from atlas.tools.reference import Resource

log = logging.getLogger(__name__)

ASSUMPTIONS = (
    "Units are from reference data (synthetic in the demo); a unit is available unless the COP "
    "records it as committed, so unrecorded commitments are not counted.",
    "Routes avoid every road believed likely closed; a unit with no such route is not sent.",
    "Priority is a relative value: two lower-priority jobs can outweigh one higher-priority job.",
    "A plan commits nothing; availability changes only when recorded.",
)


class LogisticsConfig(BaseModel):
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    minutes_weight: int = Field(default=10, ge=0)  # cost per minute of travel
    weakness_weight: int = Field(default=50, ge=0)  # cost for a route's weakest road (0..1 scaled)
    priority_scale: int = Field(default=100, ge=1)  # priorities become integer weights via this
    solver_time_limit_seconds: float = Field(default=2.0, gt=0)


class Logistics(Agent):
    name = LOGISTICS_ASSIGNEE
    subscribes_to = (EventType.TASK_ASSIGNED,)

    def __init__(
        self,
        bus,
        cop,
        ledger,
        *,
        road_graph: geo.RoadGraph,
        resources: list[Resource],
        config: LogisticsConfig | None = None,
        clock=None,
    ):
        super().__init__(bus, cop, ledger)
        self.rg = road_graph
        self.config = config or LogisticsConfig()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._index = self.rg.edge_index()
        self.units = {r.id: r for r in resources if r.site == self.rg.name}
        if not self.units:
            raise ValueError(f"No resources known for {self.rg.name}")
        for r in self.units.values():
            if r.node not in self.rg.graph:
                raise ValueError(f"Depot of {r.id} is not on the {self.rg.name} road graph")

    # --------------------------------------------------------------- availability

    def record_availability(
        self, unit_id: str, available: bool, *, source: str, at: datetime | None = None
    ) -> bool:
        """Mark a unit committed (False) or free (True) in the COP. False if the COP ignored it."""
        if unit_id not in self.units:
            raise ValueError(f"Unknown unit {unit_id!r}")
        if not isinstance(available, bool):
            raise ValueError("available must be a bool")
        return self.cop.put(
            Belief(
                subject=f"resource:{unit_id}",
                attribute="available",
                value=available,
                confidence=1.0,
                provenance=[source],
                updated_at=at or self.clock(),
            )
        )

    def _available(self, snapshot: dict) -> set[str]:
        committed = {
            b["subject"][9:]
            for b in snapshot["beliefs"]
            if b["attribute"] == "available"
            and b["subject"].startswith("resource:")
            and b["value"] is False
        }
        return set(self.units) - committed

    # ------------------------------------------------------------------- planning

    def assign(self, jobs: list[RescueJob], *, now: datetime | None = None) -> LogisticsPlan:
        """Match units to `jobs`. Synchronous, deterministic, COP reads only.

        Raises ValueError for an unknown location or a duplicate job id; the TASK_ASSIGNED path turns
        that into a plan with status "invalid_task".
        """
        cfg = self.config
        now = now or self.clock()
        snapshot = self.cop.snapshot(now=now)
        locations = {j.job_id: self._resolve(j.location) for j in jobs}
        if len(locations) != len(jobs):
            raise ValueError("Duplicate job_id")
        cmap, provenance = load_map(self.rg.graph, self._index, snapshot, now, cfg.routing)
        free = sorted(self._available(snapshot))

        # Safe route from each free unit's depot to each job; cached because units share depots.
        stats_by_route: dict[tuple[int, int], object] = {}
        routes: dict[tuple[str, str], object] = {}
        cost: dict[tuple[str, str], int] = {}
        for job in jobs:
            wanted = {k for k, n in job.needs.items() if n > 0}
            for unit_id in free:
                unit = self.units[unit_id]
                if unit.kind not in wanted:
                    continue
                key = (unit.node, locations[job.job_id])
                if key not in stats_by_route:
                    found = alternatives(
                        cmap, key[0], [key[1]], 1, avoid_below=cfg.routing.close_threshold
                    )
                    stats_by_route[key] = found[0] if found else None
                stats = stats_by_route[key]
                if stats is not None:
                    routes[unit_id, job.job_id] = stats
                    cost[unit_id, job.job_id] = route_cost(
                        stats, cfg.minutes_weight, cfg.weakness_weight
                    )

        result = optimisation.assign_resources(
            [optimisation.Unit(u, self.units[u].kind) for u in free],
            [
                optimisation.Job(
                    j.job_id,
                    max(1, round(j.priority * cfg.priority_scale)),
                    tuple(sorted((k, n) for k, n in j.needs.items())),
                )
                for j in jobs
            ],
            cost,
            time_limit_seconds=cfg.solver_time_limit_seconds,
        )

        assignments, evidence = [], {}
        for job_id, unit_id in result.assignments:
            stats = routes[unit_id, job_id]
            unit = self.units[unit_id]
            assignments.append(
                UnitAssignment(
                    job_id=job_id,
                    unit_id=unit_id,
                    kind=unit.kind,
                    depot_node=unit.node,
                    route_nodes=list(stats.path),
                    minutes=stats.minutes,
                    weakest_belief=stats.weakest_belief
                    if stats.weakest_belief is not None
                    else 1.0,
                )
            )
            for edge in zip(stats.path[:-1], stats.path[1:]):
                evidence.update(dict.fromkeys(provenance.get(edge, [])))

        unserved = [
            self._explain(job, free, cost) for job in jobs if job.job_id in set(result.unserved)
        ]
        unserved.sort(key=lambda u: u.job_id)
        shortages = self._shortages(jobs, unserved, free)

        if not unserved:
            status, reason = "ok", ""
        elif result.served:
            status, reason = "partial", f"{len(unserved)} of {len(jobs)} jobs not served"
        else:
            status, reason = "none_served", "no job could be served; escalate to a human"

        fleet = {}
        for unit in self.units.values():
            row = fleet.setdefault(unit.kind, {"total": 0, "available": 0})
            row["total"] += 1
            row["available"] += unit.id in free

        return LogisticsPlan(
            status=status,
            reason=reason,
            site=self.rg.name,
            snapshot_version=snapshot["version"],
            planned_at=now,
            valid_until=snapshot["valid_until"],
            assignments=assignments,
            unserved=unserved,
            shortages=shortages,
            fleet=fleet,
            solver_status=result.status,
            evidence_ids=list(evidence),
            assumptions=list(ASSUMPTIONS),
        )

    def _explain(self, job: RescueJob, free: list[str], cost) -> UnservedJob:
        """Why a job was left out, judged job by job (ignoring competition from other jobs)."""
        missing: dict[str, int] = {}
        blocked = False
        for kind, need in sorted(job.needs.items()):
            of_kind = [u for u in free if self.units[u].kind == kind]
            reachable = [u for u in of_kind if (u, job.job_id) in cost]
            if len(reachable) < need:
                missing[kind] = need - len(reachable)
                blocked = blocked or len(of_kind) >= need  # enough units exist; roads stop them
        if not missing:  # each need is satisfiable alone, so higher-value jobs took the units
            return UnservedJob(
                job_id=job.job_id,
                priority=job.priority,
                reason="not_enough_units",
                missing=dict(sorted(job.needs.items())),
            )
        return UnservedJob(
            job_id=job.job_id,
            priority=job.priority,
            reason="no_safe_route" if blocked else "not_enough_units",
            missing=missing,
        )

    def _shortages(
        self, jobs: list[RescueJob], unserved: list[UnservedJob], free: list[str]
    ) -> list[ShortageAlert]:
        """Alerts only where jobs were left out for lack of units, not because roads stop them.

        A blocked road is not fixed by sending more vehicles, so it is reported on the unserved job
        (`no_safe_route`) and not as a shortage, which A3 would answer by asking for more units.
        """
        short = {u.job_id for u in unserved if u.reason == "not_enough_units"}
        alerts = []
        for kind in sorted({k for j in jobs for k in j.needs}):
            waiting = sorted(j.job_id for j in jobs if j.job_id in short and kind in j.needs)
            if waiting:
                alerts.append(
                    ShortageAlert(
                        kind=kind,
                        demanded=sum(j.needs.get(kind, 0) for j in jobs),
                        available=sum(1 for u in free if self.units[u].kind == kind),
                        unmet_jobs=waiting,
                    )
                )
        return alerts

    def _resolve(self, location: int | LatLon) -> int:
        if isinstance(location, LatLon):
            return self.rg.nearest_node(location.lat, location.lon)
        if location not in self.rg.graph:
            raise ValueError(f"Location node {location} is not on the {self.rg.name} road graph")
        return location

    # --------------------------------------------------------------------- events

    async def handle(self, event: Event) -> list[Event]:
        if event.type is not EventType.TASK_ASSIGNED or event.payload.get("assignee") != self.name:
            return []
        now = self.clock()
        task_id = str(event.payload.get("task_id") or event.id)
        try:
            task = LogisticsTask.model_validate(event.payload)
            plan = self.assign(task.jobs, now=now)
        except (ValidationError, ValueError) as exc:
            plan = self._failed_plan("invalid_task", str(exc), now)
        except Exception as exc:  # a bug must not wedge the bus; the event is still audited
            log.exception("A6 failed planning task %s", task_id)
            plan = self._failed_plan("error", f"{type(exc).__name__}: {exc}", now)
        payload = CandidatePlanPayload(
            task_id=task_id,
            task="logistics",
            agent=self.name,
            status=plan.status,
            plan=plan.model_dump(mode="json"),
        )
        return [
            Event(
                # Deterministic id: a retried delivery must produce the same event.
                id=sha256(f"{event.id}:candidate_plan:{self.name}".encode()).hexdigest(),
                type=EventType.CANDIDATE_PLAN,
                source=self.name,
                created_at=now,
                incident_id=event.incident_id,
                payload=payload.model_dump(mode="json"),
            )
        ]

    def _failed_plan(self, status: str, reason: str, now: datetime) -> LogisticsPlan:
        return LogisticsPlan(
            status=status,
            reason=reason,
            site=self.rg.name,
            snapshot_version=self.cop.version,
            planned_at=now,
            assumptions=list(ASSUMPTIONS),
        )
