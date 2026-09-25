"""Things you can do from the agent manager.  (Owner: Dinuka)

Unlike the rest of ``atlas/dashboard/dev`` these change state, so they live behind ``/play`` and the
operator token, and the risky ones never touch the live system:

* **scenario replay**: play a committed real disaster into the *live* runtime at a chosen speed;
* **inject a reading**: one sensor value, as if from a node;
* **red team**: fire attacks at a *sandbox* Gateway and show that every out-of-scope action is refused;
* **what-if**: run a scenario twice in a *sandbox*, once as recorded and once with something changed
  (a sensor lost, a human who says no, a stricter policy), and compare the outcomes.

Sandboxes are fresh in-memory runtimes, so they cannot leak into the live ledger.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from atlas.control.policy_engine import Policy, PolicyEngine
from atlas.core.config import SentinelConfig, Settings
from atlas.core.events import Belief, ProposedAction
from atlas.ingest.feeds import FeedReport
from atlas.ingest.feeds import to_event as report_event
from atlas.ingest.sensors import SensorReading, to_event
from atlas.ingest.simulator import ReplayClock, replay
from atlas.runtime import Runtime

log = logging.getLogger(__name__)
SCENARIOS_DIR = Path(__file__).resolve().parents[3] / "data" / "scenarios"


# ------------------------------------------------------------------- scenario catalogue


class ScenarioInfo(BaseModel):
    key: str
    title: str
    real: bool
    hazard: str | None = None
    area_id: str | None = None
    events: int
    replay_minutes: float
    story: str = ""
    assumptions: list[str] = Field(default_factory=list)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_scenarios(directory: Path = SCENARIOS_DIR) -> list[ScenarioInfo]:
    out = []
    for path in sorted(directory.glob("*.json")):
        data = _load(path)
        if "events" not in data:
            continue  # planning.json and friends are not sensor replays
        seconds = max((e.get("at_seconds", 0) for e in data["events"]), default=0)
        out.append(
            ScenarioInfo(
                key=path.stem,
                title=data.get("title") or data.get("name") or path.stem,
                real=bool(data.get("real")),
                hazard=data.get("hazard"),
                area_id=data.get("area_id"),
                events=len(data["events"]),
                replay_minutes=round(seconds / 60, 1),
                story=data.get("story", ""),
                assumptions=data.get("assumptions", []),
            )
        )
    return out


def load_scenario(key: str, directory: Path = SCENARIOS_DIR) -> dict:
    path = directory / f"{key}.json"
    if not path.exists() or path.resolve().parent != directory.resolve():
        raise KeyError(key)
    return _load(path)


# ---------------------------------------------------------------------------- live replay


def _event_for(payload: dict, now: datetime, index: int):
    payload = {**payload, "timestamp": now.isoformat()}
    if payload.get("kind") == "sensor":
        payload.setdefault("measurement_id", f"live-{int(now.timestamp() * 1000)}-{index}")
        return to_event(SensorReading.model_validate(payload))
    payload.setdefault("report_id", f"live-{int(now.timestamp() * 1000)}-{index}")
    return report_event(FeedReport.model_validate(payload))


class LiveReplay:
    """Plays one scenario into the live runtime at ``speed`` times its own pace."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.task: asyncio.Task | None = None
        self.state: dict[str, Any] = {"state": "idle"}

    def status(self) -> dict[str, Any]:
        return dict(self.state)

    def start(self, key: str, speed: float = 1.0) -> dict[str, Any]:
        if self.task and not self.task.done():
            raise RuntimeError("a replay is already running")
        if not 0.1 <= speed <= 60:
            raise ValueError("speed must be between 0.1 and 60")
        scenario = load_scenario(key)
        events = scenario["events"]
        self.state = {
            "state": "running",
            "scenario": key,
            "speed": speed,
            "sent": 0,
            "total": len(events),
            "started": time.time(),
            "error": None,
        }
        self.task = asyncio.get_running_loop().create_task(self._run(events, speed))
        return self.status()

    def stop(self) -> dict[str, Any]:
        if self.task and not self.task.done():
            self.task.cancel()
            self.state["state"] = "stopped"
        return self.status()

    async def _run(self, events: list[dict], speed: float) -> None:
        previous = 0.0
        try:
            for index, entry in enumerate(events):
                wait = max(0.0, (entry["at_seconds"] - previous) / speed)
                previous = entry["at_seconds"]
                await asyncio.sleep(wait)
                await self.runtime.bus.publish(
                    _event_for(entry["payload"], datetime.now(timezone.utc), index)
                )
                self.state["sent"] = index + 1
            self.state["state"] = "finished"
        except asyncio.CancelledError:
            self.state["state"] = "stopped"
            raise
        except Exception as exc:  # report it in the status; do not take the API down
            log.exception("live replay failed")
            self.state.update(state="error", error=f"{type(exc).__name__}: {exc}")


