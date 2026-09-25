from datetime import timedelta

import pytest

from atlas.core.config import Settings
from atlas.core.events import Belief, Event, EventType
from atlas.runtime import Runtime
from tests.conftest import events, sensor


async def test_handler_failure_rolls_back_state_and_replays_after_restart(tmp_path, clock):
    settings = Settings(database_path=str(tmp_path / "atlas.db"))
    runtime = Runtime(settings, clock=clock)
    event = Event(type=EventType.TRIGGER, source="test")

    async def failing(event):
        runtime.cop.put(
            Belief(subject="test", attribute="count", value=1, confidence=1, updated_at=clock.now)
        )
        raise RuntimeError("injected crash")

    runtime.bus.subscribe("consumer", [EventType.TRIGGER], failing)
    with pytest.raises(RuntimeError):
        await runtime.bus.publish(event)
    assert runtime.cop.version == 0
    await runtime.close()
    recovered = Runtime(settings, clock=clock)
    calls = []

    async def successful(event):
        calls.append(event.id)
        return [Event(type=EventType.MISSION_STATUS, source="consumer")]

    recovered.bus.subscribe("consumer", [EventType.TRIGGER], successful)
    await recovered.start()
    await recovered.bus.recover()
    assert calls == [event.id]
    assert len(events(recovered, "mission_status")) == 1
    await recovered.close()


async def test_sentinel_restart_does_not_retrigger(tmp_path, clock):
    settings = Settings(database_path=str(tmp_path / "atlas.db"))
    runtime = Runtime(settings, clock=clock)
    event = sensor(clock, 26)
    await runtime.bus.publish(event)
    await runtime.close()
    runtime = Runtime(settings, clock=clock)
    await runtime.start()
    await runtime.bus.publish(event)
    clock.now += timedelta(seconds=10)
    await runtime.bus.publish(sensor(clock, 26))
    assert len(events(runtime, "trigger")) == 1
    await runtime.close()


def test_snapshots_detached_and_ttl(runtime, clock):
    runtime.cop.put(
        Belief(
            subject="road",
            attribute="open",
            value=True,
            confidence=0.8,
            updated_at=clock.now,
            ttl_seconds=10,
        )
    )
    snapshot = runtime.snapshot()
    snapshot["beliefs"][0]["value"] = False
    assert runtime.cop.get("road", "open", now=clock.now).value is True
    clock.now += timedelta(seconds=10)
    assert runtime.snapshot()["beliefs"] == []


async def test_publish_reads_only_new_event_history_and_resume_keeps_pending(runtime, monkeypatch):
    seen = []

    async def handler(event):
        seen.append(event.id)
        return []

    runtime.bus.subscribe("incremental", [EventType.INFORMATION_GAP], handler)
    first = Event(type=EventType.INFORMATION_GAP, source="test")
    await runtime.bus.publish(first)
    cursor = runtime.ledger.records("event")[-1]["seq"]
    batches = []
    original = runtime.ledger.records_after

    def read_after(seq, kind, limit=1000):
        batches.append(seq)
        return original(seq, kind, limit)

    monkeypatch.setattr(runtime.ledger, "records_after", read_after)
    second = Event(type=EventType.INFORMATION_GAP, source="test")
    await runtime.bus.publish(second)
    assert batches and min(batches) >= cursor
    runtime.bus.suspend("incremental")
    third = Event(type=EventType.INFORMATION_GAP, source="test")
    await runtime.bus.publish(third)
    assert seen == [first.id, second.id]
    await runtime.bus.resume("incremental")
    assert seen == [first.id, second.id, third.id]
