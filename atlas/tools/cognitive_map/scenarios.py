"""Synthetic road grids and the three evaluation scenarios (A normal, B flood, C high noise).

Ported unchanged from EvacuationAgent-using-Cognitivity/scenarios.py so the published results can
be reproduced. These are illustrative networks, not real roads; the real Sri Lankan networks are
loaded by `atlas.tools.geo`.
"""

from __future__ import annotations

from atlas.tools.cognitive_map.environment import RoadNetwork


def build_small_grid() -> RoadNetwork:
    """
    12-node directed grid:
    0 - 1 - 2 - 3
    |   |   |   |
    4 - 5 - 6 - 7
    |   |   |   |
    8 - 9 - S10 - S11   (S = shelter)
    """
    rn = RoadNetwork(seed=42)

    for i in range(10):
        rn.add_node(i, is_shelter=False)
    rn.add_node(10, is_shelter=True)
    rn.add_node(11, is_shelter=True)

    for row in range(3):
        base = row * 4
        for col in range(3):
            rn.add_road(base + col, base + col + 1, travel_time=5.0)

    for col in range(4):
        for row in range(2):
            rn.add_road(row * 4 + col, (row + 1) * 4 + col, travel_time=6.0)

    rn.add_road(8, 10, travel_time=4.0, bidirectional=False)
    rn.add_road(9, 10, travel_time=3.0, bidirectional=False)
    rn.add_road(9, 11, travel_time=4.0, bidirectional=False)
    rn.add_road(10, 11, travel_time=2.0)

    return rn


def build_stress_grid() -> RoadNetwork:
    """
    5x5 directed grid (nodes 0..24):
    0  - 1  - 2  - 3  - 4
    5  - 6  - 7  - 8  - 9
    10 - 11 - 12 - 13 - 14
    15 - 16 - 17 - 18 - 19
    20 - 21 - 22 - S23 - S24
    """
    rn = RoadNetwork(seed=42)

    for i in range(25):
        rn.add_node(i, is_shelter=i in (23, 24))

    for row in range(5):
        base = row * 5
        for col in range(4):
            rn.add_road(base + col, base + col + 1, travel_time=5.0)

    for col in range(5):
        for row in range(4):
            rn.add_road(row * 5 + col, (row + 1) * 5 + col, travel_time=6.0)

    return rn


def scenario_A_normal(rn):
    return rn, [], []  # no closures / partials


def scenario_B_flood(rn):
    closures = [
        (3, [(1, 5), (5, 1), (5, 9), (9, 5)]),
        (7, [(2, 6), (6, 2), (6, 10)]),
    ]
    partials = [
        (2, [(0, 1), (1, 0), (4, 5), (5, 4)], 1.5),
        (6, [(8, 9), (9, 8), (8, 10), (9, 10)], 1.7),
    ]
    return rn, closures, partials


def scenario_B_flood_stress(rn):
    """Stress flood with more closures and explicit shelter-side bottlenecks."""
    closures = [
        # Early disruptions on common central corridors
        (3, [(6, 7), (7, 6), (11, 12), (12, 11), (12, 13), (13, 12)]),
        # Later bottlenecks close key shelter approach edges
        (
            7,
            [
                (18, 23),
                (23, 18),
                (19, 24),
                (24, 19),
                (22, 23),
                (23, 22),
                (22, 24),
                (24, 22),
                (17, 22),
                (22, 17),
                (14, 19),
                (19, 14),
            ],
        ),
    ]
    partials = [
        (2, [(6, 7), (7, 6), (11, 12), (12, 11)], 1.4),
        (6, [(16, 17), (17, 16), (21, 22), (22, 21)], 1.6),
    ]
    return rn, closures, partials


def scenario_C_high_noise(rn):
    # Same flood schedule as Scenario B, but with higher misinformation noise.
    closures = [
        (3, [(1, 5), (5, 1), (5, 9), (9, 5)]),
        (7, [(2, 6), (6, 2), (6, 10)]),
    ]
    partials = [
        (2, [(0, 1), (1, 0), (4, 5), (5, 4)], 1.6),
        (6, [(8, 9), (9, 8), (8, 10), (9, 10)], 1.8),
    ]
    return rn, closures, {"error_prob": 0.4}, partials


def scenario_C_high_noise_stress(rn):
    """Same stress flood schedule as scenario_B_flood_stress with higher report noise."""
    rn, closures, partials = scenario_B_flood_stress(rn)
    return rn, closures, {"error_prob": 0.4}, partials
