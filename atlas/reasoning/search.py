"""Bounded stochastic MCTS and scenario evaluation using the same adapters.

Action nodes have multiple sampled outcome children. Backup records discounted
returns, selection uses UCB, and final selection uses the configured objective.
This intentionally omits PlanU's learned novelty and quantile regression.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from random import Random

from atlas.reasoning.contracts import (
    Budget,
    BudgetExceeded,
    PlannerConfig,
    PlanningResult,
    Transition,
)
from atlas.reasoning.returns import score, summary


@dataclass
class Branch:
    action: object
    returns: list = field(default_factory=list)
    outcomes: dict = field(default_factory=dict)


@dataclass
class Node:
    state: dict
    branches: list | None = None
    visits: int = 0


class Planner:
    def __init__(self, config=None):
        self.config = config or PlannerConfig()

    async def plan(self, request, adapter):
        cfg = self.config
        state = deepcopy(request.state)
        budget, rng = Budget(cfg), Random(cfg.seed)
        root = Node(state)
        started = time.monotonic()
        count, trace = 0, []
        reason, use_fallback = "simulation_budget", False

        async def expand(node):
            budget.check()
            if node.branches is None:
                actions = await adapter.candidates(deepcopy(node.state), budget)
                unique = {}
                for action in actions:
                    if action.id in unique:
                        raise ValueError("Candidate IDs must be unique within a state")
                    if adapter.feasible(deepcopy(node.state), action.model_copy(deep=True)):
                        unique[action.id] = action.model_copy(deep=True)
                    if len(unique) >= cfg.max_candidates:
                        break
                node.branches = [Branch(a) for a in unique.values()]
            return node.branches

        async def visit(node, depth, path):
            budget.check()
            if depth >= cfg.max_depth or adapter.terminal(deepcopy(node.state)):
                return 0.0
            branches = await expand(node)
            if not branches:
                return 0.0
            unvisited = [b for b in branches if not b.returns]
            if unvisited:
                branch = unvisited[0]
            elif cfg.mode == "scenario":
                branch = branches[node.visits % len(branches)]
            else:
                # Exploration optimizes the same objective as final selection.
                branch = max(
                    branches,
                    key=lambda b: (
                        score(b.returns, cfg) / cfg.reward_scale
                        + cfg.exploration * math.sqrt(math.log(node.visits + 1) / len(b.returns))
                    ),
                )
            # In scenario mode each root action sees the same sequence of seeds.
            # Outcome models should consume random draws consistently to get CRN.
            sample_rng = Random(cfg.seed + len(branch.returns)) if cfg.mode == "scenario" else rng
            transition = Transition.model_validate(
                await adapter.simulate(
                    deepcopy(node.state), branch.action.model_copy(deep=True), sample_rng, budget
                )
            )
            budget.check()
            total = transition.reward
            outcome_key = json.dumps(transition.state, sort_keys=True, allow_nan=False)
            path.append(
                {
                    "depth": depth,
                    "action": branch.action.model_dump(mode="json"),
                    "reward": transition.reward,
                    "terminal": transition.terminal,
                    "outcome_key": sha256(outcome_key.encode()).hexdigest(),
                }
            )
            if not transition.terminal and cfg.mode == "mcts":
                child = branch.outcomes.setdefault(outcome_key, Node(deepcopy(transition.state)))
                total += cfg.discount * await visit(child, depth + 1, path)
            branch.returns.append(total)
            node.visits += 1
            return total

        async def run():
            nonlocal count
            await expand(root)
            for _ in range(cfg.simulations):
                path = []
                value = await visit(root, 0, path)
                trace.append({"simulation": count, "return": value, "steps": path})
                count += 1
                await asyncio.sleep(0)  # allow cancellation and incident scheduling

        try:
            if adapter.terminal(deepcopy(state)):
                reason = "terminal"
            else:
                await asyncio.wait_for(run(), cfg.timeout_seconds)
        except (TimeoutError, BudgetExceeded) as error:
            reason = str(error) or "timeout"
            use_fallback = True
        except Exception as error:
            reason = "adapter_error:" + type(error).__name__
            use_fallback = True

        branches = root.branches or []
        alternatives = [
            {
                "action": b.action.model_dump(mode="json"),
                **summary(b.returns),
                "score": score(b.returns, cfg) if b.returns else None,
                "outcomes": len(b.outcomes),
            }
            for b in branches
        ]
        action = None
        if reason != "terminal":
            # Insufficient coverage cannot justify a winner over unseen alternatives.
            if not branches or any(not b.returns for b in branches):
                reason = reason if use_fallback else "insufficient_coverage"
                use_fallback = True
            if not use_fallback:
                action = max(branches, key=lambda b: score(b.returns, cfg)).action
            else:
                try:
                    candidate = adapter.fallback(deepcopy(state))
                    if candidate and adapter.feasible(deepcopy(state), candidate):
                        action = candidate
                except Exception:
                    reason += ":fallback_unavailable"
        return PlanningResult(
            agent=request.agent,
            action=action,
            snapshot_version=request.snapshot_version,
            valid_until=request.valid_until,
            input_state=state,
            configuration=cfg.model_dump(mode="json"),
            evidence_ids=request.evidence_ids,
            assumptions=request.assumptions,
            alternatives=alternatives,
            simulations=count,
            model_calls=budget.model_calls,
            elapsed_seconds=time.monotonic() - started,
            stop_reason=reason,
            fallback_used=use_fallback,
            trace=trace,
        )
