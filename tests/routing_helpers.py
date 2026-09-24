"""Helpers shared by the A4 / A6 / A7 tests: build sensor evidence and inspect road beliefs."""

from atlas.core.events import Belief
from atlas.tools import geo
from atlas.tools.demo import (  # noqa: F401  (re-exported for the tests)
    AREA,
    DEMO_LEVEL_CM,
    DEMO_ORIGIN,
    DEPOT,
)
from atlas.tools.demo import validated_observation as observation  # noqa: F401
from atlas.tools.sensor_map import inundated_edges


def edge_beliefs(runtime):
    """{"<u>-<v>": Belief} for every road belief_open currently in the COP."""
    out = {}
    for b in runtime.cop.snapshot(now=runtime.clock())["beliefs"]:
        if b["subject"].startswith("edge:") and b["attribute"] == "belief_open":
            out[b["subject"][5:]] = Belief.model_validate(b)
    return out


def wet(mapping, rg, level):
    """Edge ids the sensor map considers under water at `level` cm."""
    return {geo.edge_id(*e) for e in inundated_edges(mapping, rg, level)}


def crossed(nodes, ids):
    """Which of the edge ids `ids` a node path crosses."""
    return {geo.edge_id(u, v) for u, v in zip(nodes[:-1], nodes[1:])} & ids
