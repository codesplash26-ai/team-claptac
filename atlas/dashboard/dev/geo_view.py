"""The situation on a map, with uncertainty.  (Owner: Dinuka)

Roads coloured by what the system *believes* about them, and how sure it is: A4 keeps, for every road, a
belief that it is passable and a confidence in that belief (evidence decays, unobserved roads sit at a
prior). The map shows both, because "probably closed, but based on one old report" and "closed, three
sources agree" call for different responses. No map tiles: the road graph itself draws the geography, so
this works with no internet.
"""

from __future__ import annotations

import re
from typing import Any


def friendly(entity_id: str) -> str:
    """'ratnapura-shelter-01' -> 'Shelter 01'; 'ratnapura-rescue-team-01' -> 'Rescue team 01'.

    Only for display: the id stays the reference everywhere else."""
    key = re.sub(r"^[a-z]+(?:-fort)?-", "", entity_id or "")
    words = key.replace("-", " ").split()
    return (" ".join(words).capitalize() if words else entity_id) or entity_id


def situation_map(system) -> dict[str, Any]:
    runtime = system.runtime
    rg = system.routing.road_graph
    now = runtime.clock()
    beliefs: dict[str, dict] = {}
    closed_hard: set[str] = set()
    for b in runtime.cop.snapshot(now=now)["beliefs"]:
        if b["subject"].startswith("edge:"):
            edge = b["subject"][5:]
            if b["attribute"] == "belief_open":
                beliefs[edge] = b
            elif b["attribute"] == "open" and b["value"] is False:
                closed_hard.add(edge)
    edges = []
    from atlas.tools import geo

    for u, v in sorted(rg.graph.edges):
        eid = geo.edge_id(u, v)
        b = beliefs.get(eid)
        a, c = rg.graph.nodes[u], rg.graph.nodes[v]
        edges.append(
            {
                "id": eid,
                "from": [a["lat"], a["lon"]],
                "to": [c["lat"], c["lon"]],
                "belief_open": round(b["value"], 3) if b else None,
                "confidence": round(b["confidence"], 3) if b else None,
                "closed_by_decision": eid in closed_hard,
            }
        )
    lats = [n["lat"] for _, n in rg.graph.nodes(data=True)]
    lons = [n["lon"] for _, n in rg.graph.nodes(data=True)]
    routes = []
    for mission in system.control.dispatch.list():
        pts = [
            [rg.graph.nodes[n]["lat"], rg.graph.nodes[n]["lon"]]
            for n in mission.route
            if isinstance(n, int) and n in rg.graph.nodes
        ]
        if len(pts) >= 2:
            routes.append(
                {
                    "mission_id": mission.id,
                    "title": mission.title,
                    "status": mission.status.value,
                    "points": pts,
                    "team": friendly(mission.team or ""),
                    "people": _people(mission),
                    "destination": friendly(mission.destination) if mission.destination else None,
                    "progress": mission.progress,
                }
            )
    missions = system.control.dispatch.list()
    snapshot = runtime.cop.snapshot(now=now)
    occupancy = {
        b["subject"][8:]: b["value"]
        for b in snapshot["beliefs"]
        if b["subject"].startswith("shelter:") and b["attribute"] == "occupancy"
    }
    finished = {"completed", "cancelled", "failed"}
    shelters = []
    for sh in system.routing.shelter.shelters.values():
        expected = sum(
            _people(m)
            for m in missions
            if getattr(m, "destination", None) == sh.id and m.status.value not in finished
        )
        shelters.append(
            {
                "id": sh.id,
                "name": friendly(sh.id),
                "lat": sh.lat,
                "lon": sh.lon,
                "capacity": sh.capacity,
                "occupancy": occupancy.get(sh.id),  # recorded; None if nobody has reported it
                "expected": expected,  # people on missions heading there now
                "elevation_m": getattr(sh, "elevation_m", None),
                "kind": getattr(sh, "kind", None),
            }
        )
    teams = []
    try:
        from atlas.tools.reference import load_resources

        busy = {
            t.strip()
            for m in missions
            if m.status.value not in finished
            for t in (m.team or "").split(",")
        }
        for r in load_resources(rg.name):
            if r.node in rg.graph.nodes:
                n = rg.graph.nodes[r.node]
                teams.append(
                    {
                        "id": r.id,
                        "name": friendly(r.id),
                        "kind": r.kind,
                        "lat": n["lat"],
                        "lon": n["lon"],
                        "busy": r.id in busy,
                    }
                )
    except Exception:  # a map without teams is still a map
        pass
    areas = []
    try:
        import json
        from pathlib import Path

        ref = (
            Path(__file__).resolve().parents[3]
            / "data"
            / "reference"
            / "vulnerable_populations.json"
        )
        incidents = {i.get("area_id"): i for i in system.orchestration.commander.incidents()}
        for a in json.loads(ref.read_text(encoding="utf-8")):
            if not (
                min(lats) - 0.05 <= a["lat"] <= max(lats) + 0.05
                and min(lons) - 0.05 <= a["lon"] <= max(lons) + 0.05
            ):
                continue
            inc = incidents.get(a["area_id"])
            areas.append(
                {
                    "id": a["area_id"],
                    "name": a["name"],
                    "lat": a["lat"],
                    "lon": a["lon"],
                    "population": a.get("population"),
                    "vulnerable": round(a.get("population", 0) * a.get("vulnerable_fraction", 0)),
                    "level": inc.get("level") if inc else None,  # watch / warning / emergency
                    "incident_id": inc.get("incident_id") if inc else None,
                }
            )
    except Exception:
        pass
    gauges = []
    try:
        from atlas.dashboard.panel import sensor_panel

        for s in sensor_panel(runtime):
            if s.get("location"):
                gauges.append(
                    {
                        "node": s["node_id"],
                        "type": s["sensor_type"],
                        "value": s["value"],
                        "unit": s["unit"],
                        "threshold": s.get("threshold"),
                        "lat": s["location"]["lat"],
                        "lon": s["location"]["lon"],
                    }
                )
    except Exception:  # a map without gauges is still a map
        pass
    return {
        "site": rg.name,
        "areas": areas,
        "teams": teams,
        "bounds": [[min(lats), min(lons)], [max(lats), max(lons)]],
        "edges": edges,
        "routes": routes,
        "shelters": shelters,
        "gauges": gauges,
        "observed_edges": len(beliefs),
        "likely_closed": sum(1 for b in beliefs.values() if b["value"] < 0.3),
    }


def _people(mission) -> int:
    m = re.search(r"Evacuate (\d+) people", mission.title or "")
    return int(m.group(1)) if m else 0
