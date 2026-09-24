import pytest

from atlas.control.policy_engine import Policy, PolicyEngine
from atlas.control.wiring import attach_control
from atlas.core.events import ProposedAction


def fast_policy(max_retries=3):
    """The real policy with no retry sleeping, so failure paths stay quick."""
    policy = Policy.load()
    gateway = policy.gateway.model_copy(
        update={"retry_backoff_seconds": 0, "max_retries": max_retries}
    )
    return PolicyEngine(policy.model_copy(update={"gateway": gateway}))


@pytest.fixture
def control(runtime):
    return attach_control(runtime, policy=fast_policy())


def proposal(key, executor, action_type, params=None, incident="inc-1", **kw):
    return ProposedAction(
        idempotency_key=key,
        incident_id=incident,
        proposed_by="a3_incident_commander",
        executor=executor,
        action_type=action_type,
        params=params or {},
        **kw,
    )
