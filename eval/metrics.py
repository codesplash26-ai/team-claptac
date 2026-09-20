"""Disaster-response and agentic metrics from proposal section 8, and the published results table.

`PROPOSAL_TABLE` is the table in section 7.1 (synthetic grids, scenarios B and C). It is the
regression gate: `python -m eval.run_abc` fails if the ported engine stops reproducing it.

`disaster_response_checks` applies the section 8.1 targets for the full system to any set of
results, so the same yardstick is used on the grids and on the real networks. Targets are ATLAS's
goals, not claims: a check can, and on some real networks does, fail.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class Row:
    success_rate: float
    avg_failed_routes: float
    avg_travel_time: float
    avg_total_evac_time: float


# Proposal section 7.1: synthetic stress grid, 20 seeded runs x 10 agents per type.
PROPOSAL_TABLE: dict[tuple[str, str], Row] = {
    ("B", "Cognitive"): Row(0.960, 1.0, 18.6, 32.2),
    ("B", "Omniscient"): Row(0.950, 0.1, 18.4, 31.7),
    ("B", "Reactive"): Row(0.890, 2.2, 17.2, 25.2),
    ("C", "Cognitive"): Row(0.960, 1.0, 18.6, 32.2),
    ("C", "Omniscient"): Row(0.960, 0.1, 18.6, 32.2),
    ("C", "Reactive"): Row(0.925, 1.2, 18.0, 31.1),
}
# Half a unit in the last published digit.
TOLERANCE = Row(0.0005, 0.05, 0.05, 0.05)

# Proposal section 8.1 targets for the full system.
TARGET_SUCCESS = 0.90
TARGET_FAILED_ROUTES = 1.5
TARGET_WITHIN_BASELINE = 0.10  # travel and total evacuation time within 10% of perfect information


@dataclass(frozen=True)
class Check:
    name: str
    value: float
    target: str
    passed: bool


def compare_to_proposal(summaries: dict[tuple[str, str], dict]) -> list[Check]:
    """One check per published number: did we reproduce it, to the digits published?"""
    checks = []
    for (scenario, agent), expected in sorted(PROPOSAL_TABLE.items()):
        got = summaries[(scenario, agent)]
        for field in (
            "success_rate",
            "avg_failed_routes",
            "avg_travel_time",
            "avg_total_evac_time",
        ):
            want, tol = getattr(expected, field), getattr(TOLERANCE, field)
            value = got[field]
            checks.append(
                Check(
                    f"{scenario}/{agent} {field}",
                    value,
                    f"{want} ± {tol}",
                    abs(value - want) <= tol,
                )
            )
    return checks


def disaster_response_checks(by_agent: dict[str, dict], agent: str = "Cognitive") -> list[Check]:
    """Section 8.1 targets for `agent`, judged against the Omniscient (perfect-information) baseline."""
    mine, base = by_agent[agent], by_agent["Omniscient"]

    def within(field):
        if not base[field]:
            return mine[field] == 0
        return abs(mine[field] - base[field]) / base[field] <= TARGET_WITHIN_BASELINE

    return [
        Check(
            "evacuation success rate",
            mine["success_rate"],
            f">= {TARGET_SUCCESS}",
            mine["success_rate"] >= TARGET_SUCCESS,
        ),
        Check(
            "average failed route attempts",
            mine["avg_failed_routes"],
            f"< {TARGET_FAILED_ROUTES}",
            mine["avg_failed_routes"] < TARGET_FAILED_ROUTES,
        ),
        Check(
            "average travel time vs perfect information",
            mine["avg_travel_time"],
            f"within {TARGET_WITHIN_BASELINE:.0%} of {base['avg_travel_time']:.1f}",
            within("avg_travel_time"),
        ),
        Check(
            "total evacuation time vs perfect information",
            mine["avg_total_evac_time"],
            f"within {TARGET_WITHIN_BASELINE:.0%} of {base['avg_total_evac_time']:.1f}",
            within("avg_total_evac_time"),
        ),
    ]


def latency_stats(samples_ms: list[float]) -> dict[str, float]:
    """Median, 95th percentile and worst case of a list of timings (milliseconds)."""
    if not samples_ms:
        raise ValueError("no samples")
    ordered = sorted(samples_ms)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return {
        "n": len(ordered),
        "median_ms": statistics.median(ordered),
        "p95_ms": p95,
        "max_ms": ordered[-1],
    }
