"""Assemble the planning agents (A4 Evacuation, A6 Logistics, A7 Shelter) onto a Runtime.

    routing = attach_routing(runtime, site="ratnapura")     # before ``await runtime.start()``

Mirrors `atlas.control.wiring.attach_control`. Subscribers must be registered before start, because
startup recovery redelivers pending events (including earlier observations) to whoever is registered.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.agents.a4_evacuation import Evacuation, EvacuationConfig
from atlas.agents.a6_logistics import Logistics, LogisticsConfig
from atlas.agents.a7_shelter import ShelterConfig, ShelterRelief
from atlas.tools import geo
from atlas.tools.reference import load_resources, load_shelters
from atlas.tools.sensor_map import SensorMap


@dataclass
class RoutingPlane:
    runtime: object
    road_graph: geo.RoadGraph
    evacuation: Evacuation
    shelter: ShelterRelief
    logistics: Logistics


def attach_routing(
    runtime,
    *,
    site: str = "ratnapura",
    sensor_map: SensorMap | None = None,
    config: EvacuationConfig | None = None,
    shelter_config: ShelterConfig | None = None,
    logistics_config: LogisticsConfig | None = None,
) -> RoutingPlane:
    road_graph = geo.load_site(site)  # cache only: no network at start-up
    shelters = load_shelters(site)
    evacuation = Evacuation(
        runtime.bus,
        runtime.cop,
        runtime.ledger,
        road_graph=road_graph,
        sensor_map=sensor_map or SensorMap.from_file(),
        shelters=shelters,
        config=config,
        clock=runtime.clock,
    )
    shelter = ShelterRelief(
        runtime.bus,
        runtime.cop,
        runtime.ledger,
        road_graph=road_graph,
        shelters=shelters,
        config=shelter_config,
        clock=runtime.clock,
    )
    logistics = Logistics(
        runtime.bus,
        runtime.cop,
        runtime.ledger,
        road_graph=road_graph,
        resources=load_resources(site),
        config=logistics_config,
        clock=runtime.clock,
    )
    for agent in (evacuation, shelter, logistics):
        runtime.register(agent)
    return RoutingPlane(runtime, road_graph, evacuation, shelter, logistics)
