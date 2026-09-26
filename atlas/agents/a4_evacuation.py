"""Agent 4 - Evacuation & Access: belief-weighted routes on the real road network.

Wraps the cognitive-map engine (`atlas.tools.cognitive_map`). Deterministic, no LLM: routes, travel
times and risk scores are computed, never generated, and the agent keeps working if the LLM layer is
down.

Inputs
    OBSERVATION_VALIDATED (from A1)  water-level readings for a mapped area become evidence about
                                     nearby roads (`atlas.tools.sensor_map`); so do landslide signals
                                     (soil wetness, slope tilt) and earthquake reports, which affect
                                     steep roads.
    TASK_ASSIGNED (from A3)          "plan routes from X to these destinations".
    COP                              `edge:<u>-<v>` / `belief_open` (this agent) and `edge:<u>-<v>` /
                                     `open` = False (A8's hard closures via close_major_route).

Outputs
    COP                              `edge:<u>-<v>` / `belief_open`, written only when a belief moves
                                     by more than `write_epsilon` (every write bumps the COP version
                                     and can make an in-flight plan stale at the Gateway).
    RISK_UPDATE                      when roads newly close or reopen, so A3 can replan.
    CANDIDATE_PLAN                   the RoutePlan for a task, addressed by `task_id`.
    plan_evacuation()                the same plan as a synchronous call, for A3 to use inline.

Time decay is computed when a plan (or an evidence update) reads the map and is never written to the
COP; otherwise every plan would see a changed COP version and be rejected as stale.

Handlers never raise on bad input. The bus retries a failing handler's event forever, so a malformed
task would wedge it; an invalid task yields a plan with status "invalid_task" instead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from hashlib import sha256

from pydantic import ValidationError

from atlas.agents.base import Agent
from atlas.agents.contracts import (
    EVACUATION_ASSIGNEE,
    CandidatePlanPayload,
    EvacuationTask,
    LatLon,
    RouteOption,
    RoutePlan,
)
from atlas.core.events import Belief, Event, EventType, Observation
from atlas.tools import geo
from atlas.tools.cognitive_map.beliefs import CognitiveMap
from atlas.tools.cognitive_map.cop_bridge import RoutingConfig, load_map, minutes
from atlas.tools.cognitive_map.routing import alternatives
from atlas.tools.reference import Shelter
from atlas.tools.sensor_map import SLOPE_ATTRIBUTES, SensorMap, road_reports, slope_reports

log = logging.getLogger(__name__)

ASSUMPTIONS = (
    "Route success probability multiplies edge beliefs, treating road failures as independent.",
    "Travel times are OpenStreetMap speed estimates with a 10% per metre uphill penalty.",
    "Sensor readings become road evidence through a demo flood scale (data/reference/sensor_map.yaml).",
    "Landslide and earthquake signals affect steep roads near the area, a demo rule, not geotechnics.",
    "Shelter capacity shown is static; live occupancy and assignment belong to A7.",
)


EvacuationConfig = RoutingConfig  # A4's name for the shared planning-agent config


class Evacuation(Agent):
    name = EVACUATION_ASSIGNEE
    subscribes_to = (EventType.OBSERVATION_VALIDATED, EventType.TASK_ASSIGNED)

    def __init__(
        self,
        bus,
        cop,
        ledger,
        *,
        road_graph: geo.RoadGraph,
        sensor_map: SensorMap,
        shelters: list[Shelter] | tuple = (),
        config: EvacuationConfig | None = None,
        clock=None,
    ):
        super().__init__(bus, cop, ledger)
        self.rg = road_graph
        self.config = config or EvacuationConfig()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._index = self.rg.edge_index()
        self.shelters = {s.id: s for s in shelters if s.site == self.rg.name}
        self._shelter_by_node = {s.node: s for s in self.shelters.values()}
        self.areas = {a.area_id: a for a in sensor_map.areas.values() if a.site == self.rg.name}
        SensorMap(list(self.areas.values())).validate({self.rg.name: self.rg})
        for shelter in self.shelters.values():
            if shelter.node not in self.rg.graph:
                raise ValueError(f"Shelter {shelter.id} is not on the {self.rg.name} road graph")

    # ------------------------------------------------------------------ events

    async def handle(self, event: Event) -> list[Event]:
        if event.type is EventType.OBSERVATION_VALIDATED:
            return self._on_observation(event)
        if event.type is EventType.TASK_ASSIGNED:
            return self._on_task(event)
        return []

    def _event(self, parent: Event, kind: EventType, payload: dict, now: datetime) -> Event:
        # Deterministic id: a retried delivery must produce the same event, not a duplicate.
        return Event(
            id=sha256(f"{parent.id}:{kind.value}:{self.name}".encode()).hexdigest(),
            type=kind,
            source=self.name,
            created_at=now,
            incident_id=parent.incident_id,
            payload=payload,
        )

    # -------------------------------------------------- evidence -> beliefs -> COP

    def _on_observation(self, event: Event) -> list[Event]:
        try:
            obs = Observation.model_validate(event.payload)
        except ValidationError:
            log.warning("A4 ignoring malformed validated observation %s", event.id)
            return []
        area = self.areas.get(obs.area_id)
        if area is None or obs.attribute not in {"water_level", *SLOPE_ATTRIBUTES}:
            return []  # not an area or measurement this agent maps onto roads

        now = self.clock()
        snapshot = self.cop.snapshot(now=now)
        cmap, _ = self._load_map(snapshot, now)
        # Decay is applied first (as the engine does each step) but not persisted: the baseline is
        # taken after decay, so only what this observation changes is written back.
        cmap.mark_persisted()
        before = {(u, v): d["belief_open"] for u, v, d in cmap.map.edges(data=True)}

        step = minutes(obs.observed_at)
        reports = (
            road_reports(area, self.rg, obs.value, obs.confidence)
            if obs.attribute == "water_level"
            else slope_reports(area, self.rg, obs.attribute, obs.value, obs.confidence)
        )
        for r in reports:
            cmap.update_from_report(r.u, r.v, r.reported_open, step, r.reliability)

        changed = cmap.changed_edges(self.config.write_epsilon)
        provenance = [*obs.evidence_ids, f"sensor_map:{obs.area_id}"]
        for u, v in changed:
            d = cmap.map[u][v]
            self.cop.put(
                Belief(
                    subject=f"edge:{geo.edge_id(u, v)}",
                    attribute="belief_open",
                    value=round(d["belief_open"], 6),
                    confidence=round(1.0 - d["uncertainty"], 6),
                    provenance=provenance,
                    updated_at=obs.observed_at,
                )
            )

        cfg = self.config
        closed = [
            geo.edge_id(u, v)
            for u, v in changed
            if before[(u, v)] >= cfg.close_threshold > cmap.map[u][v]["belief_open"]
        ]
        # Beliefs recover gradually (0.05 -> 0.3 -> 0.5 ...), so "reopened" is crossing the higher
        # threshold from below it, not a single jump from closed to open.
        reopened = [
            geo.edge_id(u, v)
            for u, v in changed
            if before[(u, v)] < cfg.reopen_threshold <= cmap.map[u][v]["belief_open"]
        ]
        if not closed and not reopened:
            return []
        return [
            self._event(
                event,
                EventType.RISK_UPDATE,
                {
                    "kind": "road_access_changed",
                    "site": self.rg.name,
                    "area_id": obs.area_id,
                    "level_cm": obs.value if obs.attribute == "water_level" else None,
                    "attribute": obs.attribute,
                    "value": obs.value,
                    "newly_closed": sorted(closed),
                    "newly_reopened": sorted(reopened),
                    "observation_id": obs.observation_id,
                    "snapshot_version": self.cop.version,
                    "evidence_ids": obs.evidence_ids,
                },
                now,
            )
        ]

    # ------------------------------------------------------------- COP -> map

    def _load_map(self, snapshot: dict, now: datetime) -> tuple[CognitiveMap, dict]:
        return load_map(self.rg.graph, self._index, snapshot, now, self.config)

    # ------------------------------------------------------------------ routing

    def plan_evacuation(
        self,
        origin: int | LatLon,
        destinations: list[int | str] | None = None,
        *,
        k: int = 3,
        now: datetime | None = None,
    ) -> RoutePlan:
        """Ranked routes from `origin` to any destination, with per-route risk.

        Synchronous, deterministic and safe inside a bus handler (COP reads only). Raises ValueError
        for an unknown origin or destination; the TASK_ASSIGNED path turns that into a plan with
        status "invalid_task".
        """
        now = now or self.clock()
        snapshot = self.cop.snapshot(now=now)
        origin_node = self._resolve_origin(origin)
        dest_nodes = self._resolve_destinations(destinations)
        cmap, provenance = self._load_map(snapshot, now)

        # Prefer routes that cross no road believed likely closed. Only if none exist, fall back to
        # the cheapest routes and flag them unsafe: a human must decide whether to risk them.
        ranked = alternatives(
            cmap, origin_node, dest_nodes, k, avoid_below=self.config.close_threshold
        )
        safe = bool(ranked)
        if not safe:
            ranked = alternatives(cmap, origin_node, dest_nodes, k)

        options: list[RouteOption] = []
        per_destination: dict[int, int] = {}
        evidence: dict[str, None] = {}
        for rank, stats in enumerate(ranked, start=1):
            dest = stats.path[-1]
            shelter = self._shelter_by_node.get(dest)
            per_destination[dest] = per_destination.get(dest, 0) + 1
            label = shelter.id if shelter else f"node-{dest}"
            hops = list(zip(stats.path[:-1], stats.path[1:]))
            observed = sum(edge in provenance for edge in hops)
            options.append(
                RouteOption(
                    route_id=f"{label}#{per_destination[dest]}",
                    rank=rank,
                    nodes=list(stats.path),
                    destination=dest,
                    shelter_id=shelter.id if shelter else None,
                    capacity=shelter.capacity if shelter else None,
                    hops=len(hops),
                    safe=safe,
                    observed_edges=observed,
                    unobserved_edges=len(hops) - observed,
                    minutes=stats.minutes,
                    cost=stats.cost,
                    success_probability=stats.success_probability,
                    failure_probability=1.0 - stats.success_probability,
                    weakest_edge=geo.edge_id(*stats.weakest_edge) if stats.weakest_edge else None,
                    weakest_belief=stats.weakest_belief,
                    mean_uncertainty=stats.mean_uncertainty,
                )
            )
            for edge in hops:
                evidence.update(dict.fromkeys(provenance.get(edge, [])))

        if safe:
            status, reason = "ok", ""
        elif options:
            status = "no_safe_route"
            reason = (
                f"every route to a destination crosses a road believed likely closed "
                f"(belief below {self.config.close_threshold}); escalate to a human"
            )
        else:
            status, reason = "no_route", "no destination is reachable over the road network"

        return RoutePlan(
            status=status,
            reason=reason,
            site=self.rg.name,
            origin=origin_node,
            destinations=dest_nodes,
            snapshot_version=snapshot["version"],
            planned_at=now,
            valid_until=snapshot["valid_until"],
            options=options,
            evidence_ids=list(evidence),
            assumptions=list(ASSUMPTIONS),
        )

    def _resolve_origin(self, origin: int | LatLon) -> int:
        if isinstance(origin, LatLon):
            return self.rg.nearest_node(origin.lat, origin.lon)
        if origin not in self.rg.graph:
            raise ValueError(f"Origin node {origin} is not on the {self.rg.name} road graph")
        return origin

    def _resolve_destinations(self, destinations) -> list[int]:
        if destinations is None:
            if not self.shelters:
                raise ValueError(f"No shelters are known for {self.rg.name}; give destinations")
            return sorted({s.node for s in self.shelters.values()})
        nodes = []
        for item in destinations:
            if isinstance(item, str):
                if item not in self.shelters:
                    raise ValueError(f"Unknown shelter id {item!r}")
                nodes.append(self.shelters[item].node)
            elif item in self.rg.graph:
                nodes.append(item)
            else:
                raise ValueError(f"Destination node {item} is not on the {self.rg.name} road graph")
        if not nodes:
            raise ValueError("No destinations given")
        return list(dict.fromkeys(nodes))

    def _on_task(self, event: Event) -> list[Event]:
        if event.payload.get("assignee") != self.name:
            return []  # addressed to another planner
        now = self.clock()
        task_id = str(event.payload.get("task_id") or event.id)
        try:
            task = EvacuationTask.model_validate(event.payload)
            plan = self.plan_evacuation(task.origin, task.destinations, k=task.k, now=now)
        except (ValidationError, ValueError) as exc:
            plan = self._failed_plan("invalid_task", str(exc), now)
        except Exception as exc:  # a bug must not wedge the bus; the event is still audited
            log.exception("A4 failed planning task %s", task_id)
            plan = self._failed_plan("error", f"{type(exc).__name__}: {exc}", now)
        payload = CandidatePlanPayload(
            task_id=task_id,
            task="evacuation",
            agent=self.name,
            status=plan.status,
            plan=plan.model_dump(mode="json"),
        )
        return [self._event(event, EventType.CANDIDATE_PLAN, payload.model_dump(mode="json"), now)]

    def _failed_plan(self, status: str, reason: str, now: datetime) -> RoutePlan:
        return RoutePlan(
            status=status,
            reason=reason,
            site=self.rg.name,
            snapshot_version=self.cop.version,
            planned_at=now,
            assumptions=list(ASSUMPTIONS),
        )
