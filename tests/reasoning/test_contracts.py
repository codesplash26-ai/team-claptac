from datetime import datetime, timedelta, timezone

import pytest

from atlas.agents.planning.commander import CommanderAdapter
from atlas.core.config import planner_config_for
from atlas.reasoning import Planner, PlanningRequest


async def test_stale_planning_result_must_be_replanned():
    result = await Planner().plan(
        PlanningRequest(agent="a3", state={"routes": {}, "people": 20}, snapshot_version=4),
        CommanderAdapter(),
    )
    result.require_current(4)
    with pytest.raises(ValueError, match="stale"):
        result.require_current(5)


def test_agent_overrides_are_available_to_teammates():
    assert planner_config_for("a1_sentinel").mode == "scenario"
    assert planner_config_for("a3_incident_commander").objective == "lower_tail"
    assert planner_config_for("a4_evacuation").mode == "mcts"


async def test_expired_evidence_invalidates_unchanged_version():
    expires = datetime.now(timezone.utc) + timedelta(seconds=1)
    result = await Planner().plan(
        PlanningRequest(
            agent="a3", state={"routes": {}, "people": 20}, snapshot_version=4, valid_until=expires
        ),
        CommanderAdapter(),
    )
    result.require_current(4, now=expires - timedelta(seconds=1))
    with pytest.raises(ValueError, match="expired"):
        result.require_current(4, now=expires)
