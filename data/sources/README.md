# Data sources

Owner: Dinuka. Code in `atlas/datasets/`; the full picture (what drives the live loop, assumptions,
gaps) is in [docs/data-and-hazards.md](../../docs/data-and-hazards.md). "Verified" means someone fetched
real data from the live service and checked the format; the tests replay small recorded responses.

| Source | Drives | Licence | Verified |
|---|---|---|---|
| [lk_irrigation](https://github.com/nuuuwan/lk_irrigation) river gauges | flood | MIT | yes |
| [Open-Meteo](https://open-meteo.com/en/docs/historical-weather-api) rain, soil, wind, GloFAS discharge | flood, landslide, cyclone, drought | CC BY 4.0, non-commercial | yes |
| [GDACS](https://www.gdacs.org/) alerts | flood, cyclone, quake, drought, volcano, wildfire | free, attribute | yes |
| [IBTrACS](https://www.ncei.noaa.gov/products/international-best-track-archive) cyclone tracks | cyclone | public domain | yes |
| [NOAA tsunami run-ups](https://www.ngdc.noaa.gov/hazel/view/hazards/tsunami/runup-search) | tsunami | public domain | yes |
| [USGS](https://earthquake.usgs.gov/fdsnws/event/1/) earthquakes | earthquake | public domain | yes |
| Drought index (ours, from Open-Meteo) | drought | CC BY 4.0 | yes |
| [NASA COOLR](https://gpm.nasa.gov/landslides/data.html) | landslide | CC BY | **no**: endpoints unreachable |
| DesInventar Sri Lanka, ReliefWeb, EM-DAT | history | check each | **no** |

Downloads are cached under `data/cache/` (git-ignored). Real scenarios and the hazard-history snapshot are
committed (`data/scenarios/ratnapura_*.json`, `data/reference/hazard_history.json`) so everything runs offline.
