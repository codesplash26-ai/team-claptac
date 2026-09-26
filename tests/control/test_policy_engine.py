"""Policy engine and red-team matrix: every out-of-scope action must be blocked."""

import itertools

import pytest
import yaml
from pydantic import ValidationError

from atlas.control.models import Verdict
from atlas.control.policy_engine import Policy, PolicyEngine
from atlas.core.events import Consequence, ProposedAction

ENGINE = PolicyEngine.from_file()
A8 = ["create_mission", "update_mission", "cancel_mission", "reroute_group", "close_major_route"]
A9 = ["publish_alert", "publish_sitrep", "cancel_alert"]


def act(
    executor="a8_dispatch",
    action_type="update_mission",
    params=None,
    proposer="a3_incident_commander",
    key="k1",
):
    return ProposedAction(
        idempotency_key=key,
        incident_id="inc-1",
        proposed_by=proposer,
        executor=executor,
        action_type=action_type,
        params=params or {},
    )


def test_low_consequence_is_allowed():
    verdict = ENGINE.evaluate(act(action_type="update_mission"))
    assert verdict.verdict is Verdict.ALLOW and verdict.consequence is Consequence.LOW


@pytest.mark.parametrize("action_type", ["cancel_mission", "close_major_route"])
def test_always_high_actions_escalate(action_type):
    assert ENGINE.evaluate(act(action_type=action_type)).verdict is Verdict.ESCALATE


@pytest.mark.parametrize(
    "committed,expected",
    [
        (0, Verdict.ALLOW),
        (2, Verdict.ALLOW),
        (3, Verdict.ESCALATE),
        (100, Verdict.ESCALATE),
        (None, Verdict.ESCALATE),
        ("1", Verdict.ESCALATE),
        (True, Verdict.ESCALATE),
        (float("nan"), Verdict.ESCALATE),
    ],
)
def test_resource_threshold_fails_closed(committed, expected):
    params = {} if committed is None else {"resources_committed": committed}
    assert ENGINE.evaluate(act(action_type="create_mission", params=params)).verdict is expected


@pytest.mark.parametrize(
    "params,expected",
    [
        ({"scope": "local", "instruction": "avoid_area"}, Verdict.ALLOW),  # local advisory only
        ({"scope": "local", "instruction": "evacuate"}, Verdict.ESCALATE),  # every evacuation order
        ({"scope": "district", "instruction": "avoid_area"}, Verdict.ESCALATE),
        (
            {"scope": "local", "instruction": "avoid_area", "instruction_text": "Go now"},
            Verdict.ESCALATE,
        ),
        ({"scope": "local"}, Verdict.ESCALATE),  # missing instruction fails closed
        ({}, Verdict.ESCALATE),
        ({"scope": "national", "instruction": "avoid_area"}, Verdict.ESCALATE),
    ],
)
def test_alerts_go_to_a_human_unless_a_local_advisory(params, expected):
    action = act("a9_comms", "publish_alert", params)
    assert ENGINE.evaluate(action).verdict is expected


@pytest.mark.parametrize(
    "executor,action_type",
    [
        (e, a)
        for e, allowed in (("a8_dispatch", A8), ("a9_comms", A9))
        for a in (A8 + A9)
        if a not in allowed
    ],
)
def test_red_team_cross_role_actions_are_blocked(executor, action_type):
    verdict = ENGINE.evaluate(act(executor, action_type))
    assert verdict.verdict is Verdict.BLOCK
    assert verdict.reason == "action_not_permitted_for_executor"


@pytest.mark.parametrize(
    "executor,action_type,proposer,reason",
    [
        ("a8_dispatch", "delete_everything", "a3_incident_commander", "unknown_action_type"),
        ("a8_dispatch", "", "a3_incident_commander", "unknown_action_type"),
        ("a8_dispatch", "UPDATE_MISSION", "a3_incident_commander", "unknown_action_type"),
        ("a8_dispatch", "update_mission ", "a3_incident_commander", "unknown_action_type"),
        ("a4_evacuation", "update_mission", "a3_incident_commander", "unknown_executor"),
        ("a3_incident_commander", "update_mission", "a3_incident_commander", "unknown_executor"),
        ("", "update_mission", "a3_incident_commander", "unknown_executor"),
        ("a8_dispatch", "update_mission", "a4_evacuation", "unauthorised_proposer"),
        ("a8_dispatch", "update_mission", "a8_dispatch", "unauthorised_proposer"),
        ("a9_comms", "publish_alert", "human", "unauthorised_proposer"),
        ("a8_dispatch", "update_mission", "", "unauthorised_proposer"),
    ],
)
def test_red_team_invalid_identity_or_action_is_blocked(executor, action_type, proposer, reason):
    verdict = ENGINE.evaluate(act(executor, action_type, proposer=proposer))
    assert (verdict.verdict, verdict.reason) == (Verdict.BLOCK, reason)


def test_every_declared_agent_action_pair_is_classified():
    """No allowed pair may be left unclassified; every other pair must block."""
    for executor, action_type in itertools.product(["a8_dispatch", "a9_comms"], A8 + A9):
        verdict = ENGINE.evaluate(act(executor, action_type, {"scope": "district"}))
        permitted = action_type in ENGINE.policy.agents[executor].allowed_actions
        assert (verdict.verdict is not Verdict.BLOCK) is permitted


def test_policy_rejects_unknown_condition(tmp_path):
    data = yaml.safe_load(open("config/policy.yaml"))
    data["actions"]["cancel_alert"]["when"] = {"made_up_condition": 1}
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError, match="unknown policy condition"):
        Policy.load(path)


def test_policy_rejects_agent_allowing_undeclared_action(tmp_path):
    data = yaml.safe_load(open("config/policy.yaml"))
    data["agents"]["a8_dispatch"]["allowed_actions"].append("undeclared")
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError, match="undeclared"):
        Policy.load(path)


def test_policy_rejects_unknown_top_level_keys(tmp_path):
    data = yaml.safe_load(open("config/policy.yaml"))
    data["typo_section"] = {}
    path = tmp_path / "p.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValidationError):
        Policy.load(path)
