"""The inventory: every agent, its version, status and environments.  (Owner: Dinuka)

A central list of what is running, so nothing is an unaccounted-for "shadow agent", with lifecycle
controls. An agent is either **internal** (runs in this process on the bus) or **external** (a sensor
node or radio that reports in). Environments say where work happens: ``live`` (real sensors and the
operator), ``replay`` (a recorded disaster played into live) and ``sandbox`` (throwaway systems for
what-if and the red team, never part of the live ledger).

Suspending an agent is real: the bus stops delivering to it (or the Gateway stops calling an executor)
and everything it misses waits, in order, until it is resumed.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field

from atlas.dashboard.dev.catalog import cards
from atlas.manager import VERSION

ENVIRONMENTS = [
    {"name": "live", "description": "Real sensors, LoRa and feeds; a human decides escalations."},
    {
        "name": "replay",
        "description": "A recorded disaster played into the live system on a timer.",
    },
    {
        "name": "sandbox",
        "description": "Throwaway systems (what-if, red team); never touch the live ledger.",
    },
]
EXTERNAL = {"sensors", "feeds"}  # report in, are not run by us
UNSUSPENDABLE = {"human", "sensors", "feeds"}


class AgentRecord(BaseModel):
    name: str
    title: str
    layer: str
    purpose: str
    kind: str  # internal | external
    version: str
    owner: str
    status: str  # active | suspended
    suspended_by: str | None = None
    suspended_reason: str | None = None
    environments: list[str] = Field(default_factory=lambda: ["live", "replay", "sandbox"])
    uses_llm: bool = False
    subscribed: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    received: int = 0
    produced: int = 0
    last_received: str | None = None
    last_produced: str | None = None
    pending: int = 0  # work waiting because it is suspended


class NotFound(LookupError):
    pass


class Registry:
    def __init__(self, system, *, site: str | None = None):
        self.system = system
        self.runtime = system.runtime
        self.site = site or os.getenv("ATLAS_SITE", "ratnapura")

    def environments(self) -> list[dict]:
        return ENVIRONMENTS

    def _suspended(self) -> dict[str, dict]:
        rows = self.runtime.db.connection.execute("SELECT * FROM agent_state WHERE suspended=1")
        return {r["name"]: dict(r) for r in rows}

    def _pending(self, name: str) -> int:
        """Work an agent would have done but has not (only meaningful while suspended)."""
        handler = self.runtime.bus.handlers.get(name)
        if handler is None:  # an executor: authorised actions the Gateway is holding for it
            return self.runtime.db.connection.execute(
                "SELECT COUNT(*) FROM gateway_actions WHERE status IN ('authorised','executing') "
                "AND json_extract(action,'$.executor')=?",
                (name,),
            ).fetchone()[0]
        types = handler[0]
        marks = ",".join("?" * len(types))
        return self.runtime.db.connection.execute(
            "SELECT COUNT(*) FROM ledger e WHERE e.kind='event' AND "
            f"json_extract(e.body,'$.type') IN ({marks}) AND NOT EXISTS (SELECT 1 FROM deliveries d "
            "WHERE d.event_id=substr(e.record_id,7) AND d.subscriber=?)",
            [t.value for t in types] + [name],
        ).fetchone()[0]

    def agents(self) -> list[AgentRecord]:
        suspended = self._suspended()
        out = []
        for card in cards(self.runtime):
            state = suspended.get(card.name)
            out.append(
                AgentRecord(
                    name=card.name,
                    title=card.title,
                    layer=card.layer,
                    purpose=card.purpose,
                    kind="external" if card.name in EXTERNAL else "internal",
                    version=VERSION,
                    owner=card.owner,
                    status="suspended" if state else "active",
                    suspended_by=state["changed_by"] if state else None,
                    suspended_reason=state["reason"] if state else None,
                    uses_llm=card.uses_llm,
                    subscribed=card.subscribed,
                    reads=card.reads,
                    writes=card.writes,
                    tools=card.deterministic_tools,
                    received=card.received,
                    produced=card.produced,
                    last_received=card.last_received,
                    last_produced=card.last_produced,
                    pending=self._pending(card.name) if state else 0,
                )
            )
        return out

    def agent(self, name: str) -> AgentRecord:
        for a in self.agents():
            if a.name == name:
                return a
        raise NotFound(f"unknown agent {name!r}")

    def suspend(self, name: str, *, by: str, reason: str) -> AgentRecord:
        agent = self.agent(name)
        if name in UNSUSPENDABLE or agent.kind == "external":
            raise ValueError(f"{name} is not run by ATLAS, so it cannot be suspended here")
        self.runtime.bus.suspend(name, by=by, reason=reason)
        return self.agent(name)

    async def resume(self, name: str, *, by: str) -> AgentRecord:
        if self.agent(name).status != "suspended":
            raise ValueError(f"{name} is not suspended")
        await self.runtime.bus.resume(name, by=by)
        await self.system.control.tick()  # run anything the Gateway was holding for an executor
        return self.agent(name)

    def summary(self) -> dict[str, Any]:
        agents = self.agents()
        return {
            "agents": len(agents),
            "suspended": sum(a.status == "suspended" for a in agents),
            "external": sum(a.kind == "external" for a in agents),
            "site": self.site,
        }
