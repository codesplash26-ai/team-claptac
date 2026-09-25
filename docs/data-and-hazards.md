# Data and hazards

What real data ATLAS can use, for which disasters, and what each hazard needs before it can drive the live
loop. Owner: Dinuka. Code: `atlas/datasets/`. Snapshot: `data/reference/hazard_history.json`.

```
python -m atlas.datasets sources                          # what each source is, verified or not
python -m atlas.datasets scenario ratnapura-ditwah-2025   # rebuild a real-disaster scenario
python -m atlas.datasets catalog                          # rebuild the hazard history
```

## Two kinds of data

| Kind | Interface | Becomes | Used for |
|---|---|---|---|
| Readings (numbers over time at a place) | `SourceAdapter.readings` | `SensorReading`, replayed through A1 | live-style replay of a real event |
| Events (a quake, a storm, an alert) | `EventSource.events` | `HazardEvent` | timeline, map, evaluation ground truth, scenario seeds |

## Sources, checked against the real services

| Source | Hazards | Kind | Verified | What we found |
|---|---|---|---|---|
| lk_irrigation (Irrigation Dept gauges) | flood | readings | yes | 40 stations with the department's alert, minor and major flood levels; history from Nov 2025. Ratnapura peaked at 10.21 m on 28 Nov 2025 (major flood is 9.5 m) |
| Open-Meteo (ERA5) | flood, landslide, cyclone | readings | yes | hourly rain and soil moisture, 11-25 km cells: reanalysis, not gauges |
| GDACS | flood, cyclone, quake, drought, volcano, wildfire | events | yes | 51 Sri Lanka events since 2015 with Green/Orange/Red; Ditwah is Orange for both storm and flood |
| IBTrACS (North Indian) | cyclone | events | yes | 94 storms within 600 km since 1980; Ditwah 2025 passed 44 km away |
| NOAA tsunami run-ups | tsunami | events | yes | 11 events; 2004 has 274 run-up records, peak 12.5 m |
| USGS | earthquake | events | yes | no M3.5+ inside Sri Lanka's box since 1990; 30 M7+ across the northern Indian Ocean (Sri Lanka's real exposure) |
| Drought index (ours, from ERA5) | drought | events | yes | 90-day rainfall z-score vs 1991-2020; finds 2016-17, 2019, 2021, 2023 |
| NASA COOLR | landslide | events | **no** | every endpoint we tried timed out or returned 404 from our network; the parser is tested on a sample only |
| DesInventar Sri Lanka | flood, landslide, others | - | **no** | the portal is reachable, but we found no bulk API |
| ReliefWeb | all | - | **no** | returns 403 without a registered app name |
| Lightning, coastal erosion | - | - | none | no free feed found |

Licences are in `SourceInfo` and in the snapshot; the derived scenarios carry them.

## What can drive the live loop today

The Sentinel understands three signals: river level, rainfall and soil moisture. So today:

| Hazard | Live loop | How |
|---|---|---|
| Flood | yes | gauge level, plus rainfall |
| Landslide conditions | partly | rainfall and soil saturation trigger and A2 labels the hazard `landslide`; the planners (A4) still map roads from *water level* only |
| Cyclone | through its rain | the rainfall and river response; wind and pressure have no rule |
| Earthquake, tsunami, drought | **no** | history only |

To make the rest live, someone has to change contracts (each needs the whole team, see
`docs/contracts.md` decision 3):

1. a hazard field on the Sentinel's `Trigger` (today A3 defaults to "flood" and A2 forces it on a trigger),
2. new sensor or report types (seismic magnitude, wind speed, sea level) and a rule for each in A1,
3. A2 and A4 mappings from those to areas and roads, and ontology entries for the new hazards.

Until then, a quake replayed as a "warning" would trigger a flood-shaped plan. We do not ship scenarios
that would mislead, so earthquakes, tsunamis and droughts are in the history and not in `data/scenarios/`.

## Real scenarios

| Scenario | Hazard | Real window | Replay | Signals |
|---|---|---|---|---|
| `ratnapura-ditwah-2025` | flood | 27 Nov 00:00 to 29 Nov 12:00 2025 | 16 min at 240x | gauge, rain, soil |
| `ratnapura-ditwah-rain-only` | landslide conditions | 26 to 29 Nov 2025 | 240x | rain, soil |

Played through the whole system (A1 to A9) they trigger on soil saturation first, then rainfall, then the
river, and A3 replans as roads close until its bound, at which point it asks a human: a realistic ending.

### Assumptions to state when presenting
- River metres become the Sentinel's tabletop centimetres at 0.20 m per cm (`sensor_map.yaml`): the
  department's alert level lands just above the Sentinel's alarm.
- Rain and soil are ERA5 reanalysis; soil is scaled by an assumed saturation of 0.5 m3/m3.
- Time compression changes what the Sentinel sees: its five-minute window and rate-of-rise threshold are
  in replay time. Choose the speed-up with that in mind and replay to check.
- The gauge data comes from a community scrape of the department's site: cite both.
