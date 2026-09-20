"""The evaluation CLI: the proposal-table gate, real-network reports, latency, and JSON output."""

import json

import pytest

from eval import metrics, pipeline, run_abc


def test_the_grid_run_reproduces_the_proposal_and_exits_zero(capsys):
    assert run_abc.main([]) == 0
    out = capsys.readouterr().out
    assert "24/24 numbers reproduced" in out
    assert "scenario B: 4/4 targets met" in out and "scenario C: 4/4 targets met" in out
    assert "NOT REPRODUCED" not in out


def test_the_gate_fails_loudly_when_a_published_number_stops_reproducing(capsys, monkeypatch):
    table = dict(metrics.PROPOSAL_TABLE)
    table[("B", "Cognitive")] = metrics.Row(0.90, 1.0, 18.6, 32.2)  # pretend the proposal said 90%
    monkeypatch.setattr(metrics, "PROPOSAL_TABLE", table)
    assert run_abc.main([]) == 1
    out = capsys.readouterr().out
    assert "NOT REPRODUCED: B/Cognitive success_rate" in out
    assert "23/24 numbers reproduced" in out


def test_a_non_standard_run_count_skips_the_gate_because_the_table_is_for_20_runs(capsys):
    assert run_abc.main(["--runs", "3"]) == 0
    out = capsys.readouterr().out
    assert "skipped" in out and "numbers reproduced" not in out


def test_a_real_network_lists_the_belief_agent_under_both_uphill_settings(capsys):
    assert run_abc.main(["--site", "colombo_fort", "--runs", "2"]) == 0
    out = capsys.readouterr().out
    assert "colombo_fort: real OSM network" in out and "elevation opentopodata:srtm30m" in out
    for label in ("Omniscient", run_abc.ORIGINAL, run_abc.ATLAS, "Reactive"):
        assert label in out
    for scenario in ("A: no disaster", "B: flood", "C: flood, high misinformation"):
        assert f"Scenario {scenario}" in out


def test_json_output_holds_every_number(tmp_path):
    target = tmp_path / "results.json"
    assert run_abc.main(["--runs", "2", "--json", str(target)]) == 0
    data = json.loads(target.read_text())
    assert set(data["grid"]) == {
        f"{s}/{a}" for s in "ABC" for a in ("Omniscient", "Cognitive", "Reactive")
    }
    assert 0 <= data["grid"]["B/Cognitive"]["success_rate"] <= 1


def test_pipeline_is_measured_once_and_labelled_a_floor(capsys, monkeypatch):
    calls = []

    def fake(repetitions, site="ratnapura"):
        calls.append(repetitions)
        stat = {"n": repetitions, "median_ms": 1.0, "p95_ms": 2.0, "max_ms": 3.0}
        return {stage: stat for stage in (*pipeline.STAGES, "end_to_end")}

    monkeypatch.setattr(run_abc, "measure_pipeline", fake)
    assert run_abc.main(["--pipeline", "--runs", "5"]) == 0
    out = capsys.readouterr().out
    assert calls == [5], "the measurement must run once, not once to compute and once to print"
    flat = " ".join(out.split())  # the message wraps across lines
    assert "end_to_end" in flat and "a floor for that target, not a measurement of it" in flat


def test_an_undefined_total_evacuation_time_prints_as_n_a():
    row = run_abc._row(
        "x",
        {
            "success_rate": 0.6,
            "avg_failed_routes": 18.0,
            "avg_travel_time": 2.0,
            "avg_total_evac_time": 0.0,
        },
    )
    assert "n/a" in row and "0.00" not in row.split("total evac")[1]


def test_an_unknown_site_is_rejected_by_the_parser():
    with pytest.raises(SystemExit):
        run_abc.main(["--site", "atlantis"])
