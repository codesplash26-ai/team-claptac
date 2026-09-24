"""Shelter and response-resource reference data used by A7 (shelters) and A6 (logistics).

Everything in `data/reference/` is SYNTHETIC. Positions are real nodes from the cached OSM graphs
(so routing works), chosen deterministically from high ground; names, capacities and fleets are
illustrative placeholders, not real facilities. Replace the files with real DMC / district data to
go beyond a demo; the loaders and schemas stay the same.

Regenerate (deterministic, offline):   python -m atlas.tools.reference generate
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from atlas.tools import geo

REFERENCE_DIR = geo.ROOT / "data" / "reference"
SYNTHETIC_NOTE = (
    "SYNTHETIC demonstration data. Nodes are real OSM nodes chosen from high ground; names, "
    "capacities and fleets are placeholders, not real facilities or assets."
)

# Illustrative sizes, in descending order of rank (highest ground first).
SHELTER_CAPACITIES = (120, 80, 60, 40)
FLEET = (
    ("rescue_team", 3, 6),  # (kind, count, members per team)
    ("vehicle", 4, 20),  # people carried per trip
)


class Shelter(BaseModel):
    id: str
    site: str
    node: int
    lat: float
    lon: float
    elevation_m: float
    capacity: int = Field(gt=0)
    kind: str = "community_shelter"
    synthetic: bool = True


class Resource(BaseModel):
    id: str
    site: str
    kind: Literal["rescue_team", "vehicle"]
    node: int  # depot location
    capacity: int = Field(gt=0)  # team members, or people carried per trip
    synthetic: bool = True


def _read(name: str, key: str, path: Path | None):
    path = path or REFERENCE_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))[key]


def load_shelters(site: str | None = None, path: Path | None = None) -> list[Shelter]:
    shelters = [Shelter.model_validate(s) for s in _read("shelters", "shelters", path)]
    return [s for s in shelters if site in (None, s.site)]


def load_resources(site: str | None = None, path: Path | None = None) -> list[Resource]:
    resources = [Resource.model_validate(r) for r in _read("resources", "resources", path)]
    return [r for r in resources if site in (None, r.site)]


def generate(data_dir: Path | None = None, osm_dir: Path | None = None) -> tuple[Path, Path]:
    """Write shelters.json and resources.json from the cached graphs (deterministic)."""
    shelters: list[dict] = []
    resources: list[dict] = []
    for name in sorted(geo.SITES):
        rg = geo.load_site(name, osm_dir)
        picks = geo.suggest_shelter_nodes(rg, len(SHELTER_CAPACITIES), min_separation_m=250.0)
        for i, (node, capacity) in enumerate(zip(picks, SHELTER_CAPACITIES), start=1):
            lat, lon = rg.pos(node)
            shelters.append(
                Shelter(
                    id=f"{name}-shelter-{i:02d}",
                    site=name,
                    node=node,
                    lat=lat,
                    lon=lon,
                    elevation_m=rg.elevation(node),
                    capacity=capacity,
                ).model_dump()
            )
        # One depot per site, at the node nearest the site centre; all units start there.
        depot = rg.nearest_node(*rg.center)
        for kind, count, capacity in FLEET:
            for i in range(1, count + 1):
                resources.append(
                    Resource(
                        id=f"{name}-{kind.replace('_', '-')}-{i:02d}",
                        site=name,
                        kind=kind,
                        node=depot,
                        capacity=capacity,
                    ).model_dump()
                )

    out = data_dir or REFERENCE_DIR
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, key, rows in (
        ("shelters", "shelters", shelters),
        ("resources", "resources", resources),
    ):
        path = out / f"{name}.json"
        path.write_text(
            json.dumps({"_note": SYNTHETIC_NOTE, key: rows}, indent=2) + "\n", encoding="utf-8"
        )
        paths.append(path)
    return paths[0], paths[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic shelter/resource data.")
    parser.add_argument("cmd", choices=["generate"])
    parser.parse_args(argv)
    shelters, resources = generate()
    print(f"wrote {shelters} and {resources}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
