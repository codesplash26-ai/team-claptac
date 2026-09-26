"""Landslide and earthquake as live hazards: sensed, triggered, planned for, and warned about.

Flood was the only hazard the loop acted on. These tests cover the additions end to end: the Sentinel
tags each trigger with its hazard, A2 tracks the dominant one, A4 closes steep roads for slope
hazards, A3 plans for the current hazard, and A9 words the public alert for it.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from atlas.agents.a1_sentinel import Sentinel
from atlas.agents.a2_hazard_intel import HazardIntel
from atlas.core.events import EventType
from atlas.datasets.events import HazardEvent
from atlas.datasets.usgs import reports_for_areas
from atlas.ingest.feeds import FeedReport, to_event
from atlas.ingest.simulator import replay
from atlas.tools import geo
from atlas.tools.sensor_map import SensorMap, slope_reports
from atlas.tools.translation import PhraseBook
from tests.conftest import approve_alerts, events
from tests.orchestration.conftest import reading, scenario

ROOT = Path(__file__).resolve().parents[2]
AREA = "ratnapura-demo-river"


def drill(name):
    return json.loads((ROOT / "data" / "scenarios" / f"{name}.json").read_text(encoding="utf-8"))


async def run(system, runtime, clock, story):
    await runtime.start()
    await replay(runtime, story, clock)
    for _ in range(3):
        await system.control.gateway.run_pending()
        await approve_alerts(system)


def slope(offset, kind, value, unit):
    entry = reading(offset, value)
    entry["payload"].update(type=kind, unit=unit, node_id="rtp-slope-01", sensor_id=kind)
    return entry


def report(offset, **fields):
    body = {"kind": "official_warning", "source": "demo:agency", "area_id": AREA, **fields}
    return {"at_seconds": offset, "payload": body}


# ------------------------------------------------------------------------------------ the Sentinel


async def test_slope_tilt_triggers_a_landslide(system, runtime, clock):
    await run(
        system,
        runtime,
        clock,
        scenario(slope(0, "tilt", 0.3, "deg"), slope(60, "tilt", 2.6, "deg")),
    )
    (trigger,) = [e["payload"] for e in events(runtime, "trigger")]
    assert trigger["reason"] == "tilt" and trigger["hazard"] == "landslide"


async def test_an_official_quake_report_triggers_an_earthquake(system, runtime, clock):
    await run(
        system, runtime, clock, scenario(report(0, attribute="earthquake", value=5.1, unit="M"))
    )
    (trigger,) = [e["payload"] for e in events(runtime, "trigger")]
    assert trigger["hazard"] == "earthquake" and round(trigger["score"], 2) == round(5.1 / 4.5, 2)


async def test_a_small_quake_does_not_trigger(system, runtime, clock):
    await run(
        system, runtime, clock, scenario(report(0, attribute="earthquake", value=3.2, unit="M"))
    )
    assert not events(runtime, "trigger")


async def test_a_landslide_warning_carries_its_hazard(system, runtime, clock):
    story = scenario(report(0, attribute="warning", value=2, unit="level", hazard="landslide"))
    await run(system, runtime, clock, story)
    (trigger,) = [e["payload"] for e in events(runtime, "trigger")]
    assert trigger["hazard"] == "landslide"


async def test_a_warning_without_a_hazard_is_still_a_flood(system, runtime, clock):
    await run(
        system, runtime, clock, scenario(report(0, attribute="warning", value=1.2, unit="level"))
    )
    (trigger,) = [e["payload"] for e in events(runtime, "trigger")]
    assert (
        trigger["hazard"] == "flood"
    )  # the original behaviour, for producers that predate hazards


async def test_a_quake_in_the_wrong_unit_is_rejected(system, runtime, clock):
    await run(
        system, runtime, clock, scenario(report(0, attribute="earthquake", value=5.1, unit="mw"))
    )
    assert events(runtime, "observation_rejected") and not events(runtime, "trigger")


# ------------------------------------------------------------------------------------------- A2


def test_the_dominant_hazard_is_the_one_at_the_highest_level(runtime):
    a2 = HazardIntel(runtime.bus, runtime.cop, runtime.ledger, vulnerabilities=[])
    rows = [(AREA, "flood", "watch"), (AREA, "landslide", "severe")]
    for area, hazard, level in rows:
        runtime.db.connection.execute(
            "INSERT INTO a2_risk VALUES (?,?,?,?,?)",
            (area, hazard, level, 0.8, "2026-09-26T00:00:00"),
        )
    assert a2.dominant_hazard(AREA, latest="flood") == "landslide"
    assert a2.dominant_hazard(AREA, latest="landslide") == "landslide"


def test_the_sentinel_and_a2_share_hazard_names():
    from atlas.agents.a1_sentinel import HAZARD_OF as A1
    from atlas.agents.a2_hazard_intel import HAZARD_OF as A2

    assert all(A1[attribute] == hazard for attribute, hazard in A2.items())
    assert Sentinel and A1["earthquake"] == "earthquake"


# ------------------------------------------------------------------------------------- the roads


def test_slope_hazards_affect_steep_roads_only():
    rg = geo.load_site("ratnapura")
    area = SensorMap.from_file().get(AREA)
    wet = slope_reports(area, rg, "soil_moisture", 86, 0.8)
    assert wet and {r.reported_open for r in wet} == {0.0}
    assert {r.reported_open for r in slope_reports(area, rg, "soil_moisture", 60, 0.8)} == {1.0}
    severe = slope_reports(area, rg, "tilt", 3.5, 0.8)
    assert len(severe) > len(wet)  # a severe reading reaches gentler slopes too
    assert slope_reports(area, rg, "earthquake", 4.0, 0.8) == []
    assert {r.reported_open for r in slope_reports(area, rg, "earthquake", 5.2, 0.8)} == {0.35}
    assert {r.reported_open for r in slope_reports(area, rg, "earthquake", 7.0, 0.8)} == {0.0}


# ---------------------------------------------------------------------------------- end to end


async def test_the_landslide_drill_plans_away_from_the_slopes(system, runtime, clock):
    await run(system, runtime, clock, drill("ratnapura_landslide_demo"))
    (incident,) = system.orchestration.commander.incidents()
    assert incident["hazard"] == "landslide" and system.control.dispatch.list()
    (alert,) = system.control.comms.list_alerts()
    assert alert["event"] == "landslide"
    assert "slopes" in alert["texts"]["en"]["instruction"]
    assert "<category>Geo</category>" in system.control.comms.get_alert(alert["id"])["cap_xml"]


async def test_the_earthquake_drill_warns_about_an_earthquake(system, runtime, clock):
    await run(system, runtime, clock, drill("ratnapura_earthquake_demo"))
    (incident,) = system.orchestration.commander.incidents()
    assert incident["hazard"] == "earthquake" and system.control.dispatch.list()
    (alert,) = system.control.comms.list_alerts()
    assert alert["event"] == "earthquake" and alert["texts"]["en"]["headline"].startswith(
        "Earthquake"
    )
    assert "damaged buildings" in alert["texts"]["en"]["instruction"]


async def test_a_flood_warning_still_goes_out_after_a_landslide_warning(system, runtime, clock):
    """Ditwah: the soil saturates before the river rises. Both warnings reach the public."""
    await run(system, runtime, clock, drill("ratnapura_ditwah_2025"))
    assert {a["event"] for a in system.control.comms.list_alerts()} == {"landslide", "flood"}
    assert runtime.ledger.verify()


# -------------------------------------------------------------------------- live quakes, wording


def test_only_quakes_near_an_area_become_reports():
    def quake(lat, lon, mag):
        return HazardEvent(
            id=f"usgs:{lat}", hazard="earthquake", source="usgs", name="test",
            start=datetime(2026, 9, 26, tzinfo=timezone.utc), lat=lat, lon=lon, severity=mag,
        )  # fmt: skip

    near, far = quake(6.75, 80.45, 5.4), quake(3.3, 96.0, 9.1)  # Ratnapura; Sumatra
    out = reports_for_areas([near, far])
    assert out and {r.report_id.split(":")[1] for r in out} == {"6.75"}
    assert all(r.attribute == "earthquake" and r.unit == "M" and r.value == 5.4 for r in out)
    assert to_event(out[0]).type is EventType.OBSERVATION
    assert FeedReport.model_validate(out[0].model_dump())


def test_alert_wording_depends_on_the_hazard():
    book = PhraseBook()
    assert "higher ground" in book.instruction("evacuate", "en", "flood")
    assert "slopes" in book.instruction("evacuate", "en", "landslide")
    assert book.instruction("evacuate", "si", "earthquake") and book.event("earthquake", "ta")
    assert book.instruction("avoid_area", "en", "earthquake") == book.instruction(
        "avoid_area", "en"
    )
