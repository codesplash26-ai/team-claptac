"""The inventory and its lifecycle controls: suspending an agent is real, and loses nothing."""

import pytest

from atlas.core.config import SentinelConfig, Settings
from atlas.ingest.simulator import ReplayClock, replay
from atlas.manager.registry import NotFound, Registry
from atlas.runtime import Runtime
from atlas.system import attach_all
from tests.conftest import approve_alerts, events
from tests.control.conftest import fast_policy
from tests.orchestration.conftest import reading, scenario

RISING = scenario(reading(0, 10), reading(120, 26), reading(130, 26))


@pytest.fixture
def reg(system):
    return Registry(system, site="ratnapura")


def test_inventory_lists_every_agent_and_says_which_are_external(reg):
    agents = {a.name: a for a in reg.agents()}
    assert {
        "a1_sentinel",
        "a2_hazard_intel",
        "a3_incident_commander",
        "a4_evacuation",
        "a5_triage",
        "a6_logistics",
        "a7_shelter",
        "a8_dispatch",
        "a9_comms",
        "action_gateway",
        "human",
        "sensors",
    } <= set(agents)
    assert agents["sensors"].kind == "external" and agents["a4_evacuation"].kind == "internal"
    assert all(a.status == "active" and a.version for a in agents.values())
    assert reg.summary() == {
        "agents": len(agents),
        "suspended": 0,
        "external": 1,
        "site": "ratnapura",
    }
    assert [e["name"] for e in reg.environments()] == ["live", "replay", "sandbox"]
    with pytest.raises(NotFound):
        reg.agent("a13_ghost")


def test_what_ATLAS_does_not_run_cannot_be_suspended(reg):
    for name in ("sensors", "human"):
        with pytest.raises(ValueError, match="not run by ATLAS"):
            reg.suspend(name, by="t", reason="nope")
    with pytest.raises(NotFound):
        reg.suspend("a13_ghost", by="t", reason="nope")


async def test_a_suspended_agent_holds_its_work_and_resuming_finishes_it(
    system, runtime, clock, reg
):
    await runtime.start()
    reg.suspend("a6_logistics", by="ops", reason="fleet maintenance")
    record = reg.agent("a6_logistics")
    assert record.status == "suspended" and record.suspended_by == "ops"
    assert record.suspended_reason == "fleet maintenance"

    await replay(runtime, RISING, clock)
    for _ in range(3):
        await system.control.gateway.run_pending()
    # A3 asked A6 for units and is still waiting: no plan, no missions, nothing lost
    assert events(runtime, "task_assigned") and not events(runtime, "plan_proposed")
    assert reg.agent("a6_logistics").pending >= 1 and not system.control.dispatch.list()
    (incident,) = system.orchestration.commander.incidents()
    assert incident["state"] == "planning"

    await reg.resume("a6_logistics", by="ops")
    for _ in range(3):
        await system.control.gateway.run_pending()
    assert reg.agent("a6_logistics").status == "active" and reg.agent("a6_logistics").pending == 0
    assert events(runtime, "plan_proposed") and len(system.control.dispatch.list()) == 5
    assert runtime.ledger.verify()


async def test_suspending_an_executor_leaves_authorised_actions_waiting(
    system, runtime, clock, reg
):
    await runtime.start()
    reg.suspend("a9_comms", by="ops", reason="SMS gateway down")
    await replay(runtime, RISING, clock)
    for _ in range(3):
        await system.control.gateway.run_pending()
        await approve_alerts(system)  # authorised by the commander, then held by the suspension
    assert system.control.dispatch.list() and not system.control.comms.list_alerts()
    assert reg.agent("a9_comms").pending == 2  # the alert and the sitrep, authorised and held
    await reg.resume("a9_comms", by="ops")
    assert len(system.control.comms.list_alerts()) == 1 and reg.agent("a9_comms").pending == 0


async def test_a_gateway_that_was_held_refuses_plans_made_on_an_old_picture(
    system, runtime, clock, reg
):
    """Held plans go stale while the world moves on. Resuming must not let them through."""
    await runtime.start()
    reg.suspend("action_gateway", by="ops", reason="kill switch")
    await replay(runtime, RISING, clock)
    assert events(runtime, "plan_proposed") and not events(runtime, "action_authorised")
    assert not system.control.dispatch.list()
    await reg.resume("action_gateway", by="ops")
    for _ in range(3):
        await system.control.gateway.run_pending()
    reasons = {a["decision"]["reason"] for a in system.control.gateway.list_actions()}
    assert "stale_snapshot" in reasons and not system.control.dispatch.list()
    (incident,) = system.orchestration.commander.incidents()
    assert incident["needs_human"]  # A3 replanned up to its bound, then asked a person
    assert runtime.ledger.verify()


async def test_resuming_something_that_is_not_suspended_is_an_error(reg):
    with pytest.raises(ValueError, match="not suspended"):
        await reg.resume("a6_logistics", by="ops")


async def test_suspension_survives_a_restart(tmp_path):
    path = str(tmp_path / "atlas.db")

    def build():
        clock = ReplayClock()
        rt = Runtime(
            Settings(database_path=path, sentinel=SentinelConfig(reasoning_enabled=True)),
            clock=clock,
        )
        return rt, attach_all(rt, policy=fast_policy())

    first, system = build()
    Registry(system).suspend("a7_shelter", by="ops", reason="survive restart")
    first.db.close()
    second, system2 = build()
    assert "a7_shelter" in second.bus.suspended
    assert Registry(system2).agent("a7_shelter").suspended_reason == "survive restart"
    second.db.close()


def test_lifecycle_over_http(client):
    url = "/am/v1/agents/a5_triage"
    assert client.get("/am/v1/agents").json()["summary"]["suspended"] == 0
    assert client.get(url).json()["status"] == "active"
    assert (
        client.post(url + "/suspend", json={"reason": "no"}).status_code == 422
    )  # reason too short
    assert (
        client.post(url + "/suspend", json={"reason": "triage review"}).json()["status"]
        == "suspended"
    )
    assert client.get("/am/v1/agents").json()["summary"]["suspended"] == 1
    assert client.post(url + "/resume").json()["status"] == "active"
    assert client.post(url + "/resume").status_code == 409
    assert client.get("/am/v1/agents/nope").status_code == 404
    assert (
        client.post("/am/v1/agents/sensors/suspend", json={"reason": "cannot"}).status_code == 409
    )
    assert client.get("/am/v1/environments").json()[0]["name"] == "live"
