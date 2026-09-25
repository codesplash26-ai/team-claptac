"""Seeded disruptions produce a frozen input schedule, independent of execution timing."""

import hashlib
import random

from atlas.benchmark.schema import DuplicateStep, ReportStep, SensorStep


def materialize(task, seed, random_enabled=True):
    mixed = int.from_bytes(hashlib.sha256(f"{task.id}:{seed}".encode()).digest()[:8], "big")
    rng = random.Random(mixed)
    steps = list(task.steps)
    if random_enabled:
        cfg = task.random_events
        for i in range(cfg.count):
            at = rng.randint(cfg.start_seconds, cfg.end_seconds)
            kind = rng.choice(cfg.kinds)
            base = {"id": f"random-{i:02}", "at_seconds": at, "note": f"Seeded disruption: {kind}"}
            earlier = [
                s
                for s in task.steps
                if isinstance(s, (SensorStep, ReportStep)) and s.at_seconds < at
            ]
            if kind == "duplicate" and earlier:
                steps.append(DuplicateStep(**base, target=rng.choice(earlier).id))
            elif kind == "late_reading":
                steps.append(
                    SensorStep(
                        **base,
                        value=round(rng.uniform(28, 40), 2),
                        observed_seconds=at - rng.randint(360, 600),
                        node_id="delayed-node",
                    )
                )
            elif kind == "field_report":
                steps.append(
                    ReportStep(
                        **base,
                        value=round(rng.uniform(3, 12), 2),
                        text="Simulated unverified resident report: water appears lower.",
                    )
                )
            else:
                steps.append(
                    SensorStep(
                        **base,
                        sensor_type="temperature",
                        value=round(rng.uniform(24, 34), 2),
                        node_id="weather-node",
                    )
                )
    # Base events precede injected events on ties; original duplicate targets remain valid.
    return sorted(steps, key=lambda s: s.at_seconds)
