"""Build the world graph from the running system.  (Owner: Sumudu)

    world = WorldProjector.from_system(system).world()      # cached; rebuilt when the system changed
    world.impact("road:12-34")
    world.neighbourhood("incident:ratnapura-demo-river")

The graph is a *projection*. Nothing here is stored as truth: every entity and fact comes from a
source that already exists, and each carries where it came from.

    road graph (data/osm)               roads, junctions, road times, how the network connects
    reference data                      shelters, the fleet, areas and their populations, gauge stations
    COP snapshot                        road beliefs and closures, occupancy, availability, area risk
    A3 incidents / A8 missions          incidents, missions, evacuee groups, the roads each one uses
    ledger                              which sensor node reports for which area
    A4's routing tools                  ``reachable_from`` (derived; never a second routing engine)

Two assumptions are stated on the facts they produce rather than hidden:

* an area owns a road, shelter or gauge if its centre is the nearest and within
  ``max_area_distance_m`` (areas are points, not polygons): ``located_in``, confidence ``area_confidence``;
* ``reachable_from`` is A4's own safe-route answer at this COP version.

``build_world`` is a pure function of ``WorldSources`` so it can be tested with hand-made data;
``WorldProjector`` gathers the sources from a live system.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from atlas.knowledge.graph import ProjectedWorldGraph
from atlas.knowledge.ontology import Claim, OntologyDefinition, YamlOntology
from atlas.tools import geo
from atlas.tools.cognitive_map.cop_bridge import RoutingConfig, load_map
from atlas.tools.cognitive_map.routing import alternatives

REFERENCE_DIR = geo.ROOT / "data" / "reference"
TERMINAL_MISSIONS = {
    "completed",
    "failed",
    "cancelled",
}  # no longer depend on their roads or shelter
UNIT_OVERRIDES = {"water_level": "cm"}  # the Sentinel normalises to cm; the ontology also allows m
COP_AREA_ATTRIBUTES = {"water_level", "rainfall", "soil_moisture", "temperature", "humidity"}


@dataclass
class WorldConfig:
    max_area_distance_m: float = 2500.0  # an area owns things this close to its centre, at most
    area_confidence: float = 0.7  # how much we trust "nearest area centre" as containment
    serve_minutes: float = 30.0  # a shelter serves areas it can safely be reached from within this
    snap_metres: float = 15.0  # A8 routes are coordinates; snap to a junction within this


@dataclass
class AreaInfo:
    area_id: str
    name: str
    lat: float
    lon: float
    population: int | None = None
    vulnerable_fraction: float | None = None
    groups: list[str] = field(default_factory=list)
    gauge_station: str | None = None
    note: str = ""
    sources: list[str] = field(default_factory=list)


@dataclass
class WorldSources:
    road_graph: geo.RoadGraph | None = None
    shelters: list[Any] = field(default_factory=list)  # atlas.tools.reference.Shelter
    resources: list[Any] = field(default_factory=list)  # atlas.tools.reference.Resource
    areas: dict[str, AreaInfo] = field(default_factory=dict)
    snapshot: dict = field(default_factory=lambda: {"version": 0, "beliefs": []})
    incidents: list[dict] = field(default_factory=list)  # A3 incident states
    missions: list[Any] = field(default_factory=list)  # A8 Mission models
    sensor_nodes: dict[str, dict] = field(
        default_factory=dict
    )  # node -> {area_id, first_seen, evidence}
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ------------------------------------------------------------------------------------ helpers


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def load_areas(sensor_map=None) -> dict[str, AreaInfo]:
    """Areas from the reference files. Later sources only fill what earlier ones left empty."""
    from atlas.datasets.areas import AREAS
    from atlas.tools.sensor_map import SensorMap

    areas: dict[str, AreaInfo] = {}
    path = REFERENCE_DIR / "vulnerable_populations.json"
    for row in json.loads(path.read_text(encoding="utf-8")) if path.exists() else []:
        areas[row["area_id"]] = AreaInfo(
            row["area_id"],
            row.get("name") or row["area_id"],
            row["lat"],
            row["lon"],
            row.get("population"),
            row.get("vulnerable_fraction"),
            list(row.get("groups", [])),
            sources=["data/reference/vulnerable_populations.json"],
        )
    for area_id, source in AREAS.items():
        info = areas.setdefault(
            area_id, AreaInfo(area_id, area_id, source.lat, source.lon, sources=[])
        )
        info.gauge_station = info.gauge_station or source.gauge_station
        info.note = info.note or source.note
        info.sources.append("atlas/datasets/areas.py")
    for area_id in (sensor_map or SensorMap.from_file()).areas:
        if area_id in areas:
            areas[area_id].sources.append("data/reference/sensor_map.yaml")
    return areas


def _unit(definition: OntologyDefinition, attribute: str) -> str | None:
    if attribute in UNIT_OVERRIDES:
        return UNIT_OVERRIDES[attribute]
    units = definition.attributes[attribute].get("units", [])
    return units[0] if units else None


# --------------------------------------------------------------------------------------- build


def build_world(
    sources: WorldSources, definition: OntologyDefinition, config: WorldConfig | None = None
) -> ProjectedWorldGraph:
    return _Builder(sources, definition, config or WorldConfig()).build()


class _Builder:
    def __init__(self, sources: WorldSources, definition: OntologyDefinition, config: WorldConfig):
        self.s, self.d, self.cfg = sources, definition, config
        self.g = ProjectedWorldGraph(definition)
        self.skipped: dict[str, int] = {}

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def build(self) -> ProjectedWorldGraph:
        self.hazards()
        self.areas()
        self.network()
        self.shelters_and_fleet()
        self.gauges()
        self.cop()
        self.routing_access()
        self.response()
        self.g.report = self.make_report()
        return self.g

    # ------------------------------------------------------------------------- vocabulary

    def hazards(self) -> None:
        for hazard in self.d.hazards:
            self.g.add_entity(
                f"hazard:{hazard}",
                "Hazard",
                label=hazard,
                status=self.d.hazard_status[hazard]["status"],
            )

    # -------------------------------------------------------------------------------- areas

    def areas(self) -> None:
        rg = self.s.road_graph
        for info in self.s.areas.values():
            entity = f"area:{info.area_id}"
            anchor = None
            if rg is not None:
                node = rg.nearest_node(info.lat, info.lon)
                lat, lon = rg.pos(node)
                if geo.haversine_m(info.lat, info.lon, lat, lon) <= self.cfg.max_area_distance_m:
                    anchor = f"junction:{node}"
            self.g.add_entity(
                entity,
                "Area",
                label=info.name,
                lat=info.lat,
                lon=info.lon,
                groups=info.groups,
                gauge_station=info.gauge_station,
                anchor_junction=anchor,
                note=info.note,
            )
            for name, value in (
                ("population", info.population),
                ("vulnerable_fraction", info.vulnerable_fraction),
            ):
                if value is not None:
                    self.g.set_attribute(
                        entity,
                        name,
                        value,
                        unit=_unit(self.d, name),
                        provenance=info.sources,
                        source="reference",
                    )

    def nearest_area(self, lat: float, lon: float) -> tuple[str, float] | None:
        best = None
        for info in self.s.areas.values():
            distance = float(geo.haversine_m(lat, lon, info.lat, info.lon))
            if distance <= self.cfg.max_area_distance_m and (best is None or distance < best[1]):
                best = (f"area:{info.area_id}", distance)
        return best

    def place_in_area(self, entity: str, lat: float, lon: float, basis: str) -> None:
        found = self.nearest_area(lat, lon)
        if found is None:
            return self.skip("no_area_near")
        area, distance = found
        self.g.add_fact(
            entity,
            "located_in",
            area,
            confidence=self.cfg.area_confidence,
            source="derived",
            provenance=[basis],
            basis="nearest area centre",
            distance_m=round(distance),
        )

    # -------------------------------------------------------------------------- the network

    def network(self) -> None:
        rg = self.s.road_graph
        if rg is None:
            return
        basis = f"data/osm/{rg.name}.json"
        for node, data in rg.graph.nodes(data=True):
            entity = f"junction:{node}"
            self.g.add_entity(
                entity, "Junction", label=f"junction {node}", lat=data["lat"], lon=data["lon"]
            )
            self.g.set_attribute(
                entity,
                "elevation",
                data["elevation"],
                unit="m",
                provenance=[basis],
                source=rg.elevation_source,
            )
        for u, v, data in rg.graph.edges(data=True):
            road = f"road:{geo.edge_id(u, v)}"
            self.g.add_entity(road, "Road", label=f"road {u}-{v}", highway=data.get("highway", ""))
            self.g.set_attribute(
                road, "minutes", data["travel_time"], unit="min", provenance=[basis], source="osm"
            )
            self.g.add_fact(
                road, "from_junction", f"junction:{u}", source="osm", provenance=[basis]
            )
            self.g.add_fact(road, "to_junction", f"junction:{v}", source="osm", provenance=[basis])
            (lat1, lon1), (lat2, lon2) = rg.pos(u), rg.pos(v)
            self.place_in_area(road, (lat1 + lat2) / 2, (lon1 + lon2) / 2, basis)

    # ------------------------------------------------------------------- shelters and fleet

    def shelters_and_fleet(self) -> None:
        for shelter in self.s.shelters:
            entity = f"shelter:{shelter.id}"
            self.g.add_entity(
                entity,
                "Shelter",
                label=shelter.id,
                lat=shelter.lat,
                lon=shelter.lon,
                synthetic=getattr(shelter, "synthetic", False),
            )
            ref = ["data/reference/shelters.json"]
            self.g.set_attribute(
                entity,
                "capacity",
                shelter.capacity,
                unit="people",
                provenance=ref,
                source="reference",
            )
            self.g.set_attribute(
                entity,
                "elevation",
                shelter.elevation_m,
                unit="m",
                provenance=ref,
                source="reference",
            )
            self.locate(entity, shelter.node, shelter.lat, shelter.lon, ref[0])
        for unit in self.s.resources:
            entity = f"resource:{unit.id}"
            self.g.add_entity(
                entity,
                "Resource",
                label=unit.id,
                kind=unit.kind,
                synthetic=getattr(unit, "synthetic", False),
            )
            self.g.set_attribute(
                entity,
                "capacity",
                unit.capacity,
                unit="people",
                provenance=["data/reference/resources.json"],
                source="reference",
            )
            self.locate(entity, unit.node, None, None, "data/reference/resources.json")

    def locate(
        self, entity: str, node: int, lat: float | None, lon: float | None, basis: str
    ) -> None:
        junction = f"junction:{node}"
        if not self.g.has_entity(junction):
            return self.skip("location_not_on_road_graph")
        self.g.add_fact(entity, "located_at", junction, source="reference", provenance=[basis])
        if lat is None and self.s.road_graph is not None:
            lat, lon = self.s.road_graph.pos(node)
        if lat is not None and self.g.entity_class(entity) != "Resource":
            self.place_in_area(entity, lat, lon, basis)

    # ------------------------------------------------------------------------------ gauges

    def gauges(self) -> None:
        for node, seen in sorted(self.s.sensor_nodes.items()):
            area = f"area:{seen['area_id']}"
            if not self.g.has_entity(area):
                self.skip("gauge_reports_for_unknown_area")
                continue
            entity = f"gauge:{node}"
            self.g.add_entity(entity, "Gauge", label=node, kind="sensor_node")
            evidence = [f"event:{e}" for e in seen.get("events", [])][:5]
            for predicate in ("monitors", "located_in"):
                self.g.add_fact(
                    entity,
                    predicate,
                    area,
                    valid_from=seen.get("first_seen"),
                    source="ledger",
                    provenance=evidence,
                )
        for info in self.s.areas.values():
            if not info.gauge_station:
                continue
            entity = f"gauge:{slug(info.gauge_station)}"
            self.g.add_entity(entity, "Gauge", label=info.gauge_station, kind="irrigation_station")
            self.g.add_fact(
                entity,
                "monitors",
                f"area:{info.area_id}",
                source="reference",
                provenance=["atlas/datasets/areas.py"],
            )

    # ------------------------------------------------------------------------------- the COP

    def cop(self) -> None:
        version = self.s.snapshot.get("version", 0)
        for belief in self.s.snapshot.get("beliefs", []):
            subject, attribute = belief["subject"], belief["attribute"]
            klass, _, key = subject.partition(":")
            entity = self.d.normalise_id(subject)
            if not self.g.has_entity(entity):
                self.skip(f"cop_subject_not_in_world:{klass}")
                continue
            ref = [*belief.get("provenance", []), f"cop:{subject}/{attribute}"]
            common = dict(
                confidence=belief.get("confidence", 1.0),
                valid_from=_when(belief.get("updated_at")),
                provenance=ref,
                source="cop",
            )
            value = belief["value"]
            if klass == "edge" and attribute == "belief_open":
                self.g.set_attribute(entity, "belief_open", value, **common)
            elif klass == "edge" and attribute == "open":
                if value is False:
                    self.g.set_attribute(entity, "closed", True, **common)
            elif klass == "shelter" and attribute == "occupancy":
                self.g.set_attribute(entity, "occupancy", value, unit="people", **common)
            elif klass == "resource" and attribute == "available":
                self.g.set_attribute(entity, "available", value, **common)
            elif klass == "area" and attribute == "risk_level":
                self.g.set_attribute(entity, "risk_level", value, **common)
            elif klass == "area" and attribute == "hazard":
                if self.g.has_entity(f"hazard:{value}"):
                    self.g.add_fact(
                        f"hazard:{value}",
                        "threatens",
                        entity,
                        confidence=common["confidence"],
                        valid_from=common["valid_from"],
                        provenance=ref,
                        source="cop",
                    )
                else:
                    self.skip("cop_hazard_not_in_vocabulary")
            elif klass == "area" and attribute in COP_AREA_ATTRIBUTES:
                self.g.set_attribute(
                    entity, attribute, value, unit=_unit(self.d, attribute), **common
                )
            else:
                self.skip(f"cop_attribute_not_modelled:{klass}/{attribute}")
        self.threatened_roads(version)

    def threatened_roads(self, version: int) -> None:
        """A road believed likely closed inside an area with a hazard is threatened by that hazard."""
        for hazard in self.d.hazards:
            for fact in self.g.facts(f"hazard:{hazard}", "threatens"):
                if self.g.entity_class(fact.object) != "Area":
                    continue
                for road_fact in self.g.facts(fact.object, "located_in", direction="in"):
                    road = road_fact.subject
                    belief = self.g.attribute(road, "belief_open")
                    if self.g.entity_class(road) == "Road" and belief and belief.value < 0.3:
                        self.g.add_fact(
                            f"hazard:{hazard}",
                            "threatens",
                            road,
                            confidence=round(1.0 - belief.value, 3),
                            valid_from=belief.valid_from,
                            source="derived",
                            provenance=[*belief.provenance, f"cop-version:{version}"],
                        )

    # ------------------------------------------------------------------------------ routing

    def routing_access(self) -> None:
        """`reachable_from` and `serves`, from A4's own safe-route search over the COP beliefs."""
        rg = self.s.road_graph
        if rg is None:
            return
        cfg = RoutingConfig()
        cmap, _ = load_map(rg.graph, rg.edge_index(), self.s.snapshot, self.s.now, cfg)
        version = self.s.snapshot.get("version", 0)
        provenance = [f"a4-routing@cop-v{version}"]
        for area_entity in self.g.entities("Area"):
            anchor = self.g.meta(area_entity).get("anchor_junction")
            if not anchor:
                continue
            origin = int(anchor.split(":", 1)[1])
            for shelter in self.s.shelters:
                if shelter.node not in rg.graph:
                    continue
                safe = alternatives(
                    cmap, origin, [shelter.node], 1, avoid_below=cfg.close_threshold
                )
                found = safe or alternatives(cmap, origin, [shelter.node], 1)
                if not found:
                    continue
                route, is_safe = found[0], bool(safe)
                entity = f"shelter:{shelter.id}"
                self.g.add_fact(
                    entity,
                    "reachable_from",
                    area_entity,
                    confidence=round(route.weakest_belief or 0.0, 3),
                    provenance=provenance,
                    source="derived",
                    minutes=round(route.minutes, 2),
                    safe=is_safe,
                )
                if is_safe and route.minutes <= self.cfg.serve_minutes:
                    self.g.add_fact(
                        entity,
                        "serves",
                        area_entity,
                        confidence=round(route.weakest_belief or 0.0, 3),
                        provenance=provenance,
                        source="derived",
                    )

    # ------------------------------------------------------------------ incidents, missions

    def response(self) -> None:
        by_id = {getattr(m, "id", None): m for m in self.s.missions}
        known_missions: set[str] = set()
        for state in self.s.incidents:
            incident = self.incident(state)
            if incident is None:
                continue
            key = incident.split(":", 1)[1]
            for group_id, mission in sorted((state.get("missions") or {}).items()):
                mission_id = mission.get("mission_id")
                if not mission_id:
                    self.skip("mission_without_id")
                    continue
                known_missions.add(mission_id)
                record = by_id.get(mission_id)
                status = record.status.value if record else mission.get("status")
                self.mission(incident, key, group_id, mission_id, status, mission)
        for (
            record
        ) in self.s.missions:  # missions A8 holds that no incident state lists (API-created)
            if record.id in known_missions:
                continue
            self.orphan_mission(record)

    def incident(self, state: dict) -> str | None:
        raw = state.get("incident_id") or ""
        entity = raw if raw.startswith("incident:") else f"incident:{raw}"
        if not raw:
            self.skip("incident_without_id")
            return None
        hazard = state.get("hazard")
        self.g.add_entity(
            entity,
            "Incident",
            label=f"{hazard or 'incident'} at {state.get('area_name') or state.get('area_id')}",
            state=state.get("state"),
            cycle=state.get("cycle"),
            opened_at=state.get("opened_at"),
            needs_human=state.get("needs_human"),
        )
        evidence = [f"incident:{entity.split(':', 1)[1]}"]
        opened = _when(state.get("opened_at"))
        if hazard and self.g.has_entity(f"hazard:{hazard}"):
            self.g.add_fact(
                entity,
                "about",
                f"hazard:{hazard}",
                valid_from=opened,
                source="a3",
                provenance=evidence,
            )
        area = f"area:{state.get('area_id')}"
        if self.g.has_entity(area):
            self.g.add_fact(
                area, "part_of", entity, valid_from=opened, source="a3", provenance=evidence
            )
        else:
            self.skip("incident_area_unknown")
        return entity

    def roads_of(self, route: list[int]) -> Iterator[str]:
        for a, b in zip(route[:-1], route[1:]):
            road = f"road:{a}-{b}"
            if self.g.has_entity(road):
                yield road
            else:
                self.skip("route_hop_not_a_road")

    def mission(self, incident, key, group_id, mission_id, status, info) -> None:
        entity = f"mission:{mission_id}"
        provenance = [f"mission:{mission_id}", incident]
        self.g.add_entity(entity, "Mission", label=info.get("title") or mission_id)
        if status in self.d.attributes["status"]["values"]:
            self.g.set_attribute(entity, "status", status, provenance=provenance, source="a8")
        self.g.add_fact(entity, "part_of", incident, source="a3", provenance=provenance)

        # A3 keys its missions by group id, but a standby crew and a supply run are keyed
        # "rescue:<area>" and "resupply:<shelter>": they carry no evacuee group, so make none.
        group = None if ":" in group_id else f"group:{key}/{group_id}"
        people = info.get("people")
        has_people = isinstance(people, int) and not isinstance(people, bool)
        if has_people:
            self.g.set_attribute(
                entity, "people", people, unit="people", provenance=provenance, source="a3"
            )
        if group:
            self.g.add_entity(group, "Group", label=f"group {group_id} ({key})")
            if has_people:
                self.g.set_attribute(
                    group, "people", people, unit="people", provenance=provenance, source="a3"
                )
            self.g.add_fact(group, "part_of", incident, source="a3", provenance=provenance)
            self.g.add_fact(entity, "evacuates", group, source="a3", provenance=provenance)

        for unit in info.get("units") or []:
            resource = f"resource:{unit}"
            if self.g.has_entity(resource):
                if status not in TERMINAL_MISSIONS:
                    self.g.add_fact(
                        resource, "assigned_to", entity, source="a3", provenance=provenance
                    )
            else:
                self.skip("assigned_unit_unknown")
        if status in TERMINAL_MISSIONS:
            return  # finished missions no longer depend on their road or shelter
        shelter = f"shelter:{info['shelter_id']}" if info.get("shelter_id") else None
        if shelter:
            if self.g.has_entity(shelter):
                self.g.add_fact(entity, "destined_for", shelter, source="a3", provenance=provenance)
                if group:
                    self.g.add_fact(group, "placed_at", shelter, source="a3", provenance=provenance)
            else:
                self.skip("mission_shelter_unknown")
        for road in self.roads_of([n for n in info.get("route") or [] if isinstance(n, int)]):
            self.g.add_fact(entity, "uses", road, source="a3", provenance=provenance)
            if group:
                self.g.add_fact(group, "routed_over", road, source="a3", provenance=provenance)

    def orphan_mission(self, record) -> None:
        entity = f"mission:{record.id}"
        provenance = [f"mission:{record.id}"]
        self.g.add_entity(entity, "Mission", label=record.title)
        self.g.set_attribute(
            entity, "status", record.status.value, provenance=provenance, source="a8"
        )
        incident = (
            record.incident_id
            if record.incident_id.startswith("incident:")
            else f"incident:{record.incident_id}"
        )
        if self.g.has_entity(incident):
            self.g.add_fact(entity, "part_of", incident, source="a8", provenance=provenance)
        for unit in filter(None, (record.team or "").split(",")):
            resource = f"resource:{unit.strip()}"
            if self.g.has_entity(resource) and record.status.value not in TERMINAL_MISSIONS:
                self.g.add_fact(resource, "assigned_to", entity, source="a8", provenance=provenance)
        if record.status.value in TERMINAL_MISSIONS:
            return
        shelter = f"shelter:{record.destination}" if record.destination else None
        if shelter and self.g.has_entity(shelter):
            self.g.add_fact(entity, "destined_for", shelter, source="a8", provenance=provenance)
        rg = self.s.road_graph
        if rg is None:
            return
        nodes: list[int] = []
        for point in record.route:
            if isinstance(point, (list, tuple)) and len(point) == 2:
                node = rg.nearest_node(point[0], point[1])
                lat, lon = rg.pos(node)
                if geo.haversine_m(point[0], point[1], lat, lon) <= self.cfg.snap_metres:
                    nodes.append(node)
                else:
                    self.skip("route_point_not_on_a_junction")
        for road in self.roads_of(nodes):
            self.g.add_fact(entity, "uses", road, source="a8", provenance=provenance)

    # -------------------------------------------------------------------------------- report

    def make_report(self) -> dict[str, Any]:
        classes: dict[str, int] = {}
        for entity in self.g.entities():
            klass = self.g.entity_class(entity) or "?"
            classes[klass] = classes.get(klass, 0) + 1
        predicates: dict[str, int] = {}
        for _, _, data in self.g.graph.edges(data=True):
            predicates[data["predicate"]] = predicates.get(data["predicate"], 0) + 1
        return {
            "entities": dict(sorted(classes.items())),
            "facts": dict(sorted(predicates.items())),
            "skipped": dict(sorted(self.skipped.items())),
            "cop_version": self.s.snapshot.get("version", 0),
            "built_at": self.s.now.isoformat(),
        }


