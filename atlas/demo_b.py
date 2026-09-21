#!/usr/bin/env python3
"""End-to-end demo for Workstream B: sensor observation → risk → triage → A3 → gateway.

Runs without an LLM key (deterministic A4 fallback). With LLM_API_KEY set, A3
uses tool-calling orchestration instead.

Usage:
  python -m atlas.demo_b
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from atlas.agents.runtime import build_runtime
from atlas.core.events import Event, EventType
from atlas.ingest.sensors import SensorReading, SensorType, to_event

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("atlas.demo_b")


async def run_demo() -> None:
    rt = build_runtime(force_llm_fallback=True)

    reading = SensorReading(
        node_id="rtp-node-01",
        sensor_id="wl-01",
        type=SensorType.WATER_LEVEL,
        value=62.0,
        unit="cm",
        timestamp=datetime.now(timezone.utc),
        location={
            "lat": 6.6828,
            "lon": 80.3992,
            "area_id": "ratnapura-demo-river",
        },
    )
    observation = to_event(reading)
    log.info("1) publish OBSERVATION %s", observation.id)
    await rt.publish(observation)

    trigger = Event(
        type=EventType.TRIGGER,
        source="a1_sentinel",
        incident_id="demo-flood-001",
        payload={
            "area_id": "ratnapura-demo-river",
            "hazard": "flood",
            "anomaly_score": 0.91,
            "reason": "water_level crossed severe threshold",
        },
    )
    log.info("2) publish TRIGGER %s", trigger.id)
    await rt.publish(trigger)

    cycle = rt.a3.get_cycle("demo-flood-001")
    ranked = rt.a5.ranked()
    ledger = rt.ledger.all()

    print("\n=== ATLAS Workstream B demo ===")
    print(f"A3 cycle state : {cycle.state.value if cycle else None}")
    print(f"LLM fallback   : {cycle.used_llm_fallback if cycle else None}")
    print(f"Triage top     : {ranked[0]['area_id'] if ranked else None} score={ranked[0]['score'] if ranked else None}")
    if ranked:
        print(f"Explanation    : {ranked[0]['explanation']}")
    print(f"Ledger entries : {len(ledger)}")
    for entry in ledger:
        print(f"  - {entry.status} {entry.action_type} ({entry.idempotency_key})")
    print(f"Bus events     : {len(rt.bus.history)}")
    for ev in rt.bus.history:
        print(f"  - {ev.type.value:20} from {ev.source}")
    print("=== done ===\n")


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
