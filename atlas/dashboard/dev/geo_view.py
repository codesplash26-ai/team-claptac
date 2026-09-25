"""The situation on a map, with uncertainty.  (Owner: Dinuka)

Roads coloured by what the system *believes* about them, and how sure it is: A4 keeps, for every road, a
belief that it is passable and a confidence in that belief (evidence decays, unobserved roads sit at a
prior). The map shows both, because "probably closed, but based on one old report" and "closed, three
sources agree" call for different responses. No map tiles: the road graph itself draws the geography, so
this works with no internet.
"""

from __future__ import annotations

from typing import Any


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
                }
            )
    shelters = [
        {"id": s.id, "lat": s.lat, "lon": s.lon, "capacity": s.capacity}
        for s in system.routing.shelter.shelters.values()
    ]
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
        "bounds": [[min(lats), min(lons)], [max(lats), max(lons)]],
        "edges": edges,
        "routes": routes,
        "shelters": shelters,
        "gauges": gauges,
        "observed_edges": len(beliefs),
        "likely_closed": sum(1 for b in beliefs.values() if b["value"] < 0.3),
    }