# ---------------------------------------------------------------------------- the live system


class WorldProjector:
    """Builds the world graph from a running system, and rebuilds it only when something changed."""

    def __init__(
        self,
        runtime,
        *,
        road_graph: geo.RoadGraph | None = None,
        shelters=(),
        resources=(),
        commander=None,
        dispatch=None,
        sensor_map=None,
        definition: OntologyDefinition | None = None,
        config: WorldConfig | None = None,
    ) -> None:
        self.runtime = runtime
        self.road_graph, self.shelters, self.resources = road_graph, list(shelters), list(resources)
        self.commander, self.dispatch, self.sensor_map = commander, dispatch, sensor_map
        self.definition = definition or YamlOntology.read()
        self.config = config or WorldConfig()
        self._areas = load_areas(sensor_map)
        self._cached: tuple[Any, ProjectedWorldGraph] | None = None

    @classmethod
    def from_system(cls, system, **options) -> WorldProjector:
        return cls(
            system.runtime,
            road_graph=system.routing.road_graph,
            shelters=system.routing.shelter.shelters.values(),
            resources=system.routing.logistics.units.values(),
            commander=system.orchestration.commander,
            dispatch=system.control.dispatch,
            **options,
        )

    # -------------------------------------------------------------------------------- sources

    def sources(self) -> WorldSources:
        rt = self.runtime
        now = rt.clock()
        return WorldSources(
            road_graph=self.road_graph,
            shelters=self.shelters,
            resources=self.resources,
            areas=self._areas,
            snapshot=rt.cop.snapshot(now=now),
            incidents=self.commander.incidents() if self.commander else [],
            missions=self.dispatch.list() if self.dispatch else [],
            sensor_nodes=self.sensor_nodes(),
            now=now,
        )

    def sensor_nodes(self) -> dict[str, dict]:
        """Which sensor node reports for which area, from validated observations in the ledger."""
        found: dict[str, dict] = {}
        for record in self.runtime.ledger.records("event"):
            event = record["payload"]
            if event.get("type") != "observation_validated":
                continue
            payload = event.get("payload", {})
            source = payload.get("source", "")
            if not source.startswith("sensor:") or not payload.get("area_id"):
                continue
            node = source.split(":", 1)[1]
            entry = found.setdefault(
                node,
                {
                    "area_id": payload["area_id"],
                    "first_seen": _when(event.get("created_at")),
                    "events": [],
                },
            )
            entry["events"].append(event["id"])
        return found

    def fingerprint(self, sources: WorldSources) -> str:
        digest = hashlib.sha256()
        digest.update(str(sources.snapshot.get("version")).encode())
        digest.update(json.dumps(sources.incidents, sort_keys=True, default=str).encode())
        digest.update(
            repr(
                [(m.id, m.status.value, m.updated_at.isoformat()) for m in sources.missions]
            ).encode()
        )
        row = self.runtime.db.connection.execute("SELECT MAX(seq) FROM ledger").fetchone()
        digest.update(str(row[0]).encode())
        return digest.hexdigest()

    def world(self, *, force: bool = False) -> ProjectedWorldGraph:
        sources = self.sources()
        key = self.fingerprint(sources)
        if not force and self._cached and self._cached[0] == key:
            return self._cached[1]
        graph = build_world(sources, self.definition, self.config)
        self._cached = (key, graph)
        return graph


# ------------------------------------------------------------------------------- self-checking


def graph_claims(graph: ProjectedWorldGraph, entity_id: str | None = None) -> Iterator[Claim]:
    """Every fact in the graph as a `Claim`, so the graph can be checked against its own ontology.

    A projection that the validator rejects is a bug in the projector, and this is how tests find it.
    Evidence is the fact's own provenance (or its source, if it has none).
    """
    entities = [entity_id] if entity_id else graph.entities()
    for entity in entities:
        for name, value in sorted(graph.attributes(entity).items()):
            yield Claim(
                subject=entity,
                predicate=name,
                object=value.value,
                unit=value.unit,
                evidence_ids=list(value.provenance) or [value.source or "graph"],
                confidence=value.confidence,
            )
        for fact in graph.facts(entity):
            yield Claim(
                subject=fact.subject,
                predicate=fact.predicate,
                object=fact.object,
                evidence_ids=list(fact.provenance) or [fact.source or "graph"],
                confidence=fact.confidence,
            )


__all__ = [
    "AreaInfo",
    "WorldConfig",
    "WorldProjector",
    "WorldSources",
    "build_world",
    "graph_claims",
    "load_areas",
    "slug",
]
