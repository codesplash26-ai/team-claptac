"""Deterministic input replay, distinct from the planner's outcome simulator.

Run: python -m atlas.ingest.simulator data/scenarios/flood.json
"""

import argparse
import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atlas.core.config import SentinelConfig, Settings
from atlas.ingest.feeds import FeedReport
from atlas.ingest.feeds import to_event as report_event
from atlas.ingest.sensors import SensorReading, to_event
from atlas.runtime import Runtime


class ReplayClock:
    def __init__(self, start=None):
        self.now = start or datetime(2026, 9, 20, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


async def replay(runtime, scenario, clock):
    start = clock.now
    previous = -1
    for index, entry in enumerate(scenario["events"]):
        offset = entry["at_seconds"]
        if offset < previous:
            raise ValueError("Scenario receive times must be ordered")
        previous = offset
        clock.now = start + timedelta(seconds=offset)
        payload = deepcopy(entry["payload"])
        payload["timestamp"] = (
            start + timedelta(seconds=entry.get("observed_seconds", offset))
        ).isoformat()
        if payload.get("kind") == "sensor":
            payload.setdefault("measurement_id", str(index))
            event = to_event(SensorReading.model_validate(payload))
        else:
            payload.setdefault("report_id", str(index))
            event = report_event(FeedReport.model_validate(payload))
        await runtime.bus.publish(event)


async def demo(path):
    clock = ReplayClock()
    runtime = Runtime(
        Settings(database_path=":memory:", sentinel=SentinelConfig(reasoning_enabled=True)),
        clock=clock,
    )
    try:
        await runtime.start()
        await replay(runtime, json.loads(Path(path).read_text()), clock)
        events = [r["payload"] for r in runtime.ledger.records("event")]
        runtime.graph.rebuild(runtime.ledger)
        print(
            json.dumps(
                {
                    "events": len(events),
                    "triggers": [e for e in events if e["type"] == "trigger"],
                    "conflicts": sum(e["type"] == "conflict_flag" for e in events),
                    "reasoning_runs": len(runtime.ledger.records("reasoning")),
                    "ledger_valid": runtime.ledger.verify(),
                    "cop": runtime.snapshot(),
                },
                indent=2,
            )
        )
    finally:
        await runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario")
    asyncio.run(demo(parser.parse_args().scenario))
