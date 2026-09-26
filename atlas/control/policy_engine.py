"""Policy & Authority Engine: per-agent role scope, invariants, consequence thresholds.

Pure and deterministic: no I/O, no LLM, no clock. Anything not explicitly permitted
is denied. Input is a ProposedAction; output is a PolicyVerdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.control.models import PolicyVerdict, Verdict
from atlas.core.events import Consequence, ProposedAction

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "policy.yaml"
_KNOWN_SCOPES = {"local", "district"}


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


# Each predicate answers "does this condition make the action HIGH?". Unusable input
# answers True so a malformed or omitted parameter can never lower the consequence.
PREDICATES: dict[str, Callable[[dict, Any], bool]] = {
    "resources_committed_gt": lambda params, limit: (
        not _number(params.get("resources_committed")) or params["resources_committed"] > limit
    ),
    "scope": lambda params, wanted: (
        params.get("scope") not in _KNOWN_SCOPES or params.get("scope") == wanted
    ),
    # HIGH unless the alert is local, uses one of the listed advisory instructions and carries no
    # free text. Every evacuation order and every district-wide alert therefore goes to a human.
    "unless_local_advisory": lambda params, advisory: not (
        params.get("scope") == "local"
        and params.get("instruction") in set(advisory)
        and not params.get("instruction_text")
    ),
}


class ActionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    consequence: Consequence
    when: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def known_predicates(self):
        unknown = set(self.when) - set(PREDICATES)
        if unknown:
            raise ValueError(f"unknown policy condition(s): {sorted(unknown)}")
        return self


class AgentRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_actions: list[str]


class GatewayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_retries: int = Field(default=3, ge=0)
    retry_backoff_seconds: float = Field(default=0.2, ge=0)
    require_current_snapshot: bool = True


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    proposers: list[str] = Field(default_factory=lambda: ["a3_incident_commander"])
    agents: dict[str, AgentRule]
    actions: dict[str, ActionRule]
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)

    @model_validator(mode="after")
    def agents_reference_declared_actions(self):
        for agent, rule in self.agents.items():
            missing = set(rule.allowed_actions) - set(self.actions)
            if missing:
                raise ValueError(f"{agent} allows undeclared action(s): {sorted(missing)}")
        return self

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Policy":
        data = yaml.safe_load(Path(path or DEFAULT_POLICY_PATH).read_text(encoding="utf-8"))
        return cls.model_validate(data or {})


class PolicyEngine:
    def __init__(self, policy: Policy):
        self.policy = policy

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> "PolicyEngine":
        return cls(Policy.load(path))

    def consequence_of(self, action_type: str, params: dict) -> Consequence:
        rule = self.policy.actions[action_type]
        if rule.consequence is Consequence.LOW:
            return Consequence.LOW
        high = all(PREDICATES[key](params, arg) for key, arg in rule.when.items())
        return Consequence.HIGH if high else Consequence.LOW

    def evaluate(self, action: ProposedAction) -> PolicyVerdict:
        """Order matters: identity, then role scope, then consequence."""

        def block(reason):
            return PolicyVerdict(verdict=Verdict.BLOCK, reason=reason)

        if action.proposed_by not in self.policy.proposers:
            return block("unauthorised_proposer")
        agent = self.policy.agents.get(action.executor)
        if agent is None:
            return block("unknown_executor")
        if action.action_type not in self.policy.actions:
            return block("unknown_action_type")
        if action.action_type not in agent.allowed_actions:
            return block("action_not_permitted_for_executor")
        consequence = self.consequence_of(action.action_type, action.params)
        if consequence is Consequence.HIGH:
            return PolicyVerdict(
                verdict=Verdict.ESCALATE, reason="high_consequence", consequence=consequence
            )
        return PolicyVerdict(
            verdict=Verdict.ALLOW, reason="low_consequence", consequence=consequence
        )
