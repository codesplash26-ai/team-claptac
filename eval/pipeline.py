"""Compute latency of Workstream C's part of the response loop, on the real Ratnapura network.

Times the real code paths through a real `Runtime` (in-memory database, simulated clock):

    evidence -> road beliefs    a validated water-level reading through the bus into A4's COP update
    route plan                  A4.plan_evacuation
    shelter plan                A7.assign (includes a CP-SAT solve)
    logistics plan              A6.assign (includes a CP-SAT solve)

This is wall-clock compute time on this machine. It does NOT include A1 detection, A3's LLM, the
Gateway's human approval, or any network or message delivery, so it is a floor for the proposal's
"new information to an updated, gated plan in under 2 minutes" target, not a measurement of it.
"""

from __future__ import annotations

import asyncio
import time

from atlas.agents.contracts import EvacueeGroup, RescueJob
from atlas.agents.wiring import attach_routing
from atlas.core.config import Settings
from atlas.ingest.simulator import ReplayClock
from atlas.runtime import Runtime
from atlas.tools.demo import DEMO_LEVEL_CM, DEMO_ORIGIN, validated_observation
from eval.metrics import latency_stats

STAGES = ("evidence_to_beliefs", "route_plan", "shelter_plan", "logistics_plan")
REPLAN_TARGET_SECONDS = 120.0  # proposal 8.2: new information to an updated plan in < 2 minutes


def _timed(fn):
    start = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - start) * 1000.0


async def _once(site: str) -> dict[str, float]:
    clock = ReplayClock()
    runtime = Runtime(Settings(database_path=":memory:"), clock=clock)
    try:
        routing = attach_routing(runtime, site=site)
        await runtime.start()

        start = time.perf_counter()
        await runtime.bus.publish(validated_observation(clock, DEMO_LEVEL_CM))
        beliefs_ms = (time.perf_counter() - start) * 1000.0

        _, route_ms = _timed(lambda: routing.evacuation.plan_evacuation(DEMO_ORIGIN, now=clock.now))
        groups = [
            EvacueeGroup(group_id=f"g{i}", people=20 + 5 * i, origin=DEMO_ORIGIN) for i in range(6)
        ]
        _, shelter_ms = _timed(lambda: routing.shelter.assign(groups, now=clock.now))
        shelters = [s.node for s in routing.shelter.shelters.values()]
        jobs = [
            RescueJob(job_id=f"j{i}", priority=1 + i, location=node, needs={"vehicle": 1})
            for i, node in enumerate(shelters)
        ]
        _, logistics_ms = _timed(lambda: routing.logistics.assign(jobs, now=clock.now))
        return dict(zip(STAGES, (beliefs_ms, route_ms, shelter_ms, logistics_ms), strict=True))
    finally:
        await runtime.close()


def measure_pipeline(repetitions: int = 10, site: str = "ratnapura") -> dict[str, dict]:
    """Per-stage latency statistics over `repetitions` fresh runs (the first is a warm-up, dropped)."""
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    samples = {stage: [] for stage in STAGES}
    for i in range(repetitions + 1):
        run = asyncio.run(_once(site))
        if i == 0:
            continue  # imports, JIT-like first-call costs
        for stage, ms in run.items():
            samples[stage].append(ms)
    report = {stage: latency_stats(values) for stage, values in samples.items()}
    total = [sum(v) for v in zip(*samples.values(), strict=True)]
    report["end_to_end"] = latency_stats(total)
    return report


__all__ = ["REPLAN_TARGET_SECONDS", "STAGES", "measure_pipeline"]