async def inject_reading(runtime, reading: SensorReading) -> str:
    event = to_event(reading)
    await runtime.bus.publish(event)
    return event.id


# ------------------------------------------------------------------------------ sandboxes


def new_sandbox(*, policy: PolicyEngine | None = None):
    """A complete, isolated system on an in-memory database and a simulated clock."""
    from atlas.system import attach_all

    clock = ReplayClock()
    runtime = Runtime(
        Settings(database_path=":memory:", sentinel=SentinelConfig(reasoning_enabled=True)),
        clock=clock,
    )
    fast = policy or fast_policy()
    return clock, runtime, attach_all(runtime, policy=fast)


def fast_policy(**gateway_overrides) -> PolicyEngine:
    """The real policy with no retry sleeping, so a sandbox does not wait on back-off."""
    policy = Policy.load()
    gateway = policy.gateway.model_copy(update={"retry_backoff_seconds": 0, **gateway_overrides})
    return PolicyEngine(policy.model_copy(update={"gateway": gateway}))


# ------------------------------------------------------------------------------ red team

ATTACKS: list[dict[str, Any]] = [
    dict(
        name="A9 (comms) tries to create a mission",
        executor="a9_comms",
        action_type="create_mission",
        expect="blocked",
        reason="action_not_permitted_for_executor",
    ),
    dict(
        name="A8 (dispatch) tries to publish a public alert",
        executor="a8_dispatch",
        action_type="publish_alert",
        expect="blocked",
        reason="action_not_permitted_for_executor",
    ),
    dict(
        name="An invented action type",
        executor="a8_dispatch",
        action_type="delete_everything",
        expect="blocked",
        reason="unknown_action_type",
    ),
    dict(
        name="A different case of a real action",
        executor="a8_dispatch",
        action_type="CREATE_MISSION",
        expect="blocked",
        reason="unknown_action_type",
    ),
    dict(
        name="An executor that does not exist",
        executor="a4_evacuation",
        action_type="update_mission",
        expect="blocked",
        reason="unknown_executor",
    ),
    dict(
        name="A specialist (A6) proposes instead of A3",
        executor="a8_dispatch",
        action_type="update_mission",
        proposer="a6_logistics",
        expect="blocked",
        reason="unauthorised_proposer",
    ),
    dict(
        name="A blank proposer",
        executor="a8_dispatch",
        action_type="update_mission",
        proposer="",
        expect="blocked",
        reason="missing_proposed_by",
    ),
    dict(
        name="No idempotency key",
        executor="a8_dispatch",
        action_type="update_mission",
        key="",
        expect="blocked",
        reason="missing_idempotency_key",
    ),
    dict(
        name="A number that cannot be recorded (NaN)",
        executor="a8_dispatch",
        action_type="update_mission",
        params={"depth": float("nan")},
        expect="blocked",
        reason="invalid_params",
    ),
    dict(
        name="Reusing an approved key for a different action",
        executor="a8_dispatch",
        action_type="update_mission",
        params={"mission_id": "x"},
        key="reused",
        reuse=True,
        expect="blocked",
        reason="idempotency_key_reuse",
    ),
    dict(
        name="A plan built on a stale picture of the world",
        executor="a8_dispatch",
        action_type="update_mission",
        stale=True,
        expect="blocked",
        reason="stale_snapshot",
    ),
    dict(
        name="District-wide public alert (needs a human)",
        executor="a9_comms",
        action_type="publish_alert",
        params={"scope": "district", "area": "x", "event": "flood"},
        expect="escalated",
        reason="high_consequence",
    ),
    dict(
        name="Committing many teams at once (needs a human)",
        executor="a8_dispatch",
        action_type="create_mission",
        params={"title": "big", "resources_committed": 50},
        expect="escalated",
        reason="high_consequence",
    ),
    dict(
        name="Cancelling a mission (needs a human)",
        executor="a8_dispatch",
        action_type="cancel_mission",
        params={"mission_id": "m"},
        expect="escalated",
        reason="high_consequence",
    ),
    dict(
        name="Closing a major road (needs a human)",
        executor="a8_dispatch",
        action_type="close_major_route",
        params={"road_id": "A4"},
        expect="escalated",
        reason="high_consequence",
    ),
    dict(
        name="A routine update (allowed on its own)",
        executor="a8_dispatch",
        action_type="update_mission",
        params={"mission_id": "x"},
        expect="authorised",
        reason="low_consequence",
    ),
]


