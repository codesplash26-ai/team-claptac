"""ATLAS API: sensor/report ingestion (A) plus the control-boundary routes (D)."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from atlas.api.dev_routes import router as dev_router
from atlas.api.knowledge_routes import router as knowledge_router
from atlas.api.play_routes import router as play_router
from atlas.api.routes import router
from atlas.benchmark.api import BenchmarkService
from atlas.benchmark.api import router as benchmark_router
from atlas.dashboard.dev.playground import LiveReplay
from atlas.ingest.feeds import FeedReport
from atlas.ingest.feeds import to_event as report_event
from atlas.ingest.sensors import SensorReading, to_event
from atlas.intelligence.api import router as intelligence_router
from atlas.manager import audit
from atlas.manager.api import router as manager_router
from atlas.manager.evaluation.runner import EvalStore, MonitorWorker
from atlas.manager.observer import OtlpExporter
from atlas.manager.registry import Registry
from atlas.runtime import Runtime
from atlas.system import attach_all


def create_app(settings=None):
    @asynccontextmanager
    async def lifespan(app):
        runtime = Runtime(settings)
        app.state.runtime = runtime
        # Register subscribers before start(): startup recovery redelivers pending events.
        system = app.state.system = attach_all(runtime, site=os.getenv("ATLAS_SITE", "ratnapura"))
        control = app.state.control = system.control
        app.state.replay = LiveReplay(runtime)
        app.state.benchmark = BenchmarkService()
        app.state.registry = Registry(system, site=os.getenv("ATLAS_SITE", "ratnapura"))
        app.state.eval_store = EvalStore(runtime.db)
        app.state.exporter = OtlpExporter()
        monitors = app.state.monitor_worker = MonitorWorker(system, app.state.eval_store)
        try:
            await runtime.start()
            control.start_worker()
            system.intelligence.start_worker()
            monitors.start()
            yield
        finally:
            await app.state.benchmark.close()
            await system.intelligence.stop_worker()
            await monitors.stop()
            app.state.replay.stop()
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
    app.include_router(dev_router)
    app.include_router(play_router)
    app.include_router(manager_router)
    app.include_router(knowledge_router)
    app.include_router(intelligence_router)
    app.include_router(benchmark_router)
    audit.install(app)

    manager = Path(__file__).resolve().parents[1] / "dashboard" / "static" / "manager"
    if manager.is_dir():  # the built agent manager (frontend/); see frontend/README.md
        app.mount("/manager", StaticFiles(directory=manager, html=True), name="manager")

        @app.get("/", include_in_schema=False)
        async def home():
            return RedirectResponse("/manager/")

    return app


app = create_app()
