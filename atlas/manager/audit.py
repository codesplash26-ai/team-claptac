"""The audit trail.  (Owner: Dinuka)

Every request that changes anything is recorded, automatically, in the append-only ledger (kind
``audit``): who (name and role), what (method, route template, the permission it needed), and how it
ended (status code). Never a request or response body, a token or any other secret. Registering a route
with ``authz(...)`` is what makes it auditable, and ``tests/manager/test_rbac_audit.py`` fails if a
mutating route exists without one, the so a new endpoint cannot ship un-audited.

Reads are not recorded (they would swamp the trail) except the few listed in ``SENSITIVE_READS``.
"""

from __future__ import annotations

import time
from uuid import uuid4

from fastapi import FastAPI, Request

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# Mutating routes that are deliberately not behind a token: sensors and field radios have no user.
PUBLIC_MUTATING = {"/ingest/sensor", "/ingest/report"}
SENSITIVE_READS = {"/am/v1/audit", "/am/v1/orgs/{org}/projects/{project}/traces/{trace_id}/otlp"}


def record(
    ledger,
    *,
    actor: str,
    role: str,
    method: str,
    route: str,
    permission: str | None,
    status: int,
    params: dict[str, str],
) -> None:
    ledger.append(
        f"audit:{uuid4().hex}",
        "audit",
        {
            "at": time.time(),
            "actor": actor,
            "role": role,
            "method": method,
            "route": route,
            "permission": permission,
            "status": status,
            "params": params,
        },
    )


def install(app: FastAPI) -> None:
    """Record every mutating (and sensitive read) request once it has been answered."""

    @app.middleware("http")
    async def audit(request: Request, call_next):
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        auditable = request.method in MUTATING or path in SENSITIVE_READS
        if auditable and path not in PUBLIC_MUTATING and hasattr(request.app.state, "runtime"):
            principal = getattr(request.state, "principal", None)
            record(
                request.app.state.runtime.ledger,
                actor=principal.name if principal else "unauthenticated",
                role=principal.role if principal else "none",
                method=request.method,
                route=path,
                permission=getattr(request.state, "permission", None),
                status=response.status_code,
                params={k: str(v) for k, v in request.path_params.items()},
            )
        return response


def entries(runtime, *, limit: int = 200, actor: str | None = None) -> list[dict]:
    rows = runtime.ledger.records("audit")
    items = [{**r["payload"], "seq": r["seq"]} for r in rows]
    if actor:
        items = [i for i in items if i["actor"] == actor]
    return items[-limit:][::-1]