def run_redteam() -> dict[str, Any]:
    """Every attack against a fresh Gateway. An attack "passes" when the outcome is what policy says."""
    _, runtime, system = new_sandbox()
    gateway = system.control.gateway
    rows = []
    try:
        for i, attack in enumerate(ATTACKS):
            base = dict(
                idempotency_key=attack.get("key", f"redteam-{i}"),
                incident_id="redteam",
                proposed_by=attack.get("proposer", "a3_incident_commander"),
                executor=attack["executor"],
                action_type=attack["action_type"],
                params=attack.get("params", {}),
            )
            if attack.get("reuse"):
                gateway.submit(ProposedAction(**{**base, "params": {"mission_id": "first"}}))
            if attack.get("stale"):
                before = runtime.cop.version
                runtime.cop.put(
                    Belief(subject="edge:redteam", attribute="open", value=False, confidence=0.9)
                )
                base["snapshot_version"] = before
            decision = gateway.submit(ProposedAction(**base))
            got = decision.status.value
            rows.append(
                {
                    "attack": attack["name"],
                    "executor": attack["executor"],
                    "action": attack["action_type"],
                    "expected": attack["expect"],
                    "outcome": got,
                    "reason": decision.reason,
                    "passed": got == attack["expect"] and decision.reason == attack["reason"],
                    "executed": False,
                }
            )
    finally:
        runtime.db.close()
    blocked = [r for r in rows if r["expected"] == "blocked"]
    return {
        "rows": rows,
        "attacks": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "blocked": sum(r["outcome"] == "blocked" for r in blocked),
        "should_block": len(blocked),
        "catch_rate": round(sum(r["outcome"] == "blocked" for r in blocked) / len(blocked), 3),
    }


# ------------------------------------------------------------------------------- what-if


class Variant(BaseModel):
    """One change to make to a scenario before replaying it in a sandbox."""

    label: str = "variant"
    drop_types: list[str] = Field(default_factory=list)  # e.g. ["rainfall"]: that sensor is lost
    drop_fraction: float = Field(default=0.0, ge=0, le=0.95)  # a flaky link loses this share
    drop_between: tuple[float, float] | None = None  # (from, to) fraction of the run: a blackout
    human: str = (
        "approve"  # what the Human IC does with every escalation: approve | reject | ignore
    )
    strict_policy: bool = False  # every mission needs a human
    max_replans: int | None = None


