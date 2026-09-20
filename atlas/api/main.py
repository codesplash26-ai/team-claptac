"""Sensor/report ingestion connected to the shared runtime; other API work is D's."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from atlas.ingest.feeds import FeedReport
from atlas.ingest.feeds import to_event as report_event
from atlas.ingest.sensors import SensorReading, to_event
from atlas.runtime import Runtime


def create_app(settings=None):
    @asynccontextmanager
    async def lifespan(app):
        runtime = Runtime(settings)
        app.state.runtime = runtime
        try:
            await runtime.start()
            yield
        finally:
            await runtime.close()

    app = FastAPI(title="ATLAS", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    async def publish(request, event):
        try:
            inserted = await request.app.state.runtime.bus.publish(event)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        return {"accepted": event.id, "duplicate": not inserted}

    @app.post("/ingest/sensor", status_code=202)
    async def ingest_sensor(reading: SensorReading, request: Request):
        return await publish(request, to_event(reading))

    @app.post("/ingest/report", status_code=202)
    async def ingest_report(report: FeedReport, request: Request):
        return await publish(request, report_event(report))

    return app


app = create_app()
