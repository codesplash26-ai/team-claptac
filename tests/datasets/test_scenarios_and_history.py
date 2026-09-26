"""The committed real-disaster scenarios and hazard history, played and read offline."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from atlas.datasets import scenarios
from atlas.datasets.catalog import HISTORY_PATH, load_history
from atlas.ingest.sensors import SensorReading
from atlas.ingest.simulator import replay
from tests.conftest import approve_alerts, events
from tests.dev.conftest import system  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc


def scenario(name):
    return json.loads((ROOT / "data" / "scenarios" / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", list(scenarios.PRESETS))
def test_every_preset_has_a_committed_scenario_that_says_it_is_real(key):
    data = scenario(key.replace("-", "_") + ".json")
    preset = scenarios.PRESETS[key]
    assert data["real"] is True and data["area_id"] == preset.area_id and data["story"]
    assert data["assumptions"] and data["licences"] and data["speedup"] == preset.speedup
    times = [e["at_seconds"] for e in data["events"]]
    assert times == sorted(times) and times[0] == 0
    ids = [e["payload"]["measurement_id"] for e in data["events"]]
    assert len(set(ids)) == len(ids)  # replaying twice never double counts
    for e in data["events"]:  # every payload is a valid reading once the replayer adds a timestamp
        SensorReading.model_validate({**e["payload"], "timestamp": "2025-11-28T00:00:00+00:00"})


def test_the_ditwah_scenario_carries_the_real_peak():
    data = scenario("ratnapura_ditwah_2025.json")
    gauge = [e["payload"]["value"] for e in data["events"] if e["payload"]["type"] == "water_level"]
    assert max(gauge) == pytest.approx(51.05)  # 10.21 m at the peak, in the Sentinel's cm
    kinds = {e["payload"]["type"] for e in data["events"]}
    assert kinds == {"water_level", "rainfall", "soil_moisture"}
    rain_only = scenario("ratnapura_ditwah_rain_only.json")
    assert {e["payload"]["type"] for e in rain_only["events"]} == {"rainfall", "soil_moisture"}


async def test_the_real_flood_runs_through_every_agent(system, runtime, clock):  # noqa: F811
    await runtime.start()
    await replay(runtime, scenario("ratnapura_ditwah_2025.json"), clock)
    for _ in range(3):
        await system.control.gateway.run_pending()
        await approve_alerts(system)  # the commander says yes to the evacuation alert
    reasons = {e["payload"]["reason"] for e in events(runtime, "trigger")}
    assert {"soil_moisture", "rainfall", "water_level"} <= reasons  # three independent signals fire
    (incident,) = system.orchestration.commander.incidents()
    assert incident["cycle"] >= 2  # the situation kept changing, so A3 replanned
    assert system.control.dispatch.list() and system.control.comms.list_alerts()
    assert runtime.ledger.verify()


async def test_rain_only_scenario_still_triggers_without_a_river_gauge(system, runtime, clock):  # noqa: F811
    await runtime.start()
    await replay(runtime, scenario("ratnapura_ditwah_rain_only.json"), clock)
    assert {e["payload"]["reason"] for e in events(runtime, "trigger")} <= {
        "rainfall",
        "soil_moisture",
    }
    assert events(runtime, "trigger")


def test_readings_for_uses_only_the_requested_sources(monkeypatch):
    def reading(kind, unit, value):
        return SensorReading(
            node_id="n",
            sensor_id="s",
            type=kind,
            value=value,
            unit=unit,
            timestamp=datetime(2025, 11, 28, tzinfo=UTC),
            location={"lat": 6.7, "lon": 80.4, "area_id": "ratnapura-demo-river"},
        )

    class Gauge:
        def readings(self, *a, **k):
            return [reading("water_level", "cm", 30)]

    class Weather:
        def readings(self, *a, **k):
            return [reading("rainfall", "mm", 2), reading("soil_moisture", "%", 50)]

    monkeypatch.setattr(scenarios, "LkIrrigation", Gauge)
    monkeypatch.setattr(scenarios, "OpenMeteo", Weather)
    preset = scenarios.PRESETS["ratnapura-ditwah-2025"]
    assert {r.type.value for r in scenarios.readings_for(preset)} == {
        "water_level",
        "rainfall",
        "soil_moisture",
    }
    only_rain = preset.model_copy(update={"sources": ["rain"]})
    assert {r.type.value for r in scenarios.readings_for(only_rain)} == {"rainfall"}
    built = scenarios.build("ratnapura-ditwah-rain-only")
    assert built["real"] and built["assumptions"] and built["hazard"] == "landslide"


# ---------------------------------------------------------------------- history


def test_history_covers_every_hazard_we_have_a_verified_source_for():
    history = load_history()
    hazards = {e.hazard for e in history}
    assert {"flood", "cyclone", "earthquake", "tsunami", "drought"} <= hazards
    assert history == sorted(history, key=lambda e: e.start)
    assert len({e.id for e in history}) == len(history)


def test_history_contains_the_events_a_sri_lankan_would_name():
    history = load_history()
    assert any(e.hazard == "tsunami" and e.start.year == 2004 and e.severity > 10 for e in history)
    assert any(
        e.hazard == "earthquake" and e.severity == 9.1 and e.start.year == 2004 for e in history
    )
    assert any(
        e.hazard == "cyclone" and "Ditwah" in e.name and e.detail["closest_km"] < 100
        for e in history
    )
    assert any(
        e.hazard == "flood"
        and e.source == "gdacs"
        and e.severity_label == "Orange"
        and e.start.year == 2025
        for e in history
    )
    assert any(e.hazard == "drought" and e.severity_label == "extreme" for e in history)


def test_history_snapshot_is_compact_and_says_where_it_came_from():
    data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    assert data["retrieved"] and {s["key"] for s in data["sources"]} >= {
        "gdacs",
        "ibtracs",
        "noaa_tsunami",
        "usgs",
        "drought_index",
    }
    assert all(s["licence"] and s["count"] >= 0 for s in data["sources"])
    assert all("track" not in e["detail"] and "runups" not in e["detail"] for e in data["events"])
    assert HISTORY_PATH.stat().st_size < 400_000
