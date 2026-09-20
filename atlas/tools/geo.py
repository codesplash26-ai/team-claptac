"""Road networks for ATLAS: OSM graphs with elevation, cached on disk, plus geometry helpers.

Graphs are built once with OSMnx and cached as compact JSON under `data/osm/`. The cache is
committed, so the demo runs offline and every teammate routes on the same network. Runtime code
(agents, bus handlers) only ever calls `load_site`, which reads the cache and never touches the
network; building or refreshing a site is an explicit step:

    python -m atlas.tools.geo build ratnapura        # or: build --all

Edge IDs are `"<u>-<v>"` (OSM node ids) and COP subjects are `edge:<u>-<v>`. This matches the
`Belief` docstring and the `road_id` that A8's `close_major_route` writes.

Elevation provenance is recorded on every graph (`elevation_source`), because the original engine
silently substituted a fake 0-10 m slope when its elevation API failed.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import numpy as np

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
OSM_DIR = ROOT / "data" / "osm"
EARTH_RADIUS_M = 6_371_000.0
CACHE_VERSION = 1

# Elevation providers, tried in order. OpenTopoData (SRTM 30 m) is primary: the proposal's
# Open-Elevation endpoint currently fails TLS verification (expired certificate), and certificate
# checking is never disabled. Both serve SRTM-derived data.
OPENTOPODATA_URL = "https://api.opentopodata.org/v1/srtm30m"
OPEN_ELEVATION_URL = "https://api.open-elevation.com/api/v1/lookup"


@dataclass(frozen=True)
class Site:
    name: str
    center: tuple[float, float]  # (lat, lon)
    radius_m: int = 1000
    description: str = ""


# Ratnapura is centred on the town (and the demo river sensor) rather than the original engine's
# point ~3 km further north, so the tabletop sensor's location lies inside the graph.
SITES: dict[str, Site] = {
    "colombo_fort": Site("colombo_fort", (6.9344, 79.8428), 1000, "Flat, coastal"),
    "ratnapura": Site("ratnapura", (6.6828, 80.3992), 1000, "Hilly, Kalu Ganga flood-prone"),
    "kandy": Site("kandy", (7.2906, 80.6337), 1000, "Highly mountainous"),
}


# ------------------------------------------------------------------ geometry


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres. Accepts scalars or NumPy arrays."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlmb = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(a))


def edge_id(u, v) -> str:
    return f"{u}-{v}"


def parse_edge_id(text: str) -> tuple[int, int]:
    """Inverse of `edge_id` for integer node ids. Raises ValueError on anything else."""
    u, sep, v = text.partition("-")
    if not sep:
        raise ValueError(f"Not an edge id: {text!r}")
    return int(u), int(v)


# ------------------------------------------------------------------ road graph


@dataclass
class RoadGraph:
    """A routable road network. `graph` edges carry travel_time (minutes), elev_u, elev_v."""

    name: str
    graph: nx.DiGraph
    center: tuple[float, float]
    radius_m: int
    elevation_source: str
    built_at: str = ""
    _arrays: tuple | None = field(default=None, repr=False, compare=False)

    # -- lookup

    def pos(self, node) -> tuple[float, float]:
        d = self.graph.nodes[node]
        return d["lat"], d["lon"]

    def elevation(self, node) -> float:
        return self.graph.nodes[node]["elevation"]

    def edge_index(self) -> dict[str, tuple]:
        """{"u-v": (u, v)} for every directed edge, so ids never need parsing at runtime."""
        return {edge_id(u, v): (u, v) for u, v in self.graph.edges}

    def _node_arrays(self):
        if self._arrays is None:
            nodes = sorted(self.graph.nodes)
            lat = np.array([self.graph.nodes[n]["lat"] for n in nodes])
            lon = np.array([self.graph.nodes[n]["lon"] for n in nodes])
            self._arrays = (nodes, lat, lon)
        return self._arrays

    def nearest_node(self, lat: float, lon: float):
        nodes, lats, lons = self._node_arrays()
        return nodes[int(np.argmin(haversine_m(lat, lon, lats, lons)))]

    def nodes_within(self, lat: float, lon: float, radius_m: float) -> list:
        nodes, lats, lons = self._node_arrays()
        dist = haversine_m(lat, lon, lats, lons)
        return [n for n, d in zip(nodes, dist) if d <= radius_m]

    def edges_within(self, lat: float, lon: float, radius_m: float) -> list[tuple]:
        """Edges whose midpoint is within `radius_m` of the point (sorted, deterministic)."""
        found = []
        for u, v in self.graph.edges:
            (lat1, lon1), (lat2, lon2) = self.pos(u), self.pos(v)
            if haversine_m(lat, lon, (lat1 + lat2) / 2, (lon1 + lon2) / 2) <= radius_m:
                found.append((u, v))
        return sorted(found)

    # -- GeoJSON for the dashboard (Folium expects [lon, lat])

    def edges_geojson(self, beliefs: dict[tuple, float] | None = None) -> dict:
        features = []
        for u, v in sorted(self.graph.edges):
            props = {"edge_id": edge_id(u, v)}
            if beliefs is not None and (u, v) in beliefs:
                props["belief_open"] = round(float(beliefs[(u, v)]), 4)
            elif beliefs is not None:
                continue
            features.append(self._line(props, [u, v]))
        return {"type": "FeatureCollection", "features": features}

    def route_geojson(self, path, properties: dict | None = None) -> dict:
        return self._line({"kind": "route", **(properties or {})}, list(path))

    def _line(self, properties: dict, nodes: list) -> dict:
        coords = [[self.graph.nodes[n]["lon"], self.graph.nodes[n]["lat"]] for n in nodes]
        return {
            "type": "Feature",
            "properties": properties,
            "geometry": {"type": "LineString", "coordinates": coords},
        }

    # -- (de)serialisation

    def to_dict(self) -> dict:
        g = self.graph
        return {
            "version": CACHE_VERSION,
            "name": self.name,
            "center": list(self.center),
            "radius_m": self.radius_m,
            "elevation_source": self.elevation_source,
            "built_at": self.built_at,
            "nodes": {
                str(n): [
                    round(d["lat"], 6),
                    round(d["lon"], 6),
                    round(d["elevation"], 1),
                ]
                for n, d in sorted(g.nodes(data=True))
            },
            "edges": [
                [
                    u,
                    v,
                    round(d["travel_time"], 4),
                    round(d.get("length_m", 0.0), 1),
                    d.get("highway", ""),
                ]
                for u, v, d in sorted(g.edges(data=True), key=lambda e: (e[0], e[1]))
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> RoadGraph:
        if data.get("version") != CACHE_VERSION:
            raise ValueError(f"Unsupported road-graph cache version: {data.get('version')!r}")
        g = nx.DiGraph()
        for n, (lat, lon, elev) in data["nodes"].items():
            g.add_node(int(n), lat=lat, lon=lon, elevation=elev, is_shelter=False)
        for u, v, minutes, length, highway in data["edges"]:
            g.add_edge(
                u,
                v,
                travel_time=minutes,
                length_m=length,
                highway=highway,
                elev_u=g.nodes[u]["elevation"],
                elev_v=g.nodes[v]["elevation"],
            )
        return cls(
            name=data["name"],
            graph=g,
            center=tuple(data["center"]),
            radius_m=data["radius_m"],
            elevation_source=data["elevation_source"],
            built_at=data.get("built_at", ""),
        )


# ------------------------------------------------------------------- cache I/O


def cache_path(name: str, data_dir: Path | None = None) -> Path:
    return (data_dir or OSM_DIR) / f"{name}.json"


def save_graph(rg: RoadGraph, data_dir: Path | None = None) -> Path:
    path = cache_path(rg.name, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rg.to_dict(), separators=(",", ":")), encoding="utf-8")
    return path


def load_site(name: str, data_dir: Path | None = None) -> RoadGraph:
    """Load a cached road graph. Never touches the network; safe inside bus handlers."""
    path = cache_path(name, data_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"No cached road graph for {name!r} at {path}. "
            f"Build it once with: python -m atlas.tools.geo build {name}"
        )
    rg = RoadGraph.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if rg.elevation_source.startswith("synthetic"):
        log.warning("Road graph %r uses SYNTHETIC elevation; flood mapping is not meaningful", name)
    return rg


# ----------------------------------------------------------------- elevation


def _fetch_opentopodata(points: list[tuple[float, float]], session) -> list[float]:
    out: list[float] = []
    for i in range(0, len(points), 100):  # API limit: 100 locations per request, 1 request/second
        chunk = points[i : i + 100]
        params = {"locations": "|".join(f"{lat:.6f},{lon:.6f}" for lat, lon in chunk)}
        for attempt in range(4):
            resp = session.get(OPENTOPODATA_URL, params=params, timeout=30)
            if resp.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            resp.raise_for_status()
            break
        else:
            raise RuntimeError("OpenTopoData rate limit did not clear")
        results = resp.json()["results"]
        if len(results) != len(chunk) or any(r["elevation"] is None for r in results):
            raise ValueError("OpenTopoData returned missing elevations")
        out.extend(float(r["elevation"]) for r in results)
        time.sleep(1.1)
    return out


def _fetch_open_elevation(points: list[tuple[float, float]], session) -> list[float]:
    out: list[float] = []
    for i in range(0, len(points), 200):
        chunk = points[i : i + 200]
        body = {"locations": [{"latitude": lat, "longitude": lon} for lat, lon in chunk]}
        resp = session.post(OPEN_ELEVATION_URL, json=body, timeout=30)
        resp.raise_for_status()
        results = resp.json()["results"]
        if len(results) != len(chunk):
            raise ValueError("Open-Elevation returned the wrong number of results")
        out.extend(float(r["elevation"]) for r in results)
    return out


def fetch_elevations(points: list[tuple[float, float]]) -> tuple[list[float], str]:
    """Elevation (m) for each (lat, lon), plus a provenance label. TLS is always verified."""
    import requests

    session = requests.Session()
    for label, fetch in (
        ("opentopodata:srtm30m", _fetch_opentopodata),
        ("open-elevation", _fetch_open_elevation),
    ):
        try:
            return fetch(points, session), label
        except Exception as exc:
            log.warning("Elevation provider %s failed: %s", label, exc)
    log.error("All elevation providers failed; using a SYNTHETIC 0-10 m west-east slope")
    lons = np.array([lon for _, lon in points])
    span = max(lons.max() - lons.min(), 1e-9)
    return [float((lon - lons.min()) / span * 10.0) for lon in lons], "synthetic"


# ------------------------------------------------------------------- building


def build_site(site: Site, *, fetch_elevation=fetch_elevations) -> RoadGraph:
    """Download the drivable OSM network around `site` and attach elevation. Needs network."""
    import osmnx as ox

    # Keep OSMnx's raw HTTP cache out of the working tree (default is ./cache in the CWD).
    ox.settings.cache_folder = str(ROOT / ".cache" / "osmnx")

    g_osm = ox.graph_from_point(site.center, dist=site.radius_m, network_type="drive")
    g_osm = ox.add_edge_speeds(g_osm)
    g_osm = ox.add_edge_travel_times(g_osm)  # seconds

    node_ids = list(g_osm.nodes)
    points = [(float(g_osm.nodes[n]["y"]), float(g_osm.nodes[n]["x"])) for n in node_ids]
    elevations, source = fetch_elevation(points)

    g = nx.DiGraph()
    for n, (lat, lon), elev in zip(node_ids, points, elevations):
        g.add_node(int(n), lat=lat, lon=lon, elevation=float(elev), is_shelter=False)

    # Parallel edges collapse to the fastest one, as in the original engine.
    best: dict[tuple, tuple[float, float, str]] = {}
    for u, v, data in g_osm.edges(data=True):
        seconds = float(data.get("travel_time") or 0.0)
        length = float(data.get("length", 50.0))
        if seconds <= 0.0:
            speed_mps = max(0.1, float(data.get("speed_kph", 30.0)) * 1000.0 / 3600.0)
            seconds = length / speed_mps
        minutes = max(0.05, seconds / 60.0)
        highway = data.get("highway", "")
        highway = highway[0] if isinstance(highway, list) else str(highway)
        if (u, v) not in best or minutes < best[(u, v)][0]:
            best[(u, v)] = (minutes, length, highway)
    for (u, v), (minutes, length, highway) in best.items():
        g.add_edge(
            int(u),
            int(v),
            travel_time=minutes,
            length_m=length,
            highway=highway,
            elev_u=g.nodes[int(u)]["elevation"],
            elev_v=g.nodes[int(v)]["elevation"],
        )

    # Keep the largest strongly connected component so every node can reach every other node;
    # otherwise a sensor or shelter could map to a node nothing can route to or from.
    core = max(nx.strongly_connected_components(g), key=len)
    g = g.subgraph(core).copy()

    return RoadGraph(
        name=site.name,
        graph=g,
        center=site.center,
        radius_m=site.radius_m,
        elevation_source=source,
        built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def suggest_shelter_nodes(rg: RoadGraph, k: int, min_separation_m: float = 300.0) -> list:
    """Deterministically pick `k` well-spaced high-ground nodes (candidate synthetic shelters)."""
    ranked = sorted(rg.graph.nodes, key=lambda n: (-rg.elevation(n), n))
    chosen: list = []
    for n in ranked:
        lat, lon = rg.pos(n)
        if all(haversine_m(lat, lon, *rg.pos(c)) >= min_separation_m for c in chosen):
            chosen.append(n)
        if len(chosen) == k:
            break
    return chosen


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build cached road graphs (needs network).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    build = sub.add_parser("build", help="download an OSM graph and cache it under data/osm/")
    build.add_argument("site", nargs="?", choices=sorted(SITES))
    build.add_argument("--all", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    names = sorted(SITES) if args.all else [args.site]
    if not names or names == [None]:
        parser.error("give a site name or --all")
    for name in names:
        rg = build_site(SITES[name])
        path = save_graph(rg)
        print(
            f"{name}: {rg.graph.number_of_nodes()} nodes, {rg.graph.number_of_edges()} edges, "
            f"elevation={rg.elevation_source} -> {path} ({path.stat().st_size // 1024} KB)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
