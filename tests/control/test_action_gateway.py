import pytest

from atlas.control.action_gateway import Gateway
from atlas.control.approvals import ApprovalError, ApprovalQueue
from atlas.control.models import (
    ApprovalStatus,
    DecisionStatus,
    ExecutionResult,
    Receipt,
)
from atlas.control.policy_engine import Policy, PolicyEngine
from atlas.core.events import Belief, Event, EventType, ProposedAction
from tests.conftest import events


class FakeExecutor:
    def __init__(self, *, fail_times=0, verify_ok=True, compensate_fails=False):
        self.calls, self.compensated = 0, []
        self.fail_times, self.verify_ok, self.compensate_fails = (
            fail_times,
            verify_ok,
            compensate_fails,
        )

    async def execute(self, action):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("channel down")
        return ExecutionResult(
            receipt=Receipt(
                ref=f"ref-{action.idempotency_key}",
                executed_at=action.created_at if hasattr(action, "created_at") else _EPOCH,
            ),
            events=[
                Event(
                    type=EventType.MISSION_STATUS,
                    source="fake",
                    incident_id=action.incident_id,
                    payload={"ref": action.idempotency_key},
                )
            ],
        )

    def verify(self, action, result):
        return self.verify_ok

    async def compensate(self, action, result):
        if self.compensate_fails:
            raise RuntimeError("cannot undo")
        self.compensated.append(action.idempotency_key)


from datetime import datetime, timezone  # noqa: E402

_EPOCH = datetime(2026, 9, 20, tzinfo=timezone.utc)


def make_policy(retries=2):
    policy = Policy.load()
    gateway = policy.gateway.model_copy(update={"max_retries": retries, "retry_backoff_seconds": 0})
    return PolicyEngine(policy.model_copy(update={"gateway": gateway}))


@pytest.fixture
def gw(runtime, clock):
    published = []

    async def publish(event):
        published.append(event)
        await runtime.bus.publish(event)

    gateway = Gateway(
        runtime.db,
        runtime.ledger,
        make_policy(),
        ApprovalQueue(runtime.db),
        cop=runtime.cop,
        publish=publish,
        clock=clock,
    )
    gateway.published = published
    return gateway


def action(key="k1", action_type="update_mission", executor="a8_dispatch", params=None, **kw):
    return ProposedAction(
        idempotency_key=key,
        incident_id="inc-1",
        proposed_by="a3_incident_commander",
        executor=executor,
        action_type=action_type,
        params=params or {},
        **kw,
    )


def phases(runtime, key):
    return [
        r["payload"]["phase"]
        for r in runtime.ledger.records("action")
        if r["payload"].get("idempotency_key") == key
    ]


def test_low_consequence_action_is_authorised_and_ledgered(gw, runtime):
    decision = gw.submit(action())
    assert decision.status is DecisionStatus.AUTHORISED
    assert phases(runtime, "k1") == ["authorised"]
    assert runtime.ledger.verify()


def test_out_of_scope_action_is_blocked_and_never_executes(gw, runtime):
    decision = gw.submit(action(action_type="publish_alert"))
    assert decision.status is DecisionStatus.BLOCKED
    assert decision.reason == "action_not_permitted_for_executor"
    assert phases(runtime, "k1") == ["blocked"]


async def test_blocked_action_is_never_run(gw):
    executor = FakeExecutor()
    gw.register_executor("a8_dispatch", executor)
    gw.submit(action(action_type="publish_alert"))
    assert await gw.run_pending() == 0 and executor.calls == 0


def test_high_consequence_action_waits_for_human(gw):
    decision = gw.submit(action(action_type="cancel_mission"))
    assert decision.status is DecisionStatus.ESCALATED
    assert [i.id for i in gw.approvals.pending()] == [decision.approval_id]


async def test_escalated_action_does_not_execute_until_approved(gw):
    executor = FakeExecutor()
    gw.register_executor("a8_dispatch", executor)
    decision = gw.submit(action(action_type="cancel_mission"))
    await gw.run_pending()
    assert executor.calls == 0
    gw.approve(decision.approval_id, "cmdr")
    await gw.run_pending()
    assert executor.calls == 1 and gw.get("k1").status is DecisionStatus.EXECUTED


