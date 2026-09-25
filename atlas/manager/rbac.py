"""Permissions, roles and who is calling.  (Owner: Dinuka)

A permission is ``resource:verb``, every route declares the one
it needs at registration time (``authz(...)``, not inside the handler), and roles are predefined bundles.
A permission no role grants is unreachable, and a test checks there are none.

Identity comes from a bearer token. Configure tokens with ``ATLAS_TOKENS`` (JSON list of
``{"token": ..., "name": ..., "role": ...}``) or the older single ``ATLAS_OPERATOR_TOKEN`` (admin). With
neither set the API is open in *development mode* and every caller is an admin, which ``/am/v1/me``
says out loud. Secrets are never defaulted: an empty token is refused.

Reads stay open under the single operator token (the original behaviour); once ``ATLAS_TOKENS`` is set,
or ``ATLAS_AUTH=strict``, reads need their ``*:read`` permission too.
"""

from __future__ import annotations

import hmac
import json
import os
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, Header, HTTPException, Request

# ---- permissions ---------------------------------------------------------------------------

AGENT_READ, AGENT_SUSPEND, AGENT_RESUME = "agent:read", "agent:suspend", "agent:resume"
LLM_CONFIGURE = "llm:configure"
RUN_READ, TRACE_READ, TRACE_EXPORT = "run:read", "trace:read", "trace:export"
EVAL_READ, EVAL_RUN, MONITOR_MANAGE, EXPERIMENT_RUN = (
    "eval:read",
    "eval:run",
    "monitor:manage",
    "experiment:run",
)
APPROVAL_READ, APPROVAL_DECIDE = "approval:read", "approval:decide"
MISSION_READ, MISSION_UPDATE, ALERT_READ = "mission:read", "mission:update", "alert:read"
SCENARIO_READ, SCENARIO_RUN = "scenario:read", "scenario:run"
REDTEAM_RUN, WHATIF_RUN, INJECT_CREATE = "redteam:run", "whatif:run", "inject:create"
AUDIT_READ, GUARDRAIL_READ, LEDGER_READ, SETTINGS_READ = (
    "audit:read",
    "guardrail:read",
    "ledger:read",
    "settings:read",
)

READS = {
    AGENT_READ,
    RUN_READ,
    TRACE_READ,
    EVAL_READ,
    APPROVAL_READ,
    MISSION_READ,
    ALERT_READ,
    SCENARIO_READ,
    GUARDRAIL_READ,
    LEDGER_READ,
    SETTINGS_READ,
}
ALL = READS | {
    LLM_CONFIGURE,
    AGENT_SUSPEND,
    AGENT_RESUME,
    TRACE_EXPORT,
    EVAL_RUN,
    MONITOR_MANAGE,
    EXPERIMENT_RUN,
    APPROVAL_DECIDE,
    MISSION_UPDATE,
    SCENARIO_RUN,
    REDTEAM_RUN,
    WHATIF_RUN,
    INJECT_CREATE,
    AUDIT_READ,
}

ROLES: dict[str, frozenset[str]] = {
    "admin": frozenset(ALL),
    # the Human Incident Commander: decides, and follows the incident, but does not run experiments
    "commander": frozenset(READS | {APPROVAL_DECIDE, MISSION_UPDATE, AGENT_SUSPEND, AGENT_RESUME}),
    # builds and tests the system
    "developer": frozenset(
        READS
        | {
            SCENARIO_RUN,
            REDTEAM_RUN,
            WHATIF_RUN,
            INJECT_CREATE,
            EVAL_RUN,
            EXPERIMENT_RUN,
            TRACE_EXPORT,
        }
    ),
    # owns evaluation quality
    "ai_lead": frozenset(
        READS | {EVAL_RUN, MONITOR_MANAGE, EXPERIMENT_RUN, TRACE_EXPORT, WHATIF_RUN}
    ),
    # runs the platform
    "platform_engineer": frozenset(
        READS | {AGENT_SUSPEND, AGENT_RESUME, AUDIT_READ, TRACE_EXPORT, MONITOR_MANAGE}
    ),
    "viewer": frozenset(READS - {SETTINGS_READ}),
}
ROLES["commander"] = frozenset(ROLES["commander"] | {AUDIT_READ})


def ungranted() -> set[str]:
    """Permissions no role grants. Must be empty."""
    return ALL - set().union(*ROLES.values())


# ---- callers -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    dev_mode: bool = False

    @property
    def permissions(self) -> frozenset[str]:
        return ROLES[self.role]


def _configured() -> tuple[dict[str, Principal], bool, bool]:
    """(token -> principal, reads_are_protected, any_token_configured)."""
    tokens: dict[str, Principal] = {}
    raw = os.getenv("ATLAS_TOKENS", "").strip()
    strict = os.getenv("ATLAS_AUTH", "").lower() == "strict"
    if raw:
        for item in json.loads(raw):
            if not item.get("token"):
                raise ValueError("ATLAS_TOKENS contains an empty token")
            if item["role"] not in ROLES:
                raise ValueError(f"unknown role {item['role']!r}; roles are {sorted(ROLES)}")
            tokens[item["token"]] = Principal(item.get("name", item["role"]), item["role"])
        strict = True
    legacy = os.getenv("ATLAS_OPERATOR_TOKEN")
    if legacy:
        tokens.setdefault(legacy, Principal("operator", "admin"))
    return tokens, strict, bool(tokens)


def identify(
    request: Request, authorization: str | None, x_operator_token: str | None
) -> Principal:
    tokens, _, configured = _configured()
    if not configured:
        principal = Principal("dev", "admin", dev_mode=True)
    else:
        presented = x_operator_token or (
            authorization.split(" ", 1)[1]
            if authorization and authorization.lower().startswith("bearer ")
            else ""
        )
        match = next(
            (p for t, p in tokens.items() if hmac.compare_digest(presented or "", t)), None
        )
        principal = match  # type: ignore[assignment]
    request.state.principal = principal
    return principal


def authz(permission: str) -> Callable:
    """A FastAPI dependency that requires ``permission``. Declared per route, where the route is."""

    def check(
        request: Request,
        authorization: str | None = Header(default=None),
        x_operator_token: str | None = Header(default=None),
    ) -> Principal:
        request.state.permission = permission
        _, strict, configured = _configured()
        is_read = permission in READS
        if is_read and not strict:
            if not configured or authorization or x_operator_token:
                principal = identify(request, authorization, x_operator_token)
                if principal is None:
                    raise HTTPException(401, "a valid token is required")
                request.state.principal = principal
            else:
                request.state.principal = Principal("anonymous", "viewer")
            return request.state.principal
        principal = identify(request, authorization, x_operator_token)
        if principal is None:
            raise HTTPException(401, "a valid token is required")
        if permission not in principal.permissions:
            raise HTTPException(403, f"{principal.role} may not {permission}")
        return principal

    check.atlas_permission = permission  # type: ignore[attr-defined]
    return check


def requires(permission: str) -> list:
    return [Depends(authz(permission))]
