"""The ported engine must reproduce the original repo's results episode-for-episode.

tests/fixtures/old_engine_results.json is `results.json` from EvacuationAgent-using-Cognitivity
(20 seeded episodes x 3 scenarios, 10 agents per type). Its aggregates are the numbers quoted in
the project proposal, so passing this test means the proposal's evidence carries over to ATLAS.
"""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from eval.episodes import aggregate, run_episode, run_experiment

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "old_engine_results.json"
OLD = json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("scenario", ["A", "B", "C"])
def test_every_episode_matches_the_original(scenario):
    assert len(OLD[scenario]) == 20
    for expected in OLD[scenario]:
        got = asdict(run_episode(scenario, expected["run_id"], seed=1000 + expected["run_id"]))
        assert len(got["agents"]) == len(expected["agents"]) == 30
        for g, e in zip(got["agents"], expected["agents"]):
            assert g["agent_type"] == e["agent_type"]
            assert g["agent_id"] == e["agent_id"]
            assert g["reached"] == e["reached"]
            assert g["failed_routes"] == e["failed_routes"]
            assert g["risky_edges_hit"] == e["risky_edges_hit"]
            assert g["steps_taken"] == e["steps_taken"]
            assert g["total_travel_time"] == pytest.approx(e["total_travel_time"], rel=1e-12)
        for kind, value in expected["evacuation_time_by_type"].items():
            actual = got["evacuation_time_by_type"][kind]
            if value is None:
                assert actual is None
            else:
                assert actual == pytest.approx(value, rel=1e-12)


# Proposal section 7.1 table (Scenarios B and C) and the "already achieved" column of 8.1.
PROPOSAL = {
    ("B", "Cognitive"): (0.960, 1.0, 18.6, 32.2),
    ("B", "Omniscient"): (0.950, 0.1, 18.4, 31.7),
    ("B", "Reactive"): (0.890, 2.2, 17.2, 25.2),
    ("C", "Cognitive"): (0.960, 1.0, 18.6, 32.2),
    ("C", "Omniscient"): (0.960, 0.1, 18.6, 32.2),
    ("C", "Reactive"): (0.925, 1.2, 18.0, 31.1),
}


@pytest.mark.parametrize(("scenario", "agent_type"), sorted(PROPOSAL))
def test_reproduces_the_proposal_table(scenario, agent_type):
    summary = aggregate(run_experiment(scenario, 20), agent_type)
    success, failed, travel, evac = PROPOSAL[(scenario, agent_type)]
    assert summary["success_rate"] == pytest.approx(success, abs=0.0005)
    assert summary["avg_failed_routes"] == pytest.approx(failed, abs=0.05)
    assert summary["avg_travel_time"] == pytest.approx(travel, abs=0.05)
    assert summary["avg_total_evac_time"] == pytest.approx(evac, abs=0.05)
