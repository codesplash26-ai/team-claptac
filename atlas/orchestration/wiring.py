"""Assemble Workstream B's agents (A2, A3, A5) onto a Runtime.

    orchestration = attach_orchestration(runtime)     # before ``await runtime.start()``

Mirrors ``attach_control`` and ``attach_routing``.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.agents.a2_hazard_intel import HazardIntel
from atlas.agents.a3_incident_commander import CommanderConfig, IncidentCommander
from atlas.agents.a5_triage import Triage


@dataclass
class OrchestrationPlane:
    runtime: object
    hazard: HazardIntel
    triage: Triage
    commander: IncidentCommander


def attach_orchestration(
    runtime, *, commander_config: CommanderConfig | None = None
) -> OrchestrationPlane:
    hazard = HazardIntel(
        runtime.bus,
        runtime.cop,
        runtime.ledger,
        config=runtime.settings.sentinel,
        clock=runtime.clock,
    )
    triage = Triage(runtime.bus, runtime.cop, runtime.ledger, clock=runtime.clock)
    commander = IncidentCommander(
        runtime.bus, runtime.cop, runtime.ledger, config=commander_config, clock=runtime.clock
    )
    for agent in (hazard, triage, commander):
        runtime.register(agent)
    return OrchestrationPlane(runtime, hazard, triage, commander)