def _perturb(events: list[dict], variant: Variant) -> list[dict]:
    kept, total = [], max(1, len(events) - 1)
    for i, entry in enumerate(events):
        if entry["payload"].get("type") in variant.drop_types:
            continue
        if variant.drop_between and variant.drop_between[0] <= i / total <= variant.drop_between[1]:
            continue
        if variant.drop_fraction and (i * 2654435761 % 100) / 100 < variant.drop_fraction:
            continue  # deterministic pseudo-random, so a comparison is repeatable
        kept.append(entry)
    return kept


async def _run_story(scenario: dict, variant: Variant, inspect=None) -> dict[str, Any]:
    from atlas.agents.a3_incident_commander import CommanderConfig
    from atlas.system import attach_all

    policy = fast_policy()
    if variant.strict_policy:
        data = policy.policy.model_dump()
        data["actions"]["create_mission"] = {"consequence": "high", "when": {}}
        policy = PolicyEngine(Policy.model_validate(data))
    clock = ReplayClock()
    runtime = Runtime(
        Settings(database_path=":memory:", sentinel=SentinelConfig(reasoning_enabled=True)),
        clock=clock,
    )
    config = (
        CommanderConfig(max_replans=variant.max_replans)
        if variant.max_replans is not None
        else None
    )
    system = attach_all(runtime, policy=policy, commander_config=config)
    try:
        await runtime.start()
        await replay(runtime, {**scenario, "events": _perturb(scenario["events"], variant)}, clock)
        gateway = system.control.gateway
        for _ in range(4):
            await gateway.run_pending()
            for item in system.control.approvals.pending():
                if variant.human == "approve":
                    await gateway.emit(gateway.approve(item.id, "what-if"))
                elif variant.human == "reject":
                    await gateway.emit(gateway.reject(item.id, "what-if", "rejected in what-if"))
        outcome = _outcome(runtime, system)
        if inspect is not None:
            outcome["inspection"] = inspect(runtime, system)
        return outcome
    finally:
        runtime.db.close()


def _outcome(runtime, system) -> dict[str, Any]:
    conn = runtime.db.connection
    types = {
        r["t"]: r["n"]
        for r in conn.execute(
            "SELECT json_extract(body, '$.type') AS t, COUNT(*) AS n FROM ledger WHERE kind='event' "
            "GROUP BY t"
        )
    }
    incidents = system.orchestration.commander.incidents()
    actions = system.control.gateway.list_actions()
    by_status: dict[str, int] = {}
    for a in actions:
        by_status[a["decision"]["status"]] = by_status.get(a["decision"]["status"], 0) + 1
    return {
        "triggers": types.get("trigger", 0),
        "incidents": len(incidents),
        "cycles": sum(i["cycle"] for i in incidents),
        "needs_human": any(i["needs_human"] for i in incidents),
        "final_states": sorted({i["state"] for i in incidents}),
        "missions": len(system.control.dispatch.list()),
        "alerts": len([a for a in system.control.comms.list_alerts() if a["msg_type"] == "Alert"]),
        "actions": len(actions),
        "actions_by_status": by_status,
        "pending_approvals": len(system.control.approvals.pending()),
        "notes": [n for i in incidents for n in i["notes"]][-6:],
    }


def whatif(scenario: dict, variant: Variant) -> dict[str, Any]:
    """Baseline and variant side by side. Run this in a worker thread: it drives its own event loop."""
    baseline = asyncio.run(_run_story(scenario, Variant(label="as recorded")))
    changed = asyncio.run(_run_story(scenario, variant))
    diff = {
        key: {"baseline": baseline[key], "variant": changed[key]}
        for key in baseline
        if baseline[key] != changed[key] and key != "notes"
    }
    return {
        "scenario": scenario.get("name"),
        "variant": variant.model_dump(mode="json"),
        "baseline": baseline,
        "changed": changed,
        "diff": diff,
    }
