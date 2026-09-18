"""FastAPI app: incidents, approvals, ledger, missions, alerts."""
from fastapi import FastAPI

from atlas.ingest.sensors import SensorReading, to_event

app = FastAPI(title="ATLAS")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/ingest/sensor", status_code=202)
async def ingest_sensor(reading: SensorReading):
    """HTTP alternative to MQTT for Raspberry Pi sensor nodes."""
    event = to_event(reading)
    # TODO(Workstream A): publish to the event bus once atlas/core/event_bus.py exists.
    return {"accepted": event.id}
