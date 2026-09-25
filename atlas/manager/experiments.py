"""Experiments: is a change better, on a disaster we already know?  (Owner: Dinuka)

Replays a
recorded scenario (a real flood, say) in a throwaway sandbox twice, once as recorded and once with a
change (a lost sensor, a human who rejects, a stricter policy, a different replan bound), scores both
with the same evaluators, and shows what moved.
"""

from __future__ import annotations

import asyncio
from typing import Any

from atlas.dashboard.dev import playground as pg
from atlas.manager.evaluation.base import Mode
from atlas.manager.evaluation.runner import _select, evaluate


def _inspect(evaluators: list[str] | None):
    def run(runtime, system):
        result = evaluate(
            system, None, evaluators=evaluators, mode=Mode.EXPERIMENT, trigger="experiment"
        )
        summary = result["summary"]
        failures = [
            {
                "evaluator": s.evaluator,
                "level": s.level.value,
                "detail": s.detail,
                "critical": s.critical,
            }
            for s in result["scores"]
            if s.passed is False
        ][:25]
        return {
            "summary": summary.model_dump(mode="json"),
            "failures": failures,
            "traces": result["traces"],
        }

    return run


def run_experiment(
    scenario: dict, variant: pg.Variant, evaluators: list[str] | None = None
) -> dict[str, Any]:
    """Baseline and variant, both scored. Drives its own event loops: call it from a worker thread."""
    _select(evaluators, Mode.EXPERIMENT)  # fail early on an unknown evaluator
    inspect = _inspect(evaluators)
    baseline = asyncio.run(pg._run_story(scenario, pg.Variant(label="as recorded"), inspect))
    changed = asyncio.run(pg._run_story(scenario, variant, inspect))
    names = sorted(
        set(baseline["inspection"]["summary"]["evaluators"])
        | set(changed["inspection"]["summary"]["evaluators"])
    )
    table = []
    for name in names:
        a = baseline["inspection"]["summary"]["evaluators"].get(name, {})
        b = changed["inspection"]["summary"]["evaluators"].get(name, {})
        table.append(
            {
                "evaluator": name,
                "critical": a.get("critical") or b.get("critical"),
                "baseline_pass_rate": a.get("pass_rate"),
                "variant_pass_rate": b.get("pass_rate"),
                "regressed": (a.get("pass_rate") or 0) > (b.get("pass_rate") or 0),
            }
        )
    return {
        "scenario": scenario.get("name"),
        "variant": variant.model_dump(mode="json"),
        "baseline": {
            "outcome": {k: v for k, v in baseline.items() if k != "inspection"},
            **baseline["inspection"],
        },
        "changed": {
            "outcome": {k: v for k, v in changed.items() if k != "inspection"},
            **changed["inspection"],
        },
        "evaluators": table,
        "regressions": [r["evaluator"] for r in table if r["regressed"]],
        "critical_regressions": [r["evaluator"] for r in table if r["regressed"] and r["critical"]],
    }
