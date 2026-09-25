# Data sources

Owner: Dinuka. Adapters live in `atlas/datasets/`; converted scenarios go in `data/scenarios/`.
"Verified" means someone fetched real data and checked the format. Unverified rows are leads, not facts.

| Source | Drives | Licence | Verified | Notes |
|---|---|---|---|---|
| [lk_irrigation](https://github.com/nuuuwan/lk_irrigation) | flood (river gauges) | MIT | exists, includes Ratnapura; format not yet checked | ~39 Irrigation Dept stations; units are metres, not the tabletop's cm |
| [Open-Meteo historical weather](https://open-meteo.com/en/docs/historical-weather-api) | flood, landslide (rain, soil moisture) | free, non-commercial | docs read | 1940 to now, no key |
| [Open-Meteo flood API](https://open-meteo.com/en/docs/flood-api) | flood (GloFAS discharge) | free, non-commercial | docs read | ~5 km; docs mention history only to 2022, check |
| [USGS earthquakes](https://earthquake.usgs.gov/fdsnws/event/1/) | earthquake | public domain | docs read | secondary: Sri Lanka is low seismic |
| [NASA COOLR](https://gpm.nasa.gov/landslides/data.html) | landslide (ground truth) | CC-BY | docs read | dated events, not measurements |
| DesInventar Sri Lanka (desinventar.lk) | history of floods and landslides | check | viewer found; bulk download not confirmed | 1974-2022 per its summary |
| GDACS, ReliefWeb, EM-DAT, GPM IMERG, CHIRPS | alerts, history, rainfall | check each | **not checked** | leads only |

Cache downloads under `data/cache/` (git-ignored). Tests use small committed fixtures and never touch
the network.
