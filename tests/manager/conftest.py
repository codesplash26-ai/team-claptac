import time

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.core.config import Settings
from tests.orchestration.conftest import system  # noqa: F401  (the full system, shared)

TOKENS = (
    '[{"token":"t-admin","name":"ada","role":"admin"},'
    '{"token":"t-cmd","name":"cara","role":"commander"},'
    '{"token":"t-dev","name":"dev","role":"developer"},'
    '{"token":"t-ai","name":"ai","role":"ai_lead"},'
    '{"token":"t-ops","name":"ops","role":"platform_engineer"},'
    '{"token":"t-view","name":"vic","role":"viewer"}]'
)


@pytest.fixture
def client():
    with TestClient(create_app(Settings(database_path=":memory:"))) as c:
        yield c


@pytest.fixture
def secured(monkeypatch):
    monkeypatch.setenv("ATLAS_TOKENS", TOKENS)
    with TestClient(create_app(Settings(database_path=":memory:"))) as c:
        yield c


def as_(role):
    return {"Authorization": f"Bearer t-{role}"}


def wait_for(fn, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("timed out")


def run_flood(client):
    client.post("/play/scenarios/start", json={"scenario": "flood", "speed": 60})
    wait_for(lambda: client.get("/play/scenarios/status").json()["state"] == "finished")
    return wait_for(lambda: client.get("/dev/runs").json())
