"""Real-world data sources and the scenarios built from them.  (Owner: Dinuka)

    base.py          the SourceAdapter interface and the metadata every source must carry
    lk_irrigation.py Sri Lanka Irrigation Dept river gauges (community scrape, MIT)
    open_meteo.py    historical rainfall and soil moisture; GloFAS river discharge
    usgs.py          earthquakes
    coolr.py         NASA landslide catalogue
    to_scenario.py   turn readings into the replay format in data/scenarios/

Adapters only fetch and normalise. They produce ``SensorReading``s; everything after that is the
normal pipeline, so a replayed real flood exercises exactly the code a live sensor does. Downloads are
cached under ``data/cache/`` (git-ignored); tests use small committed fixtures, never the network.
"""
