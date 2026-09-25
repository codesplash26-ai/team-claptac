"""Real disasters as replayable scenarios.  (Owner: Dinuka)

A preset names a real event, the area it drives, the window around its peak, and a speed-up. Building
one fetches the real readings (through the cache), converts them with ``to_scenario`` and adds the
metadata a viewer needs to know it is real: where the numbers came from and what was assumed.

Speed-up matters. The Sentinel measures rise in centimetres per *replay* minute and accumulates rain
over a five-minute *replay* window, so compressing time also compresses those. A river that rose
2.5 cm an hour rises 5 cm a minute at 120x, which is the Sentinel's rate-of-rise threshold. Pick the
speed-up with that in mind, and check the result by replaying it (the tests do).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from atlas.datasets.lk_irrigation import LkIrrigation
from atlas.datasets.open_meteo import OpenMeteo
from atlas.datasets.to_scenario import readings_to_scenario
from atlas.ingest.sensors import SensorReading

SCENARIOS_DIR = Path(__file__).resolve().parents[2] / "data" / "scenarios"


class Preset(BaseModel):
    key: str
    title: str
    hazard: str
    area_id: str
    start: datetime
    end: datetime
    speedup: float
    sources: list[str]  # "gauge", "rain", "soil"
    story: str


PRESETS: dict[str, Preset] = {
    p.key: p
    for p in [
        Preset(
            key="ratnapura-ditwah-2025",
            title="Ratnapura, Cyclone Ditwah floods, November 2025",
            hazard="flood",
            area_id="ratnapura-demo-river",
            start=datetime(2025, 11, 27, 0, tzinfo=timezone.utc),
            end=datetime(2025, 11, 29, 12, tzinfo=timezone.utc),
            speedup=240,
            sources=["gauge", "rain", "soil"],
            story=(
                "Tropical Storm Ditwah passed about 44 km from Sri Lanka on 27-28 November 2025. The "
                "Kalu Ganga at Ratnapura rose past the department's major-flood level (9.5 m) and "
                "peaked near 10.2 m on 28 November. GDACS rated the flood Orange."
            ),
        ),
        Preset(
            key="ratnapura-ditwah-rain-only",
            title="Ratnapura, Ditwah rainfall and soil saturation (landslide conditions)",
            hazard="landslide",
            area_id="ratnapura-demo-river",
            start=datetime(2025, 11, 26, 0, tzinfo=timezone.utc),
            end=datetime(2025, 11, 29, 0, tzinfo=timezone.utc),
            speedup=240,
            sources=["rain", "soil"],
            story=(
                "The same storm seen only through ERA5 rainfall and soil saturation: what a landslide "
                "early warning would have had to go on, with no river gauge."
            ),
        ),
    ]
}


def readings_for(preset: Preset, *, offline: bool = False) -> list[SensorReading]:
    readings: list[SensorReading] = []
    if "gauge" in preset.sources:
        readings += LkIrrigation().readings(
            preset.area_id, preset.start, preset.end, offline=offline
        )
    kinds = {"rain": "rainfall", "soil": "soil_moisture"}
    wanted = {kinds[k] for k in preset.sources if k in kinds}
    if wanted:
        got = OpenMeteo().readings(preset.area_id, preset.start, preset.end, offline=offline)
        readings += [r for r in got if r.type.value in wanted]
    return readings


def build(key: str, *, offline: bool = False) -> dict:
    preset = PRESETS[key]
    readings = readings_for(preset, offline=offline)
    if not readings:
        raise ValueError(f"no readings found for {key}")
    scenario = readings_to_scenario(readings, key, speedup=preset.speedup)
    scenario.update(
        title=preset.title,
        hazard=preset.hazard,
        area_id=preset.area_id,
        story=preset.story,
        speedup=preset.speedup,
        real_start=preset.start.isoformat(),
        real_end=preset.end.isoformat(),
        sources=preset.sources,
        real=True,
        replay_minutes=round(max(e["at_seconds"] for e in scenario["events"]) / 60, 1),
        assumptions=[
            "River level converted from metres to the Sentinel's tabletop centimetres at 0.20 m per cm "
            "(the scale in data/reference/sensor_map.yaml).",
            "Rainfall and soil moisture are ERA5 reanalysis (about 11-25 km cells), not gauge readings; "
            "soil moisture is scaled to percent of an assumed 0.5 m3/m3 saturation.",
            f"Time compressed {preset.speedup:g}x: the Sentinel's five-minute window and rate-of-rise "
            "threshold are measured in replay time.",
        ],
        licences=["lk_irrigation: MIT", "Open-Meteo: CC BY 4.0, non-commercial"],
    )
    return scenario


def save(key: str, *, offline: bool = False, directory: Path = SCENARIOS_DIR) -> Path:
    path = directory / f"{key.replace('-', '_')}.json"
    path.write_text(json.dumps(build(key, offline=offline), indent=1), encoding="utf-8")
    return path
