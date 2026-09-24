"""Rebuild the belief map from COP beliefs; shared by every planning agent (A4, A6, A7).

The COP holds the persisted evidence; time decay is computed here, at read time, and never written
back (otherwise every plan would see a changed COP version and be rejected as stale at the Gateway).

    edge:<u>-<v> / belief_open   float, written by A4 from sensor evidence
    edge:<u>-<v> / open == False hard closure, written by A8's close_major_route
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from atlas.tools.cognitive_map import beliefs as engine
from atlas.tools.cognitive_map.beliefs import CognitiveMap


class RoutingConfig(BaseModel):
    """Knobs shared by the planning agents.

    Defaults are the original engine's values, except `slope_penalty_per_m` (see below).
    """

    prior_open: float = Field(default=0.9, ge=0, le=1)  # belief for a road nothing is known about
    alpha_risk: float = Field(default=engine.ALPHA_RISK, ge=0, le=1)
    # Decay is per minute of age: 1 simulation step is treated as 1 minute (original engine).
    decay_rate_per_minute: float = Field(default=engine.DECAY_RATE, ge=0)
    # The original engine slows uphill roads by 10% per metre climbed. Off by default in ATLAS: on the
    # real hilly networks it steers evacuees down into flood-prone valleys and away from hill
    # shelters (belief agent: 61% success vs 100% with it off, Ratnapura flood). Set 0.10 to restore
    # the original behaviour.
    slope_penalty_per_m: float = Field(default=0.0, ge=0)
    write_epsilon: float = Field(default=0.03, gt=0)
    close_threshold: float = Field(default=0.3, gt=0, lt=1)  # below: treated as effectively closed
    reopen_threshold: float = Field(default=0.5, gt=0, lt=1)  # back above: treated as reopened


def minutes(moment: datetime) -> float:
    return moment.timestamp() / 60.0


def load_map(
    graph, edge_index: dict[str, tuple], snapshot: dict, now: datetime, cfg: RoutingConfig
) -> tuple[CognitiveMap, dict[tuple, list[str]]]:
    """Belief map for `graph` from a COP snapshot, decayed to `now`.

    Returns the map and {edge: evidence ids} for audit. Roads with no stored belief start at the
    prior with no decay, so "nothing known" is not mistaken for "old evidence".
    """
    cmap = CognitiveMap(
        graph,
        prior_open=cfg.prior_open,
        alpha_risk=cfg.alpha_risk,
        slope_penalty_per_m=cfg.slope_penalty_per_m,
    )
    now_min = minutes(now)
    provenance: dict[tuple, list[str]] = {}
    loaded = set()
    for b in snapshot["beliefs"]:
        subject = b["subject"]
        edge = edge_index.get(subject[5:]) if subject.startswith("edge:") else None
        if edge is None:
            continue
        if b["attribute"] == "belief_open":
            stamp = minutes(datetime.fromisoformat(b["updated_at"]))
            cmap.set_belief(*edge, float(b["value"]), last_updated=stamp)
            provenance.setdefault(edge, []).extend(b["provenance"])
            loaded.add(edge)
        elif b["attribute"] == "open" and b["value"] is False:  # A8: close_major_route
            cmap.set_closed(*edge)
            provenance.setdefault(edge, []).extend(b["provenance"])
    for u, v, d in cmap.map.edges(data=True):
        if (u, v) not in loaded:
            d["last_updated"] = now_min
    cmap.apply_time_decay(now_min, cfg.decay_rate_per_minute)
    return cmap, provenance
