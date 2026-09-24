import pytest

from atlas.system import attach_all
from tests.control.conftest import fast_policy

AREA = "ratnapura-demo-river"
LOC = {"lat": 6.6828, "lon": 80.3992, "area_id": AREA}


@pytest.fixture
def system(runtime):
    return attach_all(runtime, policy=fast_policy())


def reading(offset, value, *, node="rtp-node-01", mid=None):
    return {
        "at_seconds": offset,
        "payload": {
            "kind": "sensor",
            "node_id": node,
            "sensor_id": "wl-01",
            "type": "water_level",
            "value": value,
            "unit": "cm",
            "location": LOC,
            **({"measurement_id": mid} if mid else {}),
        },
    }


def scenario(*entries):
    return {"name": "test", "events": list(entries)}
