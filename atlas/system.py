"""The whole ATLAS system on one Runtime: platform + sensing, routing, orchestration, control.

    system = attach_all(runtime)              # before ``await runtime.start()``
    system.control.start_worker()             # inside a running event loop

Subscribers must be registered before ``start()`` because startup recovery redelivers pending events.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.agents.wiring import RoutingPlane, attach_routing
from atlas.control.wiring import ControlPlane, attach_control
from atlas.orchestration.wiring import OrchestrationPlane, attach_orchestration


@dataclass
class System:
    runtime: object
    routing: RoutingPlane
    orchestration: OrchestrationPlane
    control: ControlPlane


def attach_all(
    runtime,
    *,
    site: str = "ratnapura",
    policy=None,
    channel=None,
    translator=None,
    commander_config=None,
) -> System:
    routing = attach_routing(runtime, site=site)
    orchestration = attach_orchestration(runtime, commander_config=commander_config)
    control = attach_control(runtime, policy=policy, channel=channel, translator=translator)
    return System(runtime, routing, orchestration, control)
