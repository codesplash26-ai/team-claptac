from datetime import timedelta

import pytest

from atlas.agents.a8_dispatch import MissionError, MissionStatus, mission_id
from atlas.control.models import DecisionStatus
from tests.conftest import events
from tests.control.conftest import proposal


async def run(control, *actions):
    for action in actions:
        control.gateway.submit(action)
    await control.gateway.run_pending()


def mission_of(key):
    return mission_id(key)


async def test_create_mission_via_gateway_makes_a_ticket_and_status_event(control, runtime):
    await run(
        control,
        proposal(
            "m1",
            "a8_dispatch",
            "create_mission",
            {
                "title": "Evacuate Ward 4",
                "team": "boat-2",
                "resources_committed": 1,
                "route": [[6.68, 80.4]],
            },
        ),
    )
    mission = control.dispatch.get(mission_of("m1"))
    assert mission.status is MissionStatus.CREATED and mission.team == "boat-2"
    assert control.gateway.get("m1").status is DecisionStatus.EXECUTED
    assert events(runtime, "mission_status")[-1]["payload"]["change"] == "created"


async def test_large_mission_waits_for_human_then_runs(control):
    action = proposal(
        "m2", "a8_dispatch", "create_mission", {"title": "Big", "resources_committed": 5}
    )
    decision = control.gateway.submit(action)
    assert decision.status is DecisionStatus.ESCALATED
    await control.gateway.run_pending()
    assert control.dispatch.get(mission_of("m2")) is None
    control.gateway.approve(decision.approval_id, "cmdr")
    await control.gateway.run_pending()
    assert control.dispatch.get(mission_of("m2")).resources_committed == 5


async def test_execution_is_idempotent(control):
    action = proposal(
        "m3", "a8_dispatch", "create_mission", {"title": "T", "resources_committed": 0}
    )
    await run(control, action)
    result = control.dispatch.execute(action)  # simulate a redelivered execute
    assert result.events == [] and len(control.dispatch.list()) == 1


async def test_invalid_create_fails_then_compensates_nothing_left_behind(control):
    await run(
        control,
        proposal("bad", "a8_dispatch", "create_mission", {"title": "", "resources_committed": 0}),
    )
    assert control.gateway.get("bad").status is DecisionStatus.COMPENSATED
    assert control.dispatch.list() == []


async def test_field_lifecycle_and_state_machine(control):
    await run(
        control,
        proposal("m4", "a8_dispatch", "create_mission", {"title": "T", "resources_committed": 0}),
    )
    mid = mission_of("m4")
    with pytest.raises(MissionError):
        control.dispatch.report_progress(mid, 10)  # must acknowledge first
    control.dispatch.acknowledge(mid, "team-lead")
    control.dispatch.report_progress(mid, 40, "halfway")
    with pytest.raises(MissionError):
        control.dispatch.acknowledge(mid)  # cannot ack twice
    with pytest.raises(MissionError):
        control.dispatch.report_progress(mid, 140)
    control.dispatch.complete(mid)
    done = control.dispatch.get(mid)
    assert done.status is MissionStatus.COMPLETED and done.progress == 100
    with pytest.raises(MissionError):
        control.dispatch.report_exception(mid, "too late")
    assert [h["status"] for h in control.dispatch.history(mid)][0] == "created"


async def test_exception_report_and_fatal_failure(control):
    await run(
        control,
        proposal("m5", "a8_dispatch", "create_mission", {"title": "T", "resources_committed": 0}),
    )
    mid = mission_of("m5")
    event = control.dispatch.report_exception(mid, "bridge washed out")
    assert event.payload["exception"] == "bridge washed out"
    assert control.dispatch.get(mid).status is MissionStatus.CREATED
    control.dispatch.report_exception(mid, "vehicle lost", fatal=True)
    assert control.dispatch.get(mid).status is MissionStatus.FAILED
    with pytest.raises(MissionError):
        control.dispatch.report_exception(mid, "  ")


async def test_unacknowledged_missions_are_flagged_once(control, clock):
    await run(
        control,
        proposal("m6", "a8_dispatch", "create_mission", {"title": "T", "resources_committed": 0}),
    )
    assert control.dispatch.check_overdue() == []
    clock.now += timedelta(seconds=301)
    flagged = control.dispatch.check_overdue()
    assert len(flagged) == 1 and "not acknowledged" in flagged[0].payload["exception"]
    assert control.dispatch.check_overdue() == []  # not re-flagged
    control.dispatch.acknowledge(mission_of("m6"), "lead")
    assert control.dispatch.get(mission_of("m6")).exception is None


async def test_cancel_reroute_update(control):
    await run(
        control,
        proposal("m7", "a8_dispatch", "create_mission", {"title": "T", "resources_committed": 0}),
    )
    mid = mission_of("m7")
    await run(
        control,
        proposal("u1", "a8_dispatch", "update_mission", {"mission_id": mid, "team": "b"}),
        proposal(
            "r1", "a8_dispatch", "reroute_group", {"mission_id": mid, "route": [[1, 2], [3, 4]]}
        ),
    )
    m = control.dispatch.get(mid)
    assert m.team == "b" and m.route == [[1, 2], [3, 4]]
    cancel = control.gateway.submit(
        proposal("c1", "a8_dispatch", "cancel_mission", {"mission_id": mid})
    )
    assert cancel.status is DecisionStatus.ESCALATED  # cancelling is always human-approved
    control.gateway.approve(cancel.approval_id, "cmdr")
    await control.gateway.run_pending()
    assert control.dispatch.get(mid).status is MissionStatus.CANCELLED
    await run(
        control,
        proposal("r2", "a8_dispatch", "reroute_group", {"mission_id": mid, "route": [[9, 9]]}),
    )
    assert control.gateway.get("r2").status is DecisionStatus.COMPENSATED  # cancelled: no reroute


async def test_close_major_route_updates_the_cop_after_approval(control, runtime):
    decision = control.gateway.submit(
        proposal("route", "a8_dispatch", "close_major_route", {"road_id": "A4-12"})
    )
    assert decision.status is DecisionStatus.ESCALATED
    assert runtime.cop.get("edge:A4-12", "open") is None
    control.gateway.approve(decision.approval_id, "cmdr")
    await control.gateway.run_pending()
    belief = runtime.cop.get("edge:A4-12", "open")
    assert belief.value is False and belief.provenance == ["action:route"]


async def test_missing_mission_reference_fails_safely(control):
    await run(control, proposal("u9", "a8_dispatch", "update_mission", {"mission_id": "m-nope"}))
    assert control.gateway.get("u9").status is DecisionStatus.COMPENSATED
