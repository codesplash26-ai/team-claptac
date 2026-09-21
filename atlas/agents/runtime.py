"""Wire Workstream B agents onto a shared bus for demos and integration tests."""
from __future__ import annotations

from dataclasses import dataclass

from atlas.agents.a2_hazard_intel import HazardIntelAgent
from atlas.agents.a3_incident_commander import IncidentCommander
from atlas.agents.a5_triage import TriageAgent
from atlas.agents.base import Agent
from atlas.control.action_gateway import ActionGateway
from atlas.core.cop import CommonOperatingPicture
from atlas.core.event_bus import EventBus
from atlas.core.events import Event
from atlas.memory.ledger import ActionLedger
from atlas.tools.llm import LLMClient


@dataclass
class OrchestrationRuntime:
    bus: EventBus
    cop: CommonOperatingPicture
    ledger: ActionLedger
    gateway: ActionGateway
    llm: LLMClient
    a2: HazardIntelAgent
    a3: IncidentCommander
    a5: TriageAgent

    async def publish(self, event: Event) -> list:
        return await self.bus.publish(event)


def _agent_handler(agent: Agent):
    async def _handle(event: Event) -> list:
        produced = await agent.handle(event)
        for child in produced:
            await agent.bus.publish(child)
        return produced

    return _handle


def build_runtime(
    *,
    llm: LLMClient | None = None,
    force_llm_fallback: bool = False,
) -> OrchestrationRuntime:
    bus = EventBus()
    cop = CommonOperatingPicture()
    ledger = ActionLedger()
    gateway = ActionGateway(ledger)
    llm = llm or LLMClient()

    a2 = HazardIntelAgent(bus, cop, ledger)
    a5 = TriageAgent(bus, cop, ledger)
    a3 = IncidentCommander(
        bus,
        cop,
        ledger,
        llm=llm,
        gateway=gateway,
        force_llm_fallback=force_llm_fallback,
    )

    for agent in (a2, a5, a3):
        bus.subscribe_many(agent.subscribes_to, _agent_handler(agent))

    return OrchestrationRuntime(
        bus=bus,
        cop=cop,
        ledger=ledger,
        gateway=gateway,
        llm=llm,
        a2=a2,
        a3=a3,
        a5=a5,
    )
