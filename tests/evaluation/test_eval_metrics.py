"""Metrics: the proposal-table gate, the section 8.1 targets, and latency statistics."""

import pytest

from eval import metrics
from eval.metrics import PROPOSAL_TABLE, Row, compare_to_proposal, disaster_response_checks


def summaries_matching_the_proposal():
    return {
        key: {
            "success_rate": row.success_rate,
            "avg_failed_routes": row.avg_failed_routes,
            "avg_travel_time": row.avg_travel_time,
            "avg_total_evac_time": row.avg_total_evac_time,
        }
        for key, row in PROPOSAL_TABLE.items()
    }


def test_the_proposal_table_is_the_six_published_rows():
    assert sorted(PROPOSAL_TABLE) == [
        ("B", "Cognitive"),
        ("B", "Omniscient"),
        ("B", "Reactive"),
        ("C", "Cognitive"),
        ("C", "Omniscient"),
        ("C", "Reactive"),
    ]
    assert PROPOSAL_TABLE[("B", "Cognitive")] == Row(0.960, 1.0, 18.6, 32.2)


def test_exact_published_numbers_all_pass_the_gate():
    checks = compare_to_proposal(summaries_matching_the_proposal())
    assert len(checks) == 6 * 4 and all(c.passed for c in checks)


@pytest.mark.parametrize(
    ("field", "wrong"),
    [
        ("success_rate", 0.95),
        ("avg_failed_routes", 1.2),
        ("avg_travel_time", 18.8),
        ("avg_total_evac_time", 32.5),
    ],
)
def test_a_number_that_drifts_beyond_the_published_digits_fails_the_gate(field, wrong):
    summaries = summaries_matching_the_proposal()
    summaries[("B", "Cognitive")][field] = wrong
    failed = [c for c in compare_to_proposal(summaries) if not c.passed]
    assert [c.name for c in failed] == [f"B/Cognitive {field}"]


def test_rounding_within_the_last_published_digit_still_passes():
    summaries = summaries_matching_the_proposal()
    summaries[("B", "Cognitive")]["avg_travel_time"] = 18.6 + 0.049
    summaries[("B", "Cognitive")]["success_rate"] = 0.9604
    assert all(c.passed for c in compare_to_proposal(summaries))


def by_agent(**cognitive):
    omniscient = {
        "success_rate": 1.0,
        "avg_failed_routes": 0.1,
        "avg_travel_time": 10.0,
        "avg_total_evac_time": 20.0,
    }
    return {"Omniscient": omniscient, "Cognitive": {**omniscient, **cognitive}}


def test_meeting_every_section_8_1_target():
    checks = disaster_response_checks(by_agent(avg_failed_routes=1.0, avg_travel_time=10.9))
    assert len(checks) == 4 and all(c.passed for c in checks)


@pytest.mark.parametrize(
    ("change", "missed"),
    [
        ({"success_rate": 0.89}, "evacuation success rate"),
        ({"avg_failed_routes": 1.5}, "average failed route attempts"),
        ({"avg_travel_time": 11.1}, "average travel time vs perfect information"),
        ({"avg_total_evac_time": 17.9}, "total evacuation time vs perfect information"),
    ],
)
def test_each_target_can_be_missed_on_its_own(change, missed):
    checks = disaster_response_checks(by_agent(**change))
    assert [c.name for c in checks if not c.passed] == [missed]


def test_the_boundaries_are_inclusive_where_the_proposal_says_at_least_or_within():
    assert disaster_response_checks(by_agent(success_rate=0.90))[0].passed  # >= 90%
    assert not disaster_response_checks(by_agent(avg_failed_routes=1.5))[1].passed  # < 1.5
    assert disaster_response_checks(by_agent(avg_travel_time=11.0))[2].passed  # within 10%


def test_an_undefined_total_evacuation_time_counts_as_a_miss_not_a_pass():
    """No episode had everyone arrive: aggregate reports 0, which must not look like a fast evacuation."""
    checks = disaster_response_checks(by_agent(avg_total_evac_time=0.0))
    assert not checks[3].passed


def test_a_zero_baseline_needs_a_zero_result():
    base = {
        "success_rate": 1.0,
        "avg_failed_routes": 0.0,
        "avg_travel_time": 0.0,
        "avg_total_evac_time": 0.0,
    }
    assert all(c.passed for c in disaster_response_checks({"Omniscient": base, "Cognitive": base}))
    worse = {**base, "avg_travel_time": 0.5}
    checks = disaster_response_checks({"Omniscient": base, "Cognitive": worse})
    assert not checks[2].passed


def test_latency_statistics():
    stats = metrics.latency_stats([5.0, 1.0, 3.0, 2.0, 4.0])
    assert stats == {"n": 5, "median_ms": 3.0, "p95_ms": 5.0, "max_ms": 5.0}
    one = metrics.latency_stats([7.5])
    assert (one["median_ms"], one["p95_ms"], one["max_ms"]) == (7.5, 7.5, 7.5)
    spread = metrics.latency_stats([float(i) for i in range(1, 101)])
    assert spread["median_ms"] == 50.5 and 94 <= spread["p95_ms"] <= 96 and spread["max_ms"] == 100
    with pytest.raises(ValueError):
        metrics.latency_stats([])