def test_resubmission_is_idempotent(gw, runtime):
    first = gw.submit(action())
    second = gw.submit(action())  # a fresh ProposedAction id, same key and content
    assert second.action_id == first.action_id and second.status == first.status
    assert phases(runtime, "k1") == ["authorised"]


def test_key_reuse_with_different_content_is_blocked_and_original_kept(gw):
    gw.submit(action(params={"a": 1}))
    clash = gw.submit(action(params={"a": 2}))
    assert clash.status is DecisionStatus.BLOCKED and clash.reason == "idempotency_key_reuse"
    assert gw.get("k1").status is DecisionStatus.AUTHORISED
    assert gw.submit(action(params={"a": 2})).reason == "idempotency_key_reuse"  # repeatable


def test_missing_idempotency_key_is_blocked(gw):
    decision = gw.submit(action(key="  "))
    assert decision.status is DecisionStatus.BLOCKED
    assert decision.reason == "missing_idempotency_key"


def test_unserialisable_params_are_blocked_without_poisoning_the_ledger(gw, runtime):
    decision = gw.submit(action(params={"depth": float("nan")}))
    assert decision.reason == "invalid_params" and runtime.ledger.verify()
    assert all(
        e.payload["action"]["params"] == {"_redacted": "invalid_params"}
        for e in gw.events_for(decision)
    )


def test_unauthorised_proposer_is_blocked(gw):
    rogue = ProposedAction(
        idempotency_key="r",
        incident_id="i",
        proposed_by="a6_logistics",
        executor="a8_dispatch",
        action_type="update_mission",
    )
    assert gw.submit(rogue).reason == "unauthorised_proposer"


def test_stale_snapshot_is_blocked(gw, runtime, clock):
    version = runtime.cop.version
    runtime.cop.put(
        Belief(
            subject="edge:1", attribute="open", value=False, confidence=0.9, updated_at=clock.now
        )
    )
    assert gw.submit(action("s1", snapshot_version=version)).reason == "stale_snapshot"
    assert (
        gw.submit(action("s2", snapshot_version=runtime.cop.version)).status
        is DecisionStatus.AUTHORISED
    )
    assert gw.submit(action("s3")).status is DecisionStatus.AUTHORISED  # no snapshot claimed


# ---------------------------------------------------------------- bus integration


async def test_plan_proposed_flows_through_bus_to_decision_events(gw, runtime):
    runtime.bus.subscribe(gw.name, gw.subscribes_to, gw.handle_plan)
    plan = Event(
        type=EventType.PLAN_PROPOSED,
        source="a3_incident_commander",
        incident_id="inc-1",
        payload={
            "actions": [
                action("a").model_dump(mode="json"),
                action("b", action_type="cancel_mission").model_dump(mode="json"),
                action("c", action_type="publish_alert").model_dump(mode="json"),
                {"junk": True},
            ]
        },
    )
    await runtime.bus.publish(plan)
    assert len(events(runtime, "action_authorised")) == 1
    assert len(events(runtime, "action_escalated")) == 1
    assert len(events(runtime, "action_blocked")) == 2  # policy block + malformed
    assert runtime.ledger.verify()


async def test_redelivered_plan_does_not_duplicate_effects(gw, runtime):
    runtime.bus.subscribe(gw.name, gw.subscribes_to, gw.handle_plan)
    body = {"actions": [action("a").model_dump(mode="json")]}
    for _ in range(2):  # distinct event ids, same proposal
        await runtime.bus.publish(
            Event(type=EventType.PLAN_PROPOSED, source="a3", incident_id="inc-1", payload=body)
        )
    assert len(events(runtime, "action_authorised")) == 1
    assert phases(runtime, "a") == ["authorised"]


# ------------------------------------------------------------------ human review


def test_edit_reruns_policy_and_runs_edited_params(gw):
    decision = gw.submit(action(action_type="create_mission", params={"resources_committed": 5}))
    assert decision.status is DecisionStatus.ESCALATED
    out = gw.edit(decision.approval_id, "cmdr", {"resources_committed": 1}, "smaller")
    assert out.status is DecisionStatus.AUTHORISED and out.reason == "human_edited"
    stored = ProposedAction.model_validate_json(gw._row("k1")["action"])
    assert stored.params == {"resources_committed": 1}
    assert gw.approvals.get(decision.approval_id).status is ApprovalStatus.EDITED


