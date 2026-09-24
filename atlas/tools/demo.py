"""Demo fixtures for the Ratnapura flood scenario: known-good locations and synthetic evidence.

Used by the tests, the evaluation and any demo script, so they all stage the same story:

    25 cm  the Sentinel's alarm level; roads near the gauge flood, the one shelter whose only
           approach crosses the valley (shelter-02) is cut off
    30 cm  the valley floods: residents such as DEMO_ORIGIN must reroute to a different shelter,
           while the depot is cut off from every shelter and the plan escalates to a human

`tests/tools/test_sensor_map.py` asserts these locations still behave this way, so re-check it if the
road graph, shelters or `data/reference/sensor_map.yaml` change.
"""

from __future__ import annotations

from datetime import timedelta

from atlas.core.events import Event, EventType, Observation

AREA = "ratnapura-demo-river"  # the area_id shared by the Pi config, flood.json and sensor_map.yaml
DEPOT = 2151514485  # town-centre depot node in data/reference/resources.json

# A valley resident near the town centre (6.68394, 80.39815, 35 m). At 30 cm the usual 8-hop route to
# shelter-01 floods and the dry route goes to shelter-03: the demo's visible reroute.
DEMO_ORIGIN = 5299475391
DEMO_LEVEL_CM = 30


def validated_observation(
    clock,
    level_cm,
    *,
    area=AREA,
    attribute="water_level",
    confidence=0.9,
    evidence="ev-1",
    offset=0,
) -> Event:
    """An A1-validated observation event, as the Sentinel would emit it for a mapped area."""
    at = clock.now + timedelta(seconds=offset)
    obs = Observation(
        observation_id=f"obs-{level_cm}-{offset}",
        area_id=area,
        attribute=attribute,
        value=level_cm,
        unit="cm",
        observed_at=at,
        received_at=at,
        source="sensor:demo",
        confidence=confidence,
        evidence_ids=[evidence],
    )
    return Event(
        type=EventType.OBSERVATION_VALIDATED,
        source="a1_sentinel",
        payload=obs.model_dump(mode="json"),
    )
