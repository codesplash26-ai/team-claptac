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


async def approve_alerts(system, reviewer="test-commander"):
    """Act as the Incident Commander: approve every public alert waiting for a human.

    Every evacuation alert goes to a human (config/policy.yaml), so a test of the whole loop that
    expects an alert to go out must say yes to it, as the commander would.
    """
    gateway = system.control.gateway
    approved = 0
    for item in system.control.approvals.pending():
        if item.action["action_type"] == "publish_alert":
            await gateway.emit(gateway.approve(item.id, reviewer, "approved in test"))
            approved += 1
    return approved
