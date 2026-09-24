"""Agent 7 - Shelter & Relief: which shelter takes which group, within capacity and road access.

Deterministic, no LLM. For each evacuee group and shelter it finds the best route that avoids every
road believed likely closed (the same belief map A4 maintains, read from the COP), then solves the
assignment as a constrained optimisation (`atlas.tools.optimisation`): house as many people as
possible, then minimise travel time and risk, never exceeding a shelter's free places.

Inputs
    TASK_ASSIGNED (from A3)   a `ShelterTask` addressed to "a7_shelter".
    COP                       `edge:<u>-<v>` beliefs (roads) and `shelter:<id>` / `occupancy` (people).
Outputs
    CANDIDATE_PLAN            a `ShelterPlan`: placements with routes, unplaced groups and why,
                              resulting occupancy, and resupply requests. Resupply goes to A3 in the
                              plan; only A3 delegates, so A7 never messages A6.
    assign()                  the same plan as a synchronous, read-only call for A3 to use inline.
    record_occupancy()        writes a shelter's current occupancy to the COP (field reports, or
                              confirmed arrivals once missions report headcounts).

A plan reserves nothing: occupancy only changes through `record_occupancy`. Two plans made before
anything is recorded therefore both see the same free places, which is why A3 must submit one
reconciled plan (as the architecture requires) and replan when the COP version moves.

Handlers never raise on bad input; see A4 for why.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from hashlib import sha256

from pydantic import BaseModel, Field, ValidationError

from atlas.agents.base import Agent
from atlas.agents.contracts import (
    SHELTER_ASSIGNEE,
    CandidatePlanPayload,
    EvacueeGroup,
    LatLon,
    Placement,
    ResupplyRequest,
    ShelterPlan,
    ShelterState,
    ShelterTask,
    Unplaced,
)
from atlas.core.events import Belief, Event, EventType
from atlas.tools import geo, optimisation
from atlas.tools.cognitive_map.cop_bridge import RoutingConfig, load_map
from atlas.tools.cognitive_map.routing import alternatives, route_cost
from atlas.tools.reference import Shelter

log = logging.getLogger(__name__)

ASSUMPTIONS = (
    "Shelter capacity is from reference data (synthetic in the demo); occupancy is what has been "
    "recorded in the COP, so unrecorded arrivals are not counted.",
    "Routes avoid every road believed likely closed; a group with no such route is not placed.",
    "Cost per person = travel minutes and route weakness, weighted by ShelterConfig; not a forecast.",
    "A plan reserves nothing; occupancy changes only when recorded.",
)


class ShelterConfig(BaseModel):
    routing: RoutingConfig = Field(default_factory=RoutingConfig)
    minutes_weight: int = Field(default=10, ge=0)  # cost per minute of travel, per person
    weakness_weight: int = Field(default=50, ge=0)  # cost for a route's weakest road (0..1 scaled)
    resupply_utilisation: float = Field(default=0.8, gt=0, le=1)
    urgent_utilisation: float = Field(default=0.95, gt=0, le=1)
    solver_time_limit_seconds: float = Field(default=2.0, gt=0)


class ShelterRelief(Agent):
    name = SHELTER_ASSIGNEE
    subscribes_to = (EventType.TASK_ASSIGNED,)

    def __init__(
        self,
        bus,
        cop,
        ledger,
        *,
        road_graph: geo.RoadGraph,
        shelters: list[Shelter],
        config: ShelterConfig | None = None,
        clock=None,
    ):
        super().__init__(bus, cop, ledger)
        self.rg = road_graph
        self.config = config or ShelterConfig()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._index = self.rg.edge_index()
        self.shelters = {s.id: s for s in shelters if s.site == self.rg.name}
        if not self.shelters:
            raise ValueError(f"No shelters known for {self.rg.name}")
        for s in self.shelters.values():
            if s.node not in self.rg.graph:
                raise ValueError(f"Shelter {s.id} is not on the {self.rg.name} road graph")

    # ------------------------------------------------------------------ occupancy

    def record_occupancy(
        self, shelter_id: str, people: int, *, source: str, at: datetime | None = None
    ) -> bool:
        """Set a shelter's current occupancy in the COP. Returns False if the COP ignored it."""
        shelter = self.shelters.get(shelter_id)
        if shelter is None:
            raise ValueError(f"Unknown shelter {shelter_id!r}")
        valid = isinstance(people, int) and not isinstance(people, bool)
        if not valid or not 0 <= people <= shelter.capacity:
            raise ValueError(f"Occupancy must be 0..{shelter.capacity}, got {people!r}")
        return self.cop.put(
            Belief(
                subject=f"shelter:{shelter_id}",
                attribute="occupancy",
                value=people,
                confidence=1.0,
                provenance=[source],
                updated_at=at or self.clock(),
            )
        )

    def _occupancy(self, snapshot: dict) -> dict[str, int]:
        found = {}
        for b in snapshot["beliefs"]:
            if b["attribute"] == "occupancy" and b["subject"].startswith("shelter:"):
                found[b["subject"][8:]] = int(b["value"])
        return {sid: min(found.get(sid, 0), s.capacity) for sid, s in self.shelters.items()}

    # ------------------------------------------------------------------- planning

    def assign(
        self,
        groups: list[EvacueeGroup],
        shelter_ids: list[str] | None = None,
        *,
        allow_split: bool = True,
        now: datetime | None = None,
    ) -> ShelterPlan:
        """Place `groups` into shelters. Synchronous, deterministic, COP reads only.

        Raises ValueError for an unknown shelter/origin or a duplicate group id; the TASK_ASSIGNED
        path turns that into a plan with status "invalid_task".
        """
        cfg = self.config
        now = now or self.clock()
        snapshot = self.cop.snapshot(now=now)
        chosen = self._resolve_shelters(shelter_ids)
        origins = {g.group_id: self._resolve_origin(g.origin) for g in groups}
        if len(origins) != len(groups):
            raise ValueError("Duplicate group_id")
        cmap, provenance = load_map(self.rg.graph, self._index, snapshot, now, cfg.routing)
        occupancy = self._occupancy(snapshot)

        routes: dict[tuple[str, str], object] = {}
        cost: dict[tuple[str, str], int] = {}
        for g in groups:
            for s in chosen:
                found = alternatives(
                    cmap, origins[g.group_id], [s.node], 1, avoid_below=cfg.routing.close_threshold
                )
                if not found:
                    continue  # no safe route: this pair is never used
                stats = found[0]
                routes[g.group_id, s.id] = stats
                cost[g.group_id, s.id] = route_cost(stats, cfg.minutes_weight, cfg.weakness_weight)

        result = optimisation.assign_people(
            [optimisation.Group(g.group_id, g.people) for g in groups],
            [optimisation.Shelter(s.id, s.capacity - occupancy[s.id]) for s in chosen],
            cost,
            allow_split=allow_split,
            time_limit_seconds=cfg.solver_time_limit_seconds,
        )

        placements, evidence = [], {}
        for group_id, shelter_id, people in result.placements:
            stats = routes[group_id, shelter_id]
            placements.append(
                Placement(
                    group_id=group_id,
                    shelter_id=shelter_id,
                    people=people,
                    route_nodes=list(stats.path),
                    minutes=stats.minutes,
                    weakest_belief=stats.weakest_belief
                    if stats.weakest_belief is not None
                    else 1.0,
                )
            )
            for edge in zip(stats.path[:-1], stats.path[1:]):
                evidence.update(dict.fromkeys(provenance.get(edge, [])))

        unplaced = [
            Unplaced(
                group_id=gid,
                people=n,
                reason="no_capacity"
                if any((gid, s.id) in cost for s in chosen)
                else "no_safe_route",
            )
            for gid, n in sorted(result.unhoused.items())
        ]

        added: dict[str, int] = {}
        for p in placements:
            added[p.shelter_id] = added.get(p.shelter_id, 0) + p.people
        states, resupply = [], []
        for s in chosen:
            after = occupancy[s.id] + added.get(s.id, 0)
            utilisation = after / s.capacity
            states.append(
                ShelterState(
                    shelter_id=s.id,
                    capacity=s.capacity,
                    occupancy_before=occupancy[s.id],
                    occupancy_after=after,
                    utilisation_after=round(utilisation, 4),
                )
            )
            if utilisation >= cfg.resupply_utilisation and added.get(s.id, 0) > 0:
                resupply.append(
                    ResupplyRequest(
                        shelter_id=s.id,
                        reason=f"{after}/{s.capacity} places used after this plan",
                        urgency="high" if utilisation >= cfg.urgent_utilisation else "routine",
                    )
                )

        demand = sum(g.people for g in groups)
        left = sum(u.people for u in unplaced)
        if not unplaced:
            status, reason = "ok", ""
        elif result.housed:
            status, reason = "partial", f"{left} of {demand} people not placed"
        else:
            status, reason = "no_shelter", "no group could be placed; escalate to a human"

        return ShelterPlan(
            status=status,
            reason=reason,
            site=self.rg.name,
            snapshot_version=snapshot["version"],
            planned_at=now,
            valid_until=snapshot["valid_until"],
            placements=placements,
            unplaced=unplaced,
            shelters=states,
            resupply_requests=resupply,
            solver_status=result.status,
            evidence_ids=list(evidence),
            assumptions=list(ASSUMPTIONS),
        )

    def _resolve_shelters(self, ids: list[str] | None) -> list[Shelter]:
        if ids is None:
            return [self.shelters[i] for i in sorted(self.shelters)]
        if not ids:
            raise ValueError("No shelters given")
        unknown = [i for i in ids if i not in self.shelters]
        if unknown:
            raise ValueError(f"Unknown shelter ids: {unknown}")
        return [self.shelters[i] for i in sorted(set(ids))]

    def _resolve_origin(self, origin: int | LatLon) -> int:
        if isinstance(origin, LatLon):
            return self.rg.nearest_node(origin.lat, origin.lon)
        if origin not in self.rg.graph:
            raise ValueError(f"Origin node {origin} is not on the {self.rg.name} road graph")
        return origin

    # --------------------------------------------------------------------- events

    async def handle(self, event: Event) -> list[Event]:
        if event.type is not EventType.TASK_ASSIGNED or event.payload.get("assignee") != self.name:
            return []
        now = self.clock()
        task_id = str(event.payload.get("task_id") or event.id)
        try:
            task = ShelterTask.model_validate(event.payload)
            plan = self.assign(task.groups, task.shelter_ids, allow_split=task.allow_split, now=now)
        except (ValidationError, ValueError) as exc:
            plan = self._failed_plan("invalid_task", str(exc), now)
        except Exception as exc:  # a bug must not wedge the bus; the event is still audited
            log.exception("A7 failed planning task %s", task_id)
            plan = self._failed_plan("error", f"{type(exc).__name__}: {exc}", now)
        payload = CandidatePlanPayload(
            task_id=task_id,
            task="shelter",
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

    def _failed_plan(self, status: str, reason: str, now: datetime) -> ShelterPlan:
        return ShelterPlan(
            status=status,
            reason=reason,
            site=self.rg.name,
            snapshot_version=self.cop.version,
            planned_at=now,
            assumptions=list(ASSUMPTIONS),
        )
