"""Actions from the agent manager: replay a real disaster, inject a reading, red-team, what-if.

Everything here that changes the live system needs the operator token when one is set
(``ATLAS_OPERATOR_TOKEN``). Red team and what-if run in throwaway sandboxes and never touch live state.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from atlas.api.routes import require_operator
from atlas.dashboard.dev import playground
from atlas.ingest.sensors import SensorReading

router = APIRouter(prefix="/play")
operator = [Depends(require_operator)]


class StartReplay(BaseModel):
    scenario: str
    speed: float = Field(default=1.0, gt=0)


class WhatIf(BaseModel):
    scenario: str
    variant: playground.Variant


@router.get("/scenarios")
async def scenarios():
    return [s.model_dump(mode="json") for s in playground.list_scenarios()]


@router.get("/scenarios/status")
async def replay_status(request: Request):
    return request.app.state.replay.status()


@router.post("/scenarios/start", dependencies=operator)
async def replay_start(body: StartReplay, request: Request):
    try:
        return request.app.state.replay.start(body.scenario, body.speed)
    except KeyError as exc:
        raise HTTPException(404, f"unknown scenario {body.scenario!r}") from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409 if isinstance(exc, RuntimeError) else 422, str(exc)) from exc


@router.post("/scenarios/stop", dependencies=operator)
async def replay_stop(request: Request):
    return request.app.state.replay.stop()


@router.post("/inject", dependencies=operator, status_code=202)
async def inject(reading: SensorReading, request: Request):
    """A single reading through the normal ingest path (its timestamp is used as given)."""
    return {"accepted": await playground.inject_reading(request.app.state.runtime, reading)}


@router.post("/inject/now", dependencies=operator, status_code=202)
async def inject_now(
    request: Request,
    node_id: str,
    sensor_id: str,
    type: str,
    value: float,
    unit: str,
    area_id: str,
    lat: float = 6.6828,
    lon: float = 80.3992,
):
    reading = SensorReading(
        node_id=node_id,
        sensor_id=sensor_id,
        type=type,
        value=value,
        unit=unit,
        timestamp=datetime.now(timezone.utc),
        measurement_id=f"play-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        location={"lat": lat, "lon": lon, "area_id": area_id},
    )
    return {"accepted": await playground.inject_reading(request.app.state.runtime, reading)}


@router.post("/redteam", dependencies=operator)
async def redteam():
    return await asyncio.to_thread(playground.run_redteam)


@router.post("/whatif", dependencies=operator)
async def whatif(body: WhatIf):
    try:
        scenario = playground.load_scenario(body.scenario)
    except KeyError as exc:
        raise HTTPException(404, f"unknown scenario {body.scenario!r}") from exc
    return await asyncio.to_thread(playground.whatif, scenario, body.variant)
