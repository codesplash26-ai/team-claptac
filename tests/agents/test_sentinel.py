from datetime import timedelta

import pytest

from atlas.core.events import EventType
from tests.conftest import events, sensor


async def test_threshold_duplicate_and_restart_state(runtime, clock):
    event = sensor(clock, 26)
    await runtime.bus.publish(event)
    assert len(events(runtime, "trigger")) == 1
    assert not await runtime.bus.publish(event)
    clock.now += timedelta(seconds=10)
    await runtime.bus.publish(sensor(clock, 26))
    assert len(events(runtime, "trigger")) == 1
    assert runtime.cop.version == 2


async def test_conflict_runs_planner_without_triggering(runtime, clock):
    await runtime.bus.publish(sensor(clock, 5))
    clock.now += timedelta(seconds=10)
    await runtime.bus.publish(sensor(clock, 30, node="two"))
    assert len(events(runtime, "conflict_flag")) == 1
    assert not events(runtime, "trigger")
    assert len(runtime.ledger.records("reasoning")) == 1
    gap = events(runtime, "information_gap")[0]
    assert gap["payload"]["recommendation"] in {
        "recommend_verification",
        "flag_conflict",
        "monitor",
    }


@pytest.mark.parametrize("offset,reason", [(-301, "stale_or_future"), (31, "stale_or_future")])
async def test_old_and_future_readings_do_not_update_state(runtime, clock, offset, reason):
    await runtime.bus.publish(sensor(clock, 99, offset=offset))
    assert runtime.cop.version == 0
    assert events(runtime, "observation_rejected")[0]["payload"]["reason"] == reason


async def test_late_reading_cannot_overwrite_current(runtime, clock):
    await runtime.bus.publish(sensor(clock, 20))
    await runtime.bus.publish(sensor(clock, 40, offset=-1))
    assert runtime.cop.get("area:river", "water_level", now=clock.now).value == 20
    assert events(runtime, "observation_rejected")[0]["payload"]["reason"] == "out_of_order"


async def test_low_reliability_cannot_trigger_alone(runtime, clock):
    runtime.sentinel.config.source_reliability["sensor:one"] = 0.2
    await runtime.bus.publish(sensor(clock, 40))
    assert not events(runtime, "trigger")


async def test_rearm_and_rate_of_rise(runtime, clock):
    await runtime.bus.publish(sensor(clock, 26))
    clock.now += timedelta(seconds=120)
    await runtime.bus.publish(sensor(clock, 10))
    clock.now += timedelta(seconds=60)
    await runtime.bus.publish(sensor(clock, 20))
    assert len(events(runtime, "trigger")) == 2
    assert events(runtime, "trigger")[-1]["payload"]["reason"] == "water_rate_of_rise"


async def test_accumulated_rainfall_and_unit_conversion(runtime, clock):
    await runtime.bus.publish(sensor(clock, 0.26, unit="m"))
    assert events(runtime, "observation_validated")[0]["payload"]["value"] == 26
    for amount in (8, 8, 8):
        clock.now += timedelta(seconds=60)
        await runtime.bus.publish(sensor(clock, amount, kind="rainfall", unit="mm"))
    assert events(runtime, "trigger")[-1]["payload"]["reason"] == "rainfall"


async def test_unmapped_input_retains_evidence(runtime, clock):
    event = sensor(clock, 30)
    event.payload["location"] = None
    await runtime.bus.publish(event)
    rejection = events(runtime, EventType.OBSERVATION_REJECTED)[0]
    assert rejection["payload"]["reason"] == "unmapped_location"
    evidence_id = rejection["payload"]["evidence_ids"][0]
    assert runtime.sentinel.evidence.get(evidence_id, role="auditor")
    with pytest.raises(PermissionError):
        runtime.sentinel.evidence.get(evidence_id, role="public")


async def test_transport_online_does_not_make_backlog_fresh(runtime, clock):
    await runtime.bus.publish(sensor(clock, 30, offset=-600))
    status = runtime.sentinel.sensor_status()[0]
    assert status["online"] and status["reading_stale"]
    clock.now += timedelta(seconds=60)
    assert not runtime.sentinel.sensor_status()[0]["online"]


async def test_older_new_source_still_checks_newer_conflicting_evidence(runtime, clock):
    await runtime.bus.publish(sensor(clock, 5))
    await runtime.bus.publish(sensor(clock, 30, node="two", offset=-10))
    assert events(runtime, "conflict_flag")
    assert not events(runtime, "trigger")


async def test_independent_moderate_sources_can_corroborate(runtime, clock):
    from atlas.ingest.feeds import FeedReport, to_event

    for i in range(3):
        await runtime.bus.publish(
            to_event(
                FeedReport(
                    report_id=str(i),
                    source=f"field:{i}",
                    kind="field_report",
                    area_id="river",
                    timestamp=clock.now,
                    attribute="water_level",
                    value=26,
                    unit="cm",
                )
            )
        )
    assert len(events(runtime, "trigger")) == 1
    assert len(events(runtime, "trigger")[0]["payload"]["evidence_ids"]) == 3
