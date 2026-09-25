"""Sri Lanka Irrigation Department river gauges via nuuuwan/lk_irrigation.  (Owner: Dinuka)

Community scrape of the department's dashboard (MIT). About 39 stations including Ratnapura on the
Kalu Ganga. Verified to exist and to include Ratnapura; the file format and time range are NOT yet
checked against real files. Real gauge units are metres above datum, not the tabletop's centimetres:
the mapping onto ``SensorReading`` and the Sentinel's thresholds is part of the work.
"""

from __future__ import annotations

from datetime import datetime

from atlas.datasets.base import SourceInfo
from atlas.ingest.sensors import SensorReading


class LkIrrigation:
    info = SourceInfo(
        key="lk_irrigation",
        title="Sri Lanka river water levels (Irrigation Dept, via nuuuwan/lk_irrigation)",
        url="https://github.com/nuuuwan/lk_irrigation",
        licence="MIT (data originates from the Irrigation Department dashboard)",
        hazards=["flood"],
    )

    def readings(
        self, area_id: str, start: datetime, end: datetime, *, offline: bool = False
    ) -> list[SensorReading]:
        raise NotImplementedError("Dinuka: fetch, cache and normalise gauge history")
