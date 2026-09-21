"""Agent 3 - Incident Commander & Planner (LLM orchestrator).

Only agent allowed to delegate. Wakes on a trigger, assesses, calls A4–A7 as
tools, reconciles candidate plans, submits to the Action Gateway, replans when
missions fail, and returns to standby.

When the LLM is unavailable, falls back to deterministic A4 routing so
evacuation planning still runs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from atlas.agents.a4_evacuation import plan_evacuation
from atlas.agents.base import Agent
from atlas.control.action_gateway import ActionGateway, DecisionStatus
from atlas.core.events import Event, EventType, ProposedAction
from atlas.tools.llm import LLMClient, LLMUnavailable, ToolSpec

log = logging.getLogger(__name__)


class CycleState(str, Enum):
    STANDBY = "standby"
    ASSESSING = "assessing"
    PLANNING = "planning"
    AWAITING_GATEWAY = "awaiting_gateway"
    ACTIVE = "active"
    REPLANNING = "replanning"


@dataclass
class IncidentCycle:
    incident_id: str
    state: CycleState = CycleState.STANDBY
    area_id: str | None = None
    hazard: str | None = None
    trigger_payload: dict[str, Any] = field(default_factory=dict)
    candidate_plans: list[dict[str, Any]] = field(default_factory=list)
    last_decision: dict[str, Any] | None = None
    used_llm_fallback: bool = False
    notes: list[str] = field(default_factory=list)


_SYSTEM = """You are ATLAS Incident Commander for Sri Lanka disaster response.
You orchestrate specialist tools only; you do not invent routes or scores.
Prefer the highest-triage area, request an evacuation plan, then propose a
dispatch mission and a public sitrep. Be concise. When tools return conflicts
(e.g. route to a full shelter), call tools again to reconcile before finishing.
"""


class IncidentCommander(Agent):
    name = "a3_incident_commander"
    subscribes_to = (
        EventType.TRIGGER,
        EventType.CANDIDATE_PLAN,
        EventType.MISSION_STATUS,
        EventType.ACTION_AUTHORISED,
        EventType.ACTION_BLOCKED,
        EventType.ACTION_ESCALATED,
        EventType.RISK_UPDATE,
    )

    def __init__(
        self,
        bus,
        cop,
        ledger,
        llm: LLMClient | None = None,
        gateway: ActionGateway | None = None,
        *,
        force_llm_fallback: bool = False,
    ):
        super().__init__(bus, cop, ledger)
        self.llm = llm or LLMClient()
        self.gateway = gateway or ActionGateway(ledger)
        self.force_llm_fallback = force_llm_fallback
        self.cycles: dict[str, IncidentCycle] = {}

    def get_cycle(self, incident_id: str) -> IncidentCycle | None:
        return self.cycles.get(incident_id)

    async def handle(self, event: Event) -> list[Event]:
        if event.type == EventType.TRIGGER:
            return await self._on_trigger(event)
        if event.type == EventType.CANDIDATE_PLAN:
            return self._on_candidate_plan(event)
        if event.type == EventType.MISSION_STATUS:
            return await self._on_mission_status(event)
        if event.type in (
            EventType.ACTION_AUTHORISED,
            EventType.ACTION_BLOCKED,
            EventType.ACTION_ESCALATED,
        ):
            return self._on_gateway_result(event)
        if event.type == EventType.RISK_UPDATE:
            return self._on_risk_update(event)
        return []

    async def _on_trigger(self, event: Event) -> list[Event]:
        incident_id = event.incident_id or f"inc-{uuid4().hex[:10]}"
        cycle = IncidentCycle(
            incident_id=incident_id,
            state=CycleState.ASSESSING,
            area_id=event.payload.get("area_id"),
            hazard=event.payload.get("hazard") or "flood",
            trigger_payload=dict(event.payload),
        )
        self.cycles[incident_id] = cycle
        self.cop.set_incident(
            incident_id,
            {
                "state": cycle.state.value,
                "area_id": cycle.area_id,
                "hazard": cycle.hazard,
            },
        )
        log.info("A3 wake incident=%s area=%s", incident_id, cycle.area_id)

        out: list[Event] = [
            Event(
                type=EventType.TASK_ASSIGNED,
                source=self.name,
                incident_id=incident_id,
                payload={"task": "triage", "area_id": cycle.area_id},
            ),
            Event(
                type=EventType.TASK_ASSIGNED,
                source=self.name,
                incident_id=incident_id,
                payload={
                    "task": "evacuate",
                    "origin": cycle.area_id or "unknown",
                    "destinations": ["shelter:primary"],
                },
            ),
        ]
        out.extend(await self._run_planning_cycle(cycle))
        return out

    async def _run_planning_cycle(self, cycle: IncidentCycle) -> list[Event]:
        cycle.state = CycleState.PLANNING
        self.cop.set_incident(cycle.incident_id, {"state": cycle.state.value})

        if self.force_llm_fallback or not self.llm.available:
            return self._deterministic_fallback(cycle)

        try:
            return await self._llm_orchestrate(cycle)
        except LLMUnavailable as exc:
            log.warning("LLM unavailable (%s); falling back to A4 routing", exc)
            cycle.notes.append(f"llm_fallback: {exc}")
            return self._deterministic_fallback(cycle)

    async def _llm_orchestrate(self, cycle: IncidentCycle) -> list[Event]:
        tools = self._tool_specs(cycle)
        user_msg = (
            f"Incident {cycle.incident_id} triggered in area={cycle.area_id} "
            f"hazard={cycle.hazard}. Trigger payload: {cycle.trigger_payload}. "
            "Assess, call tools, then propose concrete actions."
        )
        result = await self.llm.complete(
            system=_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
            tools=tools,
        )

        events: list[Event] = []
        # Prefer tool-produced plans already stored on the cycle.
        reconciled = self.reconcile_plans(cycle)
        if reconciled.get("actions"):
            events.extend(self._submit_actions(cycle, reconciled["actions"], rationale=result.text or ""))
        elif result.text:
            # Still propose a sitrep so the loop produces a gated action.
            events.extend(
                self._submit_actions(
                    cycle,
                    [
                        {
                            "executor": "a9_comms",
                            "action_type": "publish_sitrep",
                            "params": {
                                "incident_id": cycle.incident_id,
                                "text": result.text,
                                "area_id": cycle.area_id,
                            },
                        }
                    ],
                    rationale=result.text,
                )
            )
        cycle.state = CycleState.AWAITING_GATEWAY if events else CycleState.ACTIVE
        self.cop.set_incident(cycle.incident_id, {"state": cycle.state.value, "llm_text": result.text})
        return events

    def _deterministic_fallback(self, cycle: IncidentCycle) -> list[Event]:
        """Keep A4 routing running without the LLM."""
        cycle.used_llm_fallback = True
        origin = cycle.area_id or "unknown"
        route = plan_evacuation(origin, ["shelter:primary"])
        cycle.candidate_plans.append(
            {"plan_type": "evacuation", "source": "a4_fallback", **route.model_dump()}
        )
        triage = self.cop.get(f"area:{origin}", "risk_level")
        actions = [
            {
                "executor": "a8_dispatch",
                "action_type": "create_mission",
                "params": {
                    "mission_type": "evacuate",
                    "origin": origin,
                    "route": route.primary_route,
                    "eta_minutes": route.eta_minutes,
                    "risk_score": route.risk_score,
                },
            },
            {
                "executor": "a9_comms",
                "action_type": "publish_sitrep",
                "params": {
                    "incident_id": cycle.incident_id,
                    "area_id": origin,
                    "text": (
                        f"LLM fallback active. Evacuation route proposed for {origin}. "
                        f"Risk belief={getattr(triage, 'value', None)}."
                    ),
                },
            },
        ]
        events = self._submit_actions(
            cycle,
            actions,
            rationale="deterministic fallback: A4 plan_evacuation without LLM",
        )
        cycle.state = CycleState.AWAITING_GATEWAY
        self.cop.set_incident(
            cycle.incident_id,
            {"state": cycle.state.value, "used_llm_fallback": True},
        )
        return events

    def _tool_specs(self, cycle: IncidentCycle) -> list[ToolSpec]:
        async def tool_triage_summary() -> dict[str, Any]:
            ranked = [
                p for p in cycle.candidate_plans if p.get("plan_type") == "triage"
            ]
            if ranked:
                return ranked[-1]
            # Live recompute from COP risk beliefs if no triage plan yet
            risks = self.cop.query(attribute="risk_level")
            return {
                "plan_type": "triage",
                "from_cop": [
                    {"subject": b.subject, "level": b.value, "confidence": b.confidence}
                    for b in risks
                ],
            }

        async def tool_plan_evacuation(
            origin: str | None = None,
            destinations: list[str] | None = None,
        ) -> dict[str, Any]:
            origin = origin or cycle.area_id or "unknown"
            plan = plan_evacuation(origin, destinations or ["shelter:primary"])
            payload = {"plan_type": "evacuation", "source": "a4", **plan.model_dump()}
            cycle.candidate_plans.append(payload)
            return payload

        async def tool_propose_action(
            executor: str,
            action_type: str,
            params: dict[str, Any] | None = None,
            rationale: str = "",
        ) -> dict[str, Any]:
            actions = [
                {
                    "executor": executor,
                    "action_type": action_type,
                    "params": params or {},
                    "rationale": rationale,
                }
            ]
            events = self._submit_actions(cycle, actions, rationale=rationale)
            return {
                "submitted": len(events),
                "events": [e.type.value for e in events],
            }

        return [
            ToolSpec(
                name="get_triage_summary",
                description="Get current triage ranking / needs priorities.",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=tool_triage_summary,
            ),
            ToolSpec(
                name="plan_evacuation",
                description="Call A4 to plan evacuation routes from origin to destinations.",
                parameters={
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string"},
                        "destinations": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [],
                },
                handler=tool_plan_evacuation,
            ),
            ToolSpec(
                name="propose_action",
                description="Submit a ProposedAction to the Action Gateway.",
                parameters={
                    "type": "object",
                    "properties": {
                        "executor": {
                            "type": "string",
                            "enum": ["a8_dispatch", "a9_comms"],
                        },
                        "action_type": {"type": "string"},
                        "params": {"type": "object"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["executor", "action_type"],
                },
                handler=tool_propose_action,
            ),
        ]

    def reconcile_plans(self, cycle: IncidentCycle) -> dict[str, Any]:
        """Merge specialist candidate plans; flag conflicts such as full-shelter routes."""
        triage = next(
            (p for p in reversed(cycle.candidate_plans) if p.get("plan_type") == "triage"),
            None,
        )
        evacuation = next(
            (p for p in reversed(cycle.candidate_plans) if p.get("plan_type") == "evacuation"),
            None,
        )
        conflicts: list[str] = []
        if evacuation and evacuation.get("destinations"):
            for dest in evacuation["destinations"]:
                cap = self.cop.get(dest if dest.startswith("shelter:") else f"shelter:{dest}", "full")
                if cap and cap.value is True:
                    conflicts.append(f"destination_full:{dest}")

        actions: list[dict[str, Any]] = []
        if evacuation and not conflicts:
            actions.append(
                {
                    "executor": "a8_dispatch",
                    "action_type": "create_mission",
                    "params": {
                        "mission_type": "evacuate",
                        "origin": evacuation.get("origin"),
                        "route": evacuation.get("primary_route"),
                        "eta_minutes": evacuation.get("eta_minutes"),
                        "risk_score": evacuation.get("risk_score"),
                        "top_triage": (triage or {}).get("top_priority"),
                    },
                }
            )
        elif evacuation and conflicts:
            # Prefer first alternative route if present
            alts = evacuation.get("alternatives") or []
            route = alts[0] if alts else evacuation.get("primary_route")
            actions.append(
                {
                    "executor": "a8_dispatch",
                    "action_type": "reroute_group",
                    "params": {
                        "origin": evacuation.get("origin"),
                        "route": route,
                        "conflicts": conflicts,
                    },
                }
            )

        actions.append(
            {
                "executor": "a9_comms",
                "action_type": "publish_sitrep",
                "params": {
                    "incident_id": cycle.incident_id,
                    "area_id": cycle.area_id,
                    "triage_top": (triage or {}).get("top_priority"),
                    "conflicts": conflicts,
                },
            }
        )
        return {"actions": actions, "conflicts": conflicts, "triage": triage, "evacuation": evacuation}

    def _submit_actions(
        self,
        cycle: IncidentCycle,
        actions: list[dict[str, Any]],
        *,
        rationale: str,
    ) -> list[Event]:
        events: list[Event] = []
        for i, spec in enumerate(actions):
            action = ProposedAction(
                idempotency_key=f"{cycle.incident_id}:{spec['action_type']}:{i}",
                incident_id=cycle.incident_id,
                proposed_by=self.name,
                executor=spec["executor"],
                action_type=spec["action_type"],
                params=spec.get("params") or {},
                rationale=spec.get("rationale") or rationale,
            )
            decision = self.gateway.submit(action)
            cycle.last_decision = decision.model_dump(mode="json")
            events.append(
                Event(
                    type=EventType.PLAN_PROPOSED,
                    source=self.name,
                    incident_id=cycle.incident_id,
                    payload={"action": action.model_dump(mode="json")},
                )
            )
            events.extend(self.gateway.decision_events(action, decision))
            if decision.status != DecisionStatus.AUTHORISED:
                cycle.notes.append(f"gateway:{decision.status.value}:{decision.reason}")
        return events

    def _on_candidate_plan(self, event: Event) -> list[Event]:
        if not event.incident_id:
            return []
        cycle = self.cycles.get(event.incident_id)
        if cycle is None:
            return []
        cycle.candidate_plans.append(dict(event.payload))
        return []

    async def _on_mission_status(self, event: Event) -> list[Event]:
        if not event.incident_id:
            return []
        cycle = self.cycles.get(event.incident_id)
        if cycle is None:
            return []
        status = str(event.payload.get("status", "")).lower()
        if status in {"failed", "blocked", "needs_replan"}:
            cycle.state = CycleState.REPLANNING
            cycle.notes.append(f"replan_due_to:{status}")
            return await self._run_planning_cycle(cycle)
        if status in {"completed", "success"}:
            cycle.state = CycleState.STANDBY
            self.cop.set_incident(cycle.incident_id, {"state": cycle.state.value})
        return []

    def _on_gateway_result(self, event: Event) -> list[Event]:
        if not event.incident_id:
            return []
        cycle = self.cycles.get(event.incident_id)
        if cycle is None:
            return []
        if event.type == EventType.ACTION_AUTHORISED:
            cycle.state = CycleState.ACTIVE
        elif event.type == EventType.ACTION_BLOCKED:
            cycle.notes.append("action_blocked")
        else:
            cycle.notes.append("action_escalated")
        self.cop.set_incident(cycle.incident_id, {"state": cycle.state.value})
        return []

    def _on_risk_update(self, event: Event) -> list[Event]:
        # Soft wake: if we already have an active cycle for this area, note it.
        area_id = event.payload.get("area_id")
        for cycle in self.cycles.values():
            if cycle.area_id == area_id and cycle.state != CycleState.STANDBY:
                cycle.notes.append(f"risk_update:{event.payload.get('level')}")
        return []
