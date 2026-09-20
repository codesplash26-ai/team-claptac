"""A/B/C scenario evaluation: normal, flood, high-misinformation, against the two baselines.

    python -m eval.run_abc                      reproduce the proposal's table on the synthetic grids
    python -m eval.run_abc --site ratnapura     a real network (colombo_fort, kandy, ratnapura, all)
    python -m eval.run_abc --pipeline           compute latency of A4/A6/A7 on the real network
    python -m eval.run_abc --json results.json  also write every number to a file

Scenarios: A no disaster, B flood, C flood with high misinformation. Agents: Omniscient (perfect
information), Cognitive (the belief-based engine A4 wraps) and Reactive (naive: trusts every report).

The grid run is a regression gate: it exits 1 if the ported engine no longer reproduces the proposal's
table (20 runs only). Real-network runs are reported, not gated: they are new results, and they show
the engine's original uphill penalty failing on hilly terrain (see docs/routing-integration.md), so
each real network lists the Cognitive agent twice, with the original penalty and with ATLAS's setting.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from atlas.tools import geo
from atlas.tools.cognitive_map.routing import SLOPE_PENALTY_PER_M
from eval import metrics
from eval.episodes import aggregate, run_experiment
from eval.networks import SCENARIOS, run_network_experiment
from eval.pipeline import REPLAN_TARGET_SECONDS, measure_pipeline

GRID_RUNS = 20  # the proposal's table is defined for this many seeded runs
ATLAS_SLOPE_PENALTY = 0.0  # RoutingConfig default; see atlas/tools/cognitive_map/cop_bridge.py
ORIGINAL = "Cognitive (original engine, uphill penalty 0.10)"
ATLAS = "Cognitive (ATLAS, uphill penalty 0)"


def grid_summaries(runs: int = GRID_RUNS) -> dict[tuple[str, str], dict]:
    return {
        (scenario, agent): aggregate(episodes, agent)
        for scenario in ("A", "B", "C")
        for episodes in [run_experiment(scenario, runs)]
        for agent in ("Omniscient", "Cognitive", "Reactive")
    }


def network_summaries(site: str, runs: int) -> dict[str, dict[str, dict]]:
    """{scenario: {row label: summary}}.

    The baselines do not use the uphill penalty, so their rows come from the "original" run. All
    agent types share one world and one random stream, so the ATLAS row was drawn from a slightly
    different stream than the baselines it is compared with; differences of a few hundredths in
    failed routes between rows are noise, not signal.
    """
    out: dict[str, dict[str, dict]] = {}
    for scenario in SCENARIOS:
        original = run_network_experiment(
            site, scenario, runs, slope_penalty_per_m=SLOPE_PENALTY_PER_M
        )
        atlas = run_network_experiment(
            site, scenario, runs, slope_penalty_per_m=ATLAS_SLOPE_PENALTY
        )
        out[scenario] = {
            "Omniscient": aggregate(original, "Omniscient"),
            ORIGINAL: aggregate(original, "Cognitive"),
            ATLAS: aggregate(atlas, "Cognitive"),
            "Reactive": aggregate(original, "Reactive"),
        }
    return out


def _row(label: str, s: dict) -> str:
    # `aggregate` reports 0 when no episode had every agent arrive; that is "not defined", not 0 minutes.
    evac = f"{s['avg_total_evac_time']:6.2f}" if s["avg_total_evac_time"] else "   n/a"
    return (
        f"  {label:<49s} | success {s['success_rate']:6.1%} | failed routes "
        f"{s['avg_failed_routes']:5.2f} | travel {s['avg_travel_time']:6.2f} | total evac {evac}"
    )


def _print_checks(title: str, checks: list[metrics.Check]) -> None:
    passed = sum(c.passed for c in checks)
    print(f"  {title}: {passed}/{len(checks)} targets met")
    for c in checks:
        if not c.passed:
            print(f"      missed: {c.name} = {c.value:.3g} (target {c.target})")


def report_grid(runs: int, summaries: dict, gate: bool) -> bool:
    print(f"\n### Synthetic stress grid, {runs} seeded runs x 10 agents per type")
    for scenario in ("A", "B", "C"):
        print(f"\nScenario {scenario}: {SCENARIOS[scenario]}")
        for agent in ("Omniscient", "Cognitive", "Reactive"):
            print(_row(agent, summaries[(scenario, agent)]))
    ok = True
    if gate:
        checks = metrics.compare_to_proposal(summaries)
        misses = [c for c in checks if not c.passed]
        print(
            f"\nProposal table (section 7.1): {len(checks) - len(misses)}/{len(checks)} numbers reproduced"
        )
        for c in misses:
            print(f"  NOT REPRODUCED: {c.name} = {c.value:.4g} (proposal {c.target})")
        ok = not misses
    else:
        print(f"\n(The proposal-table gate applies to --runs {GRID_RUNS}; skipped.)")
    print("\nSection 8.1 targets, belief agent vs perfect information (flood scenarios):")
    for scenario in ("B", "C"):
        by_agent = {a: summaries[(scenario, a)] for a in ("Omniscient", "Cognitive", "Reactive")}
        _print_checks(f"scenario {scenario}", metrics.disaster_response_checks(by_agent))
    return ok


def report_network(site: str, runs: int, summaries: dict) -> None:
    rg = geo.load_site(site)
    print(
        f"\n### {site}: real OSM network ({rg.graph.number_of_nodes()} nodes, "
        f"{rg.graph.number_of_edges()} roads, elevation {rg.elevation_source}), "
        f"{runs} seeded runs x 10 agents per type"
    )
    for scenario, rows in summaries.items():
        print(f"\nScenario {scenario}: {SCENARIOS[scenario]}")
        for label, s in rows.items():
            print(_row(label, s))
        if scenario != "A":
            for label in (ORIGINAL, ATLAS):
                by_agent = {"Omniscient": rows["Omniscient"], "Cognitive": rows[label]}
                _print_checks(f"{label}", metrics.disaster_response_checks(by_agent))


def report_pipeline(report: dict, repetitions: int) -> None:
    print(f"\n### Compute latency on Ratnapura, {repetitions} runs (warm-up dropped)")
    for stage, s in report.items():
        print(
            f"  {stage:<20s} median {s['median_ms']:7.1f} ms | p95 {s['p95_ms']:7.1f} ms | "
            f"max {s['max_ms']:7.1f} ms"
        )
    worst = report["end_to_end"]["max_ms"] / 1000.0
    print(
        f"\n  worst end-to-end compute {worst:.3f} s vs the {REPLAN_TARGET_SECONDS:.0f} s replanning target."
        "\n  This excludes A1 detection, A3's LLM, the human approval gate and any delivery: it is a"
        "\n  floor for that target, not a measurement of it."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--site", choices=[*sorted(geo.SITES), "all"], help="real network to evaluate"
    )
    parser.add_argument("--runs", type=int, default=GRID_RUNS, help="seeded runs per scenario")
    parser.add_argument("--pipeline", action="store_true", help="measure compute latency")
    parser.add_argument("--json", type=Path, help="write all results to this file")
    args = parser.parse_args(argv)

    payload: dict = {}
    ok = True
    only_extras = args.site or args.pipeline
    if not only_extras:
        summaries = grid_summaries(args.runs)
        ok = report_grid(args.runs, summaries, gate=args.runs == GRID_RUNS)
        payload["grid"] = {f"{s}/{a}": v for (s, a), v in summaries.items()}
    if args.site:
        for site in sorted(geo.SITES) if args.site == "all" else [args.site]:
            summaries = network_summaries(site, args.runs)
            report_network(site, args.runs, summaries)
            payload.setdefault("networks", {})[site] = summaries
    if args.pipeline:
        repetitions = max(3, min(args.runs, 20))
        report = measure_pipeline(repetitions)
        report_pipeline(report, repetitions)
        payload["pipeline"] = report
    if args.json:
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
