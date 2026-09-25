"""Where each ATLAS area sits in the real world.  (Owner: Dinuka)

An ATLAS ``area_id`` (the id a sensor, a scenario and ``data/reference/sensor_map.yaml`` agree on)
needs a real gauge and a real coordinate to pull real data for. Add a row to add a site.
"""

from __future__ import annotations

from pydantic import BaseModel


class AreaSource(BaseModel):
    area_id: str
    lat: float
    lon: float
    gauge_station: str | None = None  # a station name in lk_irrigation's stations.json
    note: str = ""


AREAS: dict[str, AreaSource] = {
    a.area_id: a
    for a in [
        AreaSource(
            area_id="ratnapura-demo-river",
            lat=6.6828,
            lon=80.3992,
            gauge_station="Rathnapura",
            note="Kalu Ganga at Ratnapura: the demo site. The tabletop container stands in for it.",
        ),
        AreaSource(
            area_id="ratnapura-gn-12",
            lat=6.7050,
            lon=80.3850,
            gauge_station="Rathnapura",
            note="Same river, another division; shares the gauge.",
        ),
        AreaSource(
            area_id="colombo-fort-low",
            lat=6.9344,
            lon=79.8428,
            gauge_station="Hanwella",
            note="Kelani Ganga at Hanwella is the upstream gauge for Colombo's floods.",
        ),
    ]
}
