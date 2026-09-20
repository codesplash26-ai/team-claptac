from datetime import timedelta

import pytest

from atlas.core.config import SentinelConfig, Settings
from atlas.ingest.sensors import SensorReading, to_event
from atlas.ingest.simulator import ReplayClock
from atlas.runtime import Runtime


@pytest.fixture
def clock():
    return ReplayClock()


@pytest.fixture
def runtime(clock):
    runtime = Runtime(
        Settings(database_path=":memory:", sentinel=SentinelConfig(reasoning_enabled=True)),
        clock=clock,
    )
    yield runtime
    runtime.db.close()


def sensor(
    clock,
    value,
    *,
    node="one",
    area="river",
    offset=0,
    kind="water_level",
    unit="cm",
    measurement_id=None,
):
    return to_event(
        SensorReading(
            node_id=node,
            sensor_id=kind,
            type=kind,
            value=value,
            unit=unit,
            measurement_id=measurement_id,
            timestamp=clock.now + timedelta(seconds=offset),
            location={"lat": 6.6, "lon": 80.3, "area_id": area},
        )
    )


def events(runtime, kind):
    return [r["payload"] for r in runtime.ledger.records("event") if r["payload"]["type"] == kind]
