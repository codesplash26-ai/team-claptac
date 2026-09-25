"""Readings to a replayable scenario.  (Owner: Dinuka)

The output is exactly the format ``atlas.ingest.simulator.replay`` already plays
(``data/scenarios/flood.json``): ``{"name": ..., "events": [{"at_seconds": n, "payload": {...}}]}``,
so anything built from real data runs through the same code as a live sensor.
"""

from __future__ import annotations

from atlas.ingest.sensors import SensorReading


def readings_to_scenario(readings: list[SensorReading], name: str, *, speedup: float = 1.0) -> dict:
    """Offsets are seconds from the first reading, divided by ``speedup`` so days can play as minutes.

    Payloads are ``kind: "sensor"`` dumps of ``SensorReading`` (without ``timestamp``, which the
    replayer sets from the clock), each with a stable ``measurement_id`` so a replayed reading is
    idempotent.
    """
    if not readings:
        raise ValueError("no readings to build a scenario from")
    if speedup <= 0:
        raise ValueError("speedup must be positive")
    ordered = sorted(readings, key=lambda r: r.timestamp)
    origin = ordered[0].timestamp
    events = []
    for index, reading in enumerate(ordered):
        payload = reading.model_dump(mode="json", exclude={"timestamp"})
        events.append(
            {
                "at_seconds": int((reading.timestamp - origin).total_seconds() / speedup),
                "payload": {
                    "kind": "sensor",
                    **payload,
                    # Keep a source-provided id; otherwise make one, so a replay is idempotent.
                    "measurement_id": payload.get("measurement_id") or f"{name}-{index}",
                },
            }
        )
    return {"name": name, "events": events}
