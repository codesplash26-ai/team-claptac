"""ATLAS API: sensor/report ingestion (A) plus the control-boundary routes (D)."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request

from atlas.api.routes import router
from atlas.control.wiring import attach_control
from atlas.ingest.feeds import FeedReport
from atlas.ingest.feeds import to_event as report_event
from atlas.ingest.sensors import SensorReading, to_event
from atlas.runtime import Runtime


def create_app(settings=None):
    @asynccontextmanager
    async def lifespan(app):
        runtime = Runtime(settings)
        app.state.runtime = runtime
        # Register subscribers before start(): startup recovery redelivers pending events.
        control = app.state.control = attach_control(runtime)
        try:
            await runtime.start()
            control.start_worker()
            yield
        finally:
            await control.stop_worker()
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

    app.include_router(router)
    return app


app = create_app()
