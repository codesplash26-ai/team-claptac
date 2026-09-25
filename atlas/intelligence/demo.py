"""Offline replay and evidence demonstration: python -m atlas.intelligence.demo --bad-model."""

import argparse
import asyncio
import json
from pathlib import Path

from atlas.core.config import Settings
from atlas.ingest.simulator import ReplayClock, replay
from atlas.runtime import Runtime
from atlas.system import attach_all
from atlas.tools.llm import LLMResult


class DeliberatelyBadProvider:
    available = True
    name = "deliberately-bad-demo"
    model = "fake-no-network"

    async def complete(self, *, messages, **kwargs):
        fact = json.loads(messages[0]["content"])["facts"][0]
        return LLMResult(
            json.dumps(
                {
                    "claims": [
                        {"text": "A supplied fact", "claim": fact},
                        {
                            "text": "An invented entity",
                            "claim": {**fact, "subject": "shelter:invented"},
                        },
                        {
                            "text": "A false value with a real citation",
                            "claim": {**fact, "object": "invented-value"},
                        },
                    ]
                }
            )
        )


async def run(path, *, bad_model=False):
    clock = ReplayClock()
    runtime = Runtime(Settings(database_path=":memory:"), clock=clock)
    system = attach_all(runtime)
    # This demo is always offline, even on a machine with model credentials configured.
    system.intelligence.provider = DeliberatelyBadProvider() if bad_model else None
    try:
        await runtime.start()
        await replay(runtime, json.loads(Path(path).read_text()), clock)
        await system.intelligence.tick()
        advice = system.intelligence.list()
        return {
            "scenario": str(path),
            "provider": "fake" if bad_model else "none",
            "incidents": len(system.orchestration.commander.incidents()),
            "ledger_valid": runtime.ledger.verify(),
            "accepted_claims": sum(c.accepted for a in advice for c in a.claims),
            "rejected_claims": sum(not c.accepted for a in advice for c in a.claims),
            "advice": [a.model_dump(mode="json") for a in advice],
        }
    finally:
        await runtime.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="data/scenarios/flood.json")
    parser.add_argument("--bad-model", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run(args.scenario, bad_model=args.bad_model))
    print(json.dumps(result, indent=2))
    if not result["ledger_valid"] or not result["advice"]:
        raise SystemExit(1)
    if args.bad_model and not (result["accepted_claims"] and result["rejected_claims"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
