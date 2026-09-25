"""Command line for the data layer.  (Owner: Dinuka)

python -m atlas.datasets sources                 what each source is and whether it is verified
python -m atlas.datasets scenarios               list the real-disaster presets
python -m atlas.datasets scenario ratnapura-ditwah-2025 [--offline]
python -m atlas.datasets catalog [--offline]     rebuild data/reference/hazard_history.json
"""

from __future__ import annotations

import argparse

from atlas.datasets import catalog, scenarios
from atlas.datasets.coolr import Coolr
from atlas.datasets.drought import DroughtIndex
from atlas.datasets.gdacs import Gdacs
from atlas.datasets.ibtracs import Ibtracs
from atlas.datasets.lk_irrigation import LkIrrigation
from atlas.datasets.noaa_tsunami import NoaaTsunami
from atlas.datasets.open_meteo import OpenMeteo
from atlas.datasets.usgs import Usgs

ALL = [LkIrrigation, OpenMeteo, Gdacs, Ibtracs, NoaaTsunami, Usgs, DroughtIndex, Coolr]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sources")
    sub.add_parser("scenarios")
    one = sub.add_parser("scenario")
    one.add_argument("key")
    one.add_argument("--offline", action="store_true")
    hist = sub.add_parser("catalog")
    hist.add_argument("--offline", action="store_true")
    args = parser.parse_args()

    if args.command == "sources":
        for cls in ALL:
            i = cls.info
            print(
                f"{'verified  ' if i.verified else 'UNVERIFIED'}  {i.key:14} {i.hazards}  {i.licence}"
            )
    elif args.command == "scenarios":
        for p in scenarios.PRESETS.values():
            print(
                f"{p.key}: {p.title} ({p.hazard}, {p.start.date()} to {p.end.date()}, {p.speedup:g}x)"
            )
    elif args.command == "scenario":
        print("wrote", scenarios.save(args.key, offline=args.offline))
    elif args.command == "catalog":
        print("wrote", catalog.save_history(offline=args.offline))


if __name__ == "__main__":
    main()
