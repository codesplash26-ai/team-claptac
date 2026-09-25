"""What each agent is and does, in plain language.  (Owner: Dinuka)

Static description (from the proposal and each agent's module docstring) plus live activity: the last
thing it received, the last thing it produced, counts, and whether it ever used the LLM. This is what
lets a viewer see what each agent is doing without reading code.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from atlas.dashboard.dev.trace import GATEWAY, HUMAN, SENSORS, node_of, trace


class AgentCard(BaseModel):
    name: str  # "a4_evacuation"
    title: str  # "Evacuation & Access"
    layer: str  # sense | plan | act | control | world
    purpose: str  # one sentence a non-engineer understands
    reads: list[str] = Field(default_factory=list)  # event types and COP subjects
    writes: list[str] = Field(default_factory=list)
    uses_llm: bool = False
    deterministic_tools: list[str] = Field(default_factory=list)
    owner: str = ""
    # live, filled in by ``cards``
    subscribed: list[str] = Field(default_factory=list)
    received: int = 0
    produced: int = 0
    last_received: str | None = None
    last_produced: str | None = None


def _card(name, title, layer, purpose, reads, writes, tools=(), owner="", llm=False):
    return AgentCard(
        name=name,
        title=title,
        layer=layer,
        purpose=purpose,
        reads=list(reads),
        writes=list(writes),
        deterministic_tools=list(tools),
        owner=owner,
        uses_llm=llm,
    )


CATALOG: dict[str, AgentCard] = {
    c.name: c
    for c in [
        _card(
            SENSORS,
            "Sensors and feeds",
            "world",
            "Physical river, rain and soil sensors (or a replayed real event) and official reports.",
            [],
            ["observation"],
            ["LoRa / MQTT / HTTP ingest"],
            "Hasindu",
        ),
        _card(
            "a1_sentinel",
            "A1 Sentinel",
            "sense",
            "Always on. Checks every reading, removes duplicates, scores confidence, and raises a "
            "trigger only when something significant changes.",
            ["observation"],
            [
                "observation_validated",
                "observation_rejected",
                "trigger",
                "conflict_flag",
                "information_gap",
                "COP: area beliefs",
            ],
            ["threshold and rate-of-rise rules", "corroboration", "planner for conflicts"],
            "Aeshan",
        ),
        _card(
            "a2_hazard_intel",
            "A2 Hazard & Vulnerability",
            "sense",
            "Turns validated readings into a risk level per area and says how many vulnerable people "
            "are exposed and what is still unknown.",
            ["observation_validated", "trigger", "conflict_flag"],
            ["risk_update (hazard_risk)", "COP: area risk_level, hazard"],
            ["level = ratio of the Sentinel's thresholds", "vulnerability profiles"],
            "Aeshan",
        ),
        _card(
            "a3_incident_commander",
            "A3 Incident Commander",
            "plan",
            "The only agent that delegates. Wakes on a trigger, asks the specialists for plans, "
            "reconciles them into one plan, and sends it to the Gateway. Replans when things change.",
            [
                "trigger",
                "candidate_plan",
                "risk_update",
                "mission_status",
                "action_authorised",
                "action_escalated",
                "action_blocked",
            ],
            ["task_assigned", "plan_proposed"],
            ["reconcile (pure function)", "bounded replans"],
            "Aeshan",
        ),
        _card(
            "a4_evacuation",
            "A4 Evacuation & Access",
            "plan",
            "Keeps a belief-weighted map of which roads are passable and computes evacuation routes "
            "with risk and uncertainty.",
            ["observation_validated", "task_assigned"],
            ["risk_update (road_access_changed)", "candidate_plan", "COP: edge belief_open"],
            ["belief-weighted routing on the real road network"],
            "Sumudu",
        ),
        _card(
            "a5_triage",
            "A5 Rescue & Needs Triage",
            "plan",
            "Ranks who needs help first: urgency times vulnerability, always with an explanation.",
            ["risk_update", "task_assigned"],
            ["candidate_plan"],
            ["transparent scoring", "re-rank when the top priority moves"],
            "Aeshan",
        ),
        _card(
            "a6_logistics",
            "A6 Resource & Logistics",
            "plan",
            "Matches rescue teams and vehicles to jobs and flags shortages.",
            ["task_assigned"],
            ["candidate_plan"],
            ["CP-SAT optimiser with greedy fallback"],
            "Sumudu",
        ),
        _card(
            "a7_shelter",
            "A7 Shelter & Relief",
            "plan",
            "Decides which shelter takes which group so nobody is sent to a full or unreachable "
            "shelter, and asks for resupply.",
            ["task_assigned"],
            ["candidate_plan"],
            ["CP-SAT optimiser", "safe-route check"],
            "Sumudu",
        ),
        _card(
            GATEWAY,
            "Action Gateway",
            "control",
            "Every proposed action passes here. It checks the proposer and the action against "
            "policy, authorises routine actions, asks a human about high-consequence ones, blocks "
            "the rest, and records everything.",
            ["plan_proposed"],
            ["action_authorised", "action_escalated", "action_blocked"],
            [
                "policy engine",
                "approval queue",
                "retry / verify / compensate",
                "append-only ledger",
            ],
            "Dinuka",
        ),
        _card(
            HUMAN,
            "Human Incident Commander",
            "control",
            "Approves, edits or rejects the actions the Gateway will not run on its own.",
            ["action_escalated"],
            ["approve / edit / reject"],
            ["approval queue"],
            "Dinuka",
        ),
        _card(
            "a8_dispatch",
            "A8 Dispatch",
            "act",
            "Turns authorised actions into trackable mission tickets and follows them until done.",
            ["authorised create / update / cancel mission, reroute, close route"],
            ["mission_status", "COP: closed roads"],
            ["mission state machine"],
            "Dinuka",
        ),
        _card(
            "a9_comms",
            "A9 Public Communications",
            "act",
            "Writes public alerts in Sinhala, Tamil and English as CAP 1.2 messages and compiles "
            "situation reports.",
            ["authorised publish / cancel alert, publish sitrep"],
            ["alert_published"],
            ["CAP 1.2 builder and validator", "phrasebook translation"],
            "Dinuka",
        ),
    ]
}


def cards(runtime) -> list[AgentCard]:
    """The catalog with live activity: counts and the last thing each agent received and produced."""
    live = {name: card.model_copy(deep=True) for name, card in CATALOG.items()}
    for name in runtime.bus.handlers:
        live.setdefault(
            name, AgentCard(name=name, title=name, layer="plan", purpose="(not in catalog)")
        )
    for name, (types, _) in runtime.bus.handlers.items():
        live[name].subscribed = sorted(t.value for t in types)
    newest = runtime.db.connection.execute("SELECT COALESCE(MAX(seq), 0) FROM ledger").fetchone()[0]
    # Live counts cover the most recent 1000 events, which is what a person watching cares about.
    for event in trace(runtime, after_seq=max(0, newest - 1200), limit=1000):
        producer = live.get(node_of(event.raw_source))
        if producer is not None:
            producer.produced += 1
            producer.last_produced = event.summary
        for name in event.handled_by:
            if name in live:
                live[name].received += 1
                live[name].last_received = event.summary
    # A8 and A9 are called by the Gateway, not by the bus, so their "received" comes from executions.
    for row in runtime.db.connection.execute(
        "SELECT json_extract(action, '$.executor') AS executor, "
        "json_extract(action, '$.action_type') AS action_type FROM gateway_actions "
        "WHERE status IN ('executed', 'compensated', 'failed_uncompensated') ORDER BY updated_at"
    ):
        if row["executor"] in live:
            live[row["executor"]].received += 1
            live[row["executor"]].last_received = f"asked to {row['action_type']} (authorised)"
    return list(live.values())
