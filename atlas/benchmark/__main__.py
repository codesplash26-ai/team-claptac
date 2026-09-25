import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from atlas.benchmark.runner import OUTPUTS, run_suite
from atlas.benchmark.schema import TASKS, Task, load_tasks


def main():
    parser = argparse.ArgumentParser(description="ATLAS isolated scenario benchmark")
    parser.add_argument("command", choices=["run", "validate", "schema"])
    parser.add_argument("--tasks", type=Path, default=TASKS)
    parser.add_argument("--task", action="append", help="Select task id (repeatable)")
    parser.add_argument("--seeds", default="42", help="Comma-separated integer seeds")
    parser.add_argument(
        "--output", type=Path, help="New run directory; existing directories are never overwritten"
    )
    parser.add_argument("--no-random", action="store_true")
    parser.add_argument(
        "--step-delay-ms", type=int, default=0, choices=range(0, 1001), metavar="0..1000"
    )
    args = parser.parse_args()
    if args.command == "schema":
        import json

        print(json.dumps(Task.model_json_schema(), indent=2))
        return 0
    tasks = load_tasks(args.tasks)
    if args.task:
        unknown = set(args.task) - {t.id for t in tasks}
        if unknown:
            parser.error(f"Unknown tasks: {sorted(unknown)}")
        tasks = [t for t in tasks if t.id in args.task]
    if args.command == "validate":
        print(f"Validated {len(tasks)} tasks")
        return 0
    seeds = [int(s.strip()) for s in args.seeds.split(",")]
    if (
        not 1 <= len(seeds) <= 10
        or len(set(seeds)) != len(seeds)
        or any(not 0 <= s <= 2147483647 for s in seeds)
    ):
        parser.error("Use 1..10 unique seeds between 0 and 2147483647")
    folder = args.output or OUTPUTS / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    )
    result = asyncio.run(
        run_suite(
            tasks,
            seeds,
            folder,
            random_enabled=not args.no_random,
            step_delay_ms=args.step_delay_ms,
        )
    )
    print(f"{result['passed']}/{result['total']} cases passed; state={result['state']}")
    print(f"Report: {folder.resolve() / 'report.html'}")
    return 0 if result["state"] == "completed" and result["passed"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