def test_edit_cannot_smuggle_an_invalid_change(gw):
    decision = gw.submit(action(action_type="cancel_mission"))
    out = gw.edit(decision.approval_id, "cmdr", {"x": float("nan")})
    assert out.status is DecisionStatus.REJECTED and out.reason == "edit_invalid_params"


def test_reject_is_terminal_and_blocks_execution(gw, runtime):
    decision = gw.submit(action(action_type="cancel_mission"))
    out = gw.reject(decision.approval_id, "cmdr", "no")
    assert out.status is DecisionStatus.REJECTED
    assert phases(runtime, "k1") == ["escalated", "rejected"]
    with pytest.raises(ApprovalError, match="already rejected"):
        gw.approve(decision.approval_id, "cmdr")


def test_double_resolution_is_refused(gw):
    decision = gw.submit(action(action_type="cancel_mission"))
    gw.approve(decision.approval_id, "cmdr")
    with pytest.raises(ApprovalError):
        gw.reject(decision.approval_id, "cmdr")
    with pytest.raises(ApprovalError):
        gw.approve(decision.approval_id, "cmdr")


def test_reviewer_is_required_and_unknown_approval_rejected(gw):
    decision = gw.submit(action(action_type="cancel_mission"))
    with pytest.raises(ApprovalError, match="reviewer"):
        gw.approve(decision.approval_id, "  ")
    assert gw.approvals.get(decision.approval_id).status is ApprovalStatus.PENDING
    with pytest.raises(ApprovalError, match="unknown"):
        gw.approve("ap-nope", "cmdr")


# -------------------------------------------------- execute / verify / retry / compensate


async def test_successful_execution_writes_receipt_and_publishes_events(gw, runtime):
    gw.register_executor("a8_dispatch", FakeExecutor())
    gw.submit(action())
    assert await gw.run_pending() == 1
    assert gw.get("k1").status is DecisionStatus.EXECUTED
    assert phases(runtime, "k1") == ["authorised", "attempt:1", "executed"]
    assert len(events(runtime, "mission_status")) == 1
    assert await gw.run_pending() == 0  # nothing runs twice


async def test_transient_failure_is_retried(gw, runtime):
    executor = FakeExecutor(fail_times=2)
    gw.register_executor("a8_dispatch", executor)
    gw.submit(action())
    await gw.run_pending()
    assert executor.calls == 3 and gw.get("k1").status is DecisionStatus.EXECUTED
    assert phases(runtime, "k1").count("attempt:1") == 1


async def test_exhausted_retries_compensate(gw, runtime):
    executor = FakeExecutor(fail_times=99)
    gw.register_executor("a8_dispatch", executor)
    gw.submit(action())
    await gw.run_pending()
    assert executor.calls == 3  # 1 try + max_retries(2)
    assert executor.compensated == ["k1"]
    assert gw.get("k1").status is DecisionStatus.COMPENSATED
    assert phases(runtime, "k1")[-1] == "compensated"
    assert len(events(runtime, "action_blocked")) == 1  # A3 is told, so it can replan


async def test_failed_verification_is_treated_as_failure(gw):
    executor = FakeExecutor(verify_ok=False)
    gw.register_executor("a8_dispatch", executor)
    gw.submit(action())
    await gw.run_pending()
    assert gw.get("k1").status is DecisionStatus.COMPENSATED and executor.calls == 3


async def test_failed_compensation_is_flagged_for_a_human(gw, runtime):
    gw.register_executor("a8_dispatch", FakeExecutor(fail_times=99, compensate_fails=True))
    gw.submit(action())
    await gw.run_pending()
    assert gw.get("k1").status is DecisionStatus.FAILED_UNCOMPENSATED
    assert phases(runtime, "k1")[-1] == "compensation_failed"


async def test_interrupted_execution_resumes(gw):
    gw.submit(action())
    stored = ProposedAction.model_validate_json(gw._row("k1")["action"])
    gw._transition(
        "k1", DecisionStatus.EXECUTING, "attempt_1", phase="attempt:1", action=stored, unique=True
    )  # simulate a crash mid-flight
    executor = FakeExecutor()
    gw.register_executor("a8_dispatch", executor)
    await gw.run_pending()
    assert gw.get("k1").status is DecisionStatus.EXECUTED


async def test_missing_executor_leaves_action_authorised(gw):
    gw.submit(action())
    assert await gw.run_pending() == 0
    assert gw.get("k1").status is DecisionStatus.AUTHORISED
