"""Compute-latency measurement of the routing agents on the real network."""

import pytest

from eval.pipeline import REPLAN_TARGET_SECONDS, STAGES, measure_pipeline


def test_every_stage_is_measured_with_sane_statistics():
    report = measure_pipeline(repetitions=2)
    assert list(report) == [*STAGES, "end_to_end"]
    for stage, stats in report.items():
        assert stats["n"] == 2, stage  # the warm-up run is dropped
        assert 0 < stats["median_ms"] <= stats["p95_ms"] <= stats["max_ms"], stage


def test_the_end_to_end_time_is_the_sum_of_the_stages_and_far_below_the_target():
    report = measure_pipeline(repetitions=2)
    parts = sum(report[stage]["median_ms"] for stage in STAGES)
    assert report["end_to_end"]["median_ms"] == pytest.approx(parts, rel=0.5)
    assert report["end_to_end"]["max_ms"] / 1000.0 < REPLAN_TARGET_SECONDS  # a floor, not the whole


def test_at_least_one_repetition_is_required():
    with pytest.raises(ValueError, match="at least 1"):
        measure_pipeline(repetitions=0)
