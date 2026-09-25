"""The data sources: real recorded responses in, hazard events and readings out. No network."""

import json
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from atlas.datasets import drought, gdacs, http, ibtracs, lk_irrigation, noaa_tsunami, usgs
from atlas.datasets.coolr import parse as coolr_parse
from atlas.datasets.events import INDIAN_OCEAN, SRI_LANKA, HazardEvent
from atlas.datasets.open_meteo import OpenMeteo

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "datasets"
UTC = timezone.utc


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)


# ------------------------------------------------------------------- http cache


def test_fetch_caches_and_offline_uses_only_the_cache(tmp_path, monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: calls.append(a) or Response())
    assert http.fetch_json("https://x.test/a", params={"q": 1}, cache_dir=tmp_path) == {"ok": True}
    assert http.fetch_json("https://x.test/a", params={"q": 1}, cache_dir=tmp_path) == {"ok": True}
    assert len(calls) == 1  # second call came from disk
    assert http.fetch_json("https://x.test/a", params={"q": 1}, cache_dir=tmp_path, offline=True)
    with pytest.raises(http.DatasetError, match="offline"):
        http.fetch("https://x.test/other", cache_dir=tmp_path, offline=True)


def test_failed_fetch_falls_back_to_a_stale_cache_then_errors(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("slow")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(http.time, "sleep", lambda s: None)
    path = tmp_path / http._key("https://x.test/b", None)
    path.write_bytes(b"old")
    assert http.fetch("https://x.test/b", cache_dir=tmp_path, max_age_s=0) == b"old"
    with pytest.raises(http.DatasetError, match="could not fetch"):
        http.fetch("https://x.test/never", cache_dir=tmp_path)


def test_non_json_is_a_dataset_error(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch", lambda *a, **k: b"<html>")
    with pytest.raises(http.DatasetError, match="JSON"):
        http.fetch_json("https://x.test/c")


# ---------------------------------------------------------------------- events


def test_hazard_event_needs_a_timezone_and_sane_coordinates():
    with pytest.raises(ValueError):
        HazardEvent(id="a:1", hazard="flood", source="a", name="n", start=datetime(2025, 1, 1))
    with pytest.raises(ValueError):
        HazardEvent(
            id="a:1",
            hazard="flood",
            source="a",
            name="n",
            start=datetime(2025, 1, 1, tzinfo=UTC),
            lat=91,
        )


# ------------------------------------------------------------------------ USGS


def test_usgs_parses_the_2004_sumatra_quake():
    (event,) = usgs.parse(load("usgs_2004.json"))
    assert event.hazard == "earthquake" and event.severity == 9.1
    assert event.start == datetime(2004, 12, 26, 0, 58, 53, 450000, tzinfo=UTC)
    assert "Sumatra" in event.name and event.id.startswith("usgs:")
    assert -20 < event.lat < 20 and event.detail["depth_km"] is not None


def test_usgs_skips_records_without_a_time_or_magnitude():
    assert usgs.parse({"features": [{"id": "x", "properties": {"mag": None, "time": 1}}]}) == []


def test_usgs_queries_by_decade_and_dedupes(monkeypatch):
    seen = []

    def fake(url, params, **kw):
        seen.append((params["starttime"], params["endtime"]))
        return load("usgs_2004.json")

    monkeypatch.setattr(usgs, "fetch_json", fake)
    found = usgs.Usgs(region=INDIAN_OCEAN, min_magnitude=7).events(
        datetime(1995, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC), offline=True
    )
    assert len(seen) == 4 and seen[0][0] == "1995-01-01" and len(found) == 1  # same id merged


# ------------------------------------------------------------------------ GDACS


def test_gdacs_parses_ditwah_cyclone_and_flood_with_alert_levels():
    events = gdacs.parse(load("gdacs_sample.json"))
    kinds = {(e.hazard, e.severity_label) for e in events}
    assert ("cyclone", "Orange") in kinds and ("flood", "Orange") in kinds
    flood = next(e for e in events if e.hazard == "flood" and e.severity_label == "Orange")
    assert flood.start.year == 2025 and flood.detail["glide"].startswith("FL-2025")
    assert flood.severity == 2.0 and flood.country == "Sri Lanka" and flood.url


def test_gdacs_ignores_unknown_event_types():
    assert (
        gdacs.parse(
            {"features": [{"properties": {"eventtype": "XX", "fromdate": "2025-01-01T00:00:00"}}]}
        )
        == []
    )


# --------------------------------------------------------------------- tsunami


def test_tsunami_runups_group_into_events_with_peak_height():
    events = noaa_tsunami.parse(load("tsunami_runups_sample.json")["items"])
    by_year = {e.start.year: e for e in events}
    assert {1883, 2004} <= set(by_year)
    assert (
        by_year[2004].hazard == "tsunami" and by_year[2004].severity == 2.7
    )  # tallest of the four sampled run-ups
    assert by_year[2004].detail["places"] and by_year[2004].severity_unit == "m run-up"
    assert by_year[1883].country == "Sri Lanka"


def test_tsunami_pages_are_followed(monkeypatch):
    pages = []

    def fake(url, params, **kw):
        pages.append(params["page"])
        return {"items": load("tsunami_runups_sample.json")["items"], "totalPages": 3}

    monkeypatch.setattr(noaa_tsunami, "fetch_json", fake)
    found = noaa_tsunami.NoaaTsunami().events(
        datetime(1800, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert pages == [1, 2, 3] and found


# --------------------------------------------------------------------- IBTrACS


def test_ditwah_is_found_and_a_far_storm_is_excluded():
    events = ibtracs.parse((FIX / "ibtracs_sample.csv").read_text(encoding="utf-8"))
    assert [e.name for e in events] == ["Ditwah (2025)"]  # Mocha never came within 600 km
    (ditwah,) = events
    assert ditwah.hazard == "cyclone" and ditwah.detail["closest_km"] < 100
    assert ditwah.severity_unit == "knots" and ditwah.severity >= 34
    assert ditwah.detail["track"] and ditwah.start.year == 2025


def test_a_larger_region_pulls_in_the_far_storm():
    from atlas.datasets.events import Region

    wide = Region(name="wide", lat=7.87, lon=80.77, radius_km=3000, bbox=(0, 0, 0, 0))
    names = {
        e.name
        for e in ibtracs.parse((FIX / "ibtracs_sample.csv").read_text(encoding="utf-8"), wide)
    }
    assert "Mocha (2023)" in names


def test_distance_and_categories():
    assert ibtracs.km_between(7.87, 80.77, 7.87, 80.77) == 0
    assert 95 < ibtracs.km_between(0, 0, 0, 1) < 125  # about 111 km per degree
    assert [ibtracs.category(k) for k in (None, 20, 40, 70, 90, 100, 120, 140)] == [
        "",
        "Depression",
        "Tropical storm",
        "Cat 1",
        "Cat 2",
        "Cat 3",
        "Cat 4",
        "Cat 5",
    ]


# ---------------------------------------------------------------------- drought


def test_rolling_totals_and_zscores_find_a_dry_spell():
    start = date(2000, 1, 1)
    days = [start + timedelta(days=i) for i in range(365 * 12)]
    mm = [5.0 + (i % 7) for i in range(len(days))]  # steady rain
    for i, day in enumerate(days):
        if date(2011, 3, 1) <= day <= date(2011, 7, 1):
            mm[i] = 0.0  # a dry spell in the last year
    totals = drought.rolling_totals(days, mm, 90)
    assert len(totals) == len(days) - 89
    baseline = {d: t for d, t in totals.items() if d.year < 2011}
    z = drought.zscores({d: t for d, t in totals.items() if d.year == 2011}, baseline)
    found = drought.spells(z)
    assert found and found[0]["min_z"] < -2 and drought.label(found[0]["min_z"]) == "extreme"
    assert found[0]["start"].year == 2011


def test_spells_need_enough_consecutive_days():
    z = {date(2020, 1, 1) + timedelta(days=i): -1.5 for i in range(5)}
    assert drought.spells(z, min_days=14) == [] and len(drought.spells(z, min_days=3)) == 1
    assert drought.label(-0.5) == "normal" and drought.label(-1.2) == "moderate"


# -------------------------------------------------------------------- Open-Meteo


def test_open_meteo_readings_are_valid_sensor_readings(monkeypatch):
    monkeypatch.setattr(
        "atlas.datasets.open_meteo.fetch_json", lambda *a, **k: load("open_meteo_day.json")
    )
    readings = OpenMeteo().readings(
        "ratnapura-demo-river",
        datetime(2025, 11, 28, tzinfo=UTC),
        datetime(2025, 11, 28, 23, tzinfo=UTC),
    )
    rain = [r for r in readings if r.type.value == "rainfall"]
    soil = [r for r in readings if r.type.value == "soil_moisture"]
    assert len(rain) == len(soil) == 24 and all(r.unit == "mm" for r in rain)
    assert all(0 <= r.value <= 100 and r.unit == "%" for r in soil)
    assert readings == sorted(readings, key=lambda r: (r.timestamp, r.type.value))
    assert len({r.measurement_id for r in readings}) == len(readings)  # idempotent on replay


def test_open_meteo_rejects_an_unknown_area():
    with pytest.raises(http.DatasetError, match="unknown area"):
        OpenMeteo().readings(
            "atlantis", datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC)
        )


# ----------------------------------------------------------------- lk_irrigation


def test_gauge_levels_convert_to_the_sentinels_scale_and_stages():
    assert lk_irrigation.to_sensor_cm(5.2) == 26.0  # the department's alert level: just past 25 cm
    assert lk_irrigation.to_sensor_cm(9.5) == 47.5
    station = lk_irrigation.Station(
        name="Rathnapura",
        river="Kalu Ganga",
        basin="Kalu Ganga",
        lat=6.7,
        lon=80.4,
        alert_m=5.2,
        minor_flood_m=7.5,
        major_flood_m=9.5,
    )
    assert [station.stage(x) for x in (3, 5.2, 7.5, 9.6)] == [
        "normal",
        "alert",
        "minor_flood",
        "major_flood",
    ]
    assert station.path == "data/rwlds/kalu-ganga-basin/kalu-ganga-river/rathnapura-station"
    assert lk_irrigation.slug("Kalawellawa (Millakanda)") == "kalawellawa-millakanda"


def test_lk_adapter_lists_files_reads_them_and_builds_readings(monkeypatch):
    def fake_json(url, **kw):
        if url.endswith("static/stations.json"):
            return load("lk_stations.json")
        if url.endswith("static/rivers.json"):
            return load("lk_rivers.json")
        if "/contents/" in url:
            return [{"name": "rathnapura-station", "sha": "abc"}]
        if "/git/trees/" in url:
            return {
                "tree": [
                    {"type": "blob", "path": "2025/2025-11/2025-11-28/2025-11-28-12-32-35.json"},
                    {"type": "blob", "path": "2025/2025-11/2025-11-28/2025-11-28-13-32-35.json"},
                    {"type": "blob", "path": "2025/2025-11/2025-11-29/2025-11-29-01-00-00.json"},
                    {"type": "tree", "path": "2025"},
                ]
            }
        raise AssertionError(url)

    levels = {"12-32-35": 10.21, "13-32-35": 10.0, "01-00-00": 8.0}

    def fake_fetch(url, **kw):
        key = "-".join(url.rsplit("/", 1)[1][11:19].split("-"))
        when = datetime.strptime(url.rsplit("/", 1)[1][:-5], "%Y-%m-%d-%H-%M-%S").replace(
            tzinfo=UTC
        )
        return json.dumps(
            {
                "station_name": "Rathnapura",
                "time_ut": int(when.timestamp()),
                "water_level_m": levels[key],
            }
        ).encode()

    monkeypatch.setattr(lk_irrigation, "fetch_json", fake_json)
    monkeypatch.setattr(lk_irrigation, "fetch", fake_fetch)
    readings = lk_irrigation.LkIrrigation().readings(
        "ratnapura-demo-river",
        datetime(2025, 11, 28, tzinfo=UTC),
        datetime(2025, 11, 28, 23, tzinfo=UTC),
    )
    assert [r.value for r in readings] == [51.05, 50.0]  # only the 28th, oldest first, in sensor cm
    assert all(r.unit == "cm" and r.location.area_id == "ratnapura-demo-river" for r in readings)
    assert len({r.measurement_id for r in readings}) == 2


def test_lk_adapter_needs_a_registered_gauge():
    with pytest.raises(http.DatasetError, match="no gauge"):
        lk_irrigation.LkIrrigation().readings(
            "atlantis", datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 2, tzinfo=UTC)
        )


# ------------------------------------------------------------------------ COOLR


def test_coolr_parser_reads_standard_arcgis_json():
    sample = {
        "features": [
            {
                "attributes": {
                    "event_id": 7,
                    "event_title": "Landslide near Kegalle",
                    "event_date": 1479340800000,
                    "fatality_count": 3,
                    "country_name": "Sri Lanka",
                    "landslide_trigger": "rain",
                },
                "geometry": {"x": 80.34, "y": 7.25},
            },
            {"attributes": {"event_id": 8}, "geometry": {}},
        ]
    }
    (event,) = coolr_parse(sample)
    assert event.hazard == "landslide" and event.severity == 3 and event.start.year == 2016
    assert (event.lat, event.lon) == (7.25, 80.34)


def test_only_verified_sources_claim_to_be_verified():
    from atlas.datasets.coolr import Coolr

    assert not Coolr.info.verified
    for cls in (
        usgs.Usgs,
        gdacs.Gdacs,
        ibtracs.Ibtracs,
        noaa_tsunami.NoaaTsunami,
        lk_irrigation.LkIrrigation,
        OpenMeteo,
        drought.DroughtIndex,
    ):
        assert cls.info.verified and cls.info.licence and cls.info.url.startswith("https://")
    assert SRI_LANKA.bbox[0] < SRI_LANKA.bbox[2]
