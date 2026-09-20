"""Action Gateway: preflight -> authorise/escalate -> execute with verify/retry/compensate.

Two phases, because the event bus runs handlers inside one database transaction:

* decide  - ``submit()`` / the PLAN_PROPOSED subscriber. Database and ledger writes only,
  safe inside a bus handler. Returns a Decision; the events describing it are returned
  (never published) so the caller can hand them back to the bus.
* execute - ``run_pending()``. Runs outside any handler. Calls the registered A8/A9
  executors with verify, retry and compensate, and publishes what they produce.

Every step is a ledger record under ``action:<idempotency_key>:<phase>``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from datetime import datetime, timezone
from hashlib import sha256
from typing import Awaitable, Callable, Protocol

from pydantic import ValidationError

from atlas.control.approvals import ApprovalError, ApprovalQueue
from atlas.control.models import (
    ApprovalStatus,
    Decision,
    DecisionStatus,
    ExecutionResult,
    Verdict,
)
from atlas.control.policy_engine import PolicyEngine
from atlas.core.events import Consequence, Event, EventType, ProposedAction
from atlas.core.storage import Database
from atlas.memory.ledger import Ledger, canonical

log = logging.getLogger(__name__)
NAME = "action_gateway"
PENDING_EXECUTION = (DecisionStatus.AUTHORISED.value, DecisionStatus.EXECUTING.value)


class Executor(Protocol):
    """An action executor (A8/A9). Must be idempotent on ``action.idempotency_key``.

    ``compensate`` receives the last result, or None if ``execute`` never returned,
    and must tolerate being called for work that only partly happened.
    """

    def execute(self, action: ProposedAction) -> ExecutionResult | Awaitable[ExecutionResult]: ...
    def verify(self, action: ProposedAction, result: ExecutionResult) -> bool | Awaitable[bool]: ...
    def compensate(
        self, action: ProposedAction, result: ExecutionResult | None
    ) -> None | Awaitable[None]: ...


def fingerprint(action: ProposedAction) -> str:
    """Identity of a proposal for idempotency: what it asks for, not how it was labelled."""
    core = action.model_dump(
        mode="json", include={"incident_id", "proposed_by", "executor", "action_type", "params"}
    )
    return sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class Gateway:
    name = NAME
    subscribes_to = (EventType.PLAN_PROPOSED,)

    def __init__(
        self,
        db: Database,
        ledger: Ledger,
        policy: PolicyEngine,
        approvals: ApprovalQueue,
        *,
        cop=None,
        publish: Callable[[Event], Awaitable] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.db, self.ledger, self.policy, self.approvals = db, ledger, policy, approvals
        self.cop, self.publish = cop, publish
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.executors: dict[str, Executor] = {}
        self._run_lock = asyncio.Lock()
        db.connection.executescript("""
            CREATE TABLE IF NOT EXISTS gateway_actions (
                idempotency_key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                action TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                consequence TEXT, approval_id TEXT, decided_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, receipt TEXT);
            CREATE INDEX IF NOT EXISTS gateway_actions_status ON gateway_actions(status);
        """)

    def register_executor(self, name: str, executor: Executor) -> None:
        self.executors[name] = executor

    # ---------------------------------------------------------------- decide

    def submit(self, action: ProposedAction) -> Decision:
        """Preflight and authorise one proposal. Synchronous, database-only, idempotent."""
        now = self.clock()
        key = action.idempotency_key
        if not key.strip():
            return self._reject_unkeyed(action, "missing_idempotency_key", now)
        with self.db.transaction():
            row = self._row(key)
            if row is not None:
                if row["fingerprint"] != fingerprint(action):
                    return self._key_reuse(action, now)
                return self._decision(row)
            (verdict, why), consequence, approval_reason = self._assess(action)
            status = {
                Verdict.BLOCK: DecisionStatus.BLOCKED,
                Verdict.ESCALATE: DecisionStatus.ESCALATED,
                Verdict.ALLOW: DecisionStatus.AUTHORISED,
            }[verdict]
            if why == "invalid_params":  # keep unserialisable values out of the ledger and bus
                stored = action.model_copy(update={"params": {"_redacted": why}})
            else:
                stored = action
            approval_id = None
            if status is DecisionStatus.ESCALATED:
                approval_id = self.approvals.enqueue(
                    stored.model_dump(mode="json"), approval_reason, now
                ).id
            self.db.connection.execute(
                "INSERT INTO gateway_actions(idempotency_key, fingerprint, action, status, reason,"
                " consequence, approval_id, decided_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    fingerprint(action),
                    canonical(stored.model_dump(mode="json")),
                    status.value,
                    why,
                    consequence.value if consequence else None,
                    approval_id,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            self._ledger(
                status.value,
                stored,
                now,
                reason=why,
                consequence=consequence.value if consequence else None,
                approval_id=approval_id,
            )
            return self._decision(self._row(key))

    def _assess(self, action: ProposedAction):
        """Return ((verdict, reason), consequence, approval_reason)."""
        for name in ("incident_id", "proposed_by", "executor", "action_type"):
            if not str(getattr(action, name)).strip():
                return (Verdict.BLOCK, f"missing_{name}"), None, ""
        try:
            canonical(action.params)
        except (TypeError, ValueError):
            return (Verdict.BLOCK, "invalid_params"), None, ""
        result = self.policy.evaluate(action)
        if result.verdict is Verdict.BLOCK:
            return (Verdict.BLOCK, result.reason), None, ""
        if (
            action.snapshot_version is not None
            and self.policy.policy.gateway.require_current_snapshot
            and self.cop is not None
            and action.snapshot_version != self.cop.version
        ):
            return (Verdict.BLOCK, "stale_snapshot"), result.consequence, ""
        return (result.verdict, result.reason), result.consequence, result.reason

    def _reject_unkeyed(self, action, reason, now) -> Decision:
        self.ledger.append(
            f"action:unkeyed:{action.id}:blocked",
            "action",
            {
                "phase": "blocked",
                "action_id": action.id,
                "reason": reason,
                "incident_id": action.incident_id,
            },
        )
        return Decision(
            action_id=action.id,
            idempotency_key=action.idempotency_key,
            incident_id=action.incident_id,
            status=DecisionStatus.BLOCKED,
            reason=reason,
            decided_at=now,
            updated_at=now,
        )

    def _key_reuse(self, action, now) -> Decision:
        """Same key, different content: refuse, keep the original decision intact."""
        self._ledger(
            "key_reuse",
            action,
            None,
            reason="idempotency_key_reuse",
            record_suffix=fingerprint(action)[:12],
            stable=True,
        )
        return Decision(
            action_id=action.id,
            idempotency_key=action.idempotency_key,
            incident_id=action.incident_id,
            status=DecisionStatus.BLOCKED,
            reason="idempotency_key_reuse",
            decided_at=now,
            updated_at=now,
        )

    async def handle_plan(self, event: Event) -> list[Event]:
        """Bus subscriber for PLAN_PROPOSED; payload ``{"actions": [ProposedAction, ...]}``."""
        out: list[Event] = []
        for index, raw in enumerate(event.payload.get("actions", [])):
            try:
                action = ProposedAction.model_validate(raw)
            except ValidationError:
                now = self.clock()
                self.ledger.append(
                    f"action:malformed:{event.id}:{index}",
                    "action",
                    {"phase": "blocked", "reason": "malformed_action", "event_id": event.id},
                )
                out.append(
                    Event(
                        id=sha256(f"{event.id}:{index}:malformed".encode()).hexdigest(),
                        type=EventType.ACTION_BLOCKED,
                        source=NAME,
                        incident_id=event.incident_id,
                        created_at=now,
                        payload={"reason": "malformed_action", "index": index},
                    )
                )
                continue
            out.extend(self.events_for(self.submit(action), action))
        return out

    def events_for(self, decision: Decision, action: ProposedAction | None = None) -> list[Event]:
        """The events that announce a decision. Deterministic, so replays deduplicate."""
        kind = {
            DecisionStatus.AUTHORISED: EventType.ACTION_AUTHORISED,
            DecisionStatus.ESCALATED: EventType.ACTION_ESCALATED,
            DecisionStatus.BLOCKED: EventType.ACTION_BLOCKED,
            DecisionStatus.REJECTED: EventType.ACTION_BLOCKED,
            DecisionStatus.COMPENSATED: EventType.ACTION_BLOCKED,
            DecisionStatus.FAILED_UNCOMPENSATED: EventType.ACTION_BLOCKED,
        }.get(decision.status)
        if kind is None:
            return []
        row = self._row(decision.idempotency_key)
        stored = json.loads(row["action"]) if row else None
        body = stored or (action.model_dump(mode="json") if action else None)
        return [
            Event(
                id=sha256(
                    f"{decision.action_id}:{kind.value}:{decision.status.value}".encode()
                ).hexdigest(),
                type=kind,
                source=NAME,
                incident_id=decision.incident_id,
                created_at=decision.updated_at,
                payload={"decision": decision.model_dump(mode="json"), "action": body},
            )
        ]

    async def emit(self, decision: Decision) -> None:
        """Publish a decision's events. Only from outside a bus handler (e.g. the API)."""
        if self.publish is None:
            return
        for event in self.events_for(decision):
            await self.publish(event)

    # -------------------------------------------------------- human resolution

    def approve(self, approval_id: str, reviewer: str, note: str = "") -> Decision:
        return self._resolve_approval(approval_id, reviewer, note, None)

    def edit(self, approval_id: str, reviewer: str, params: dict, note: str = "") -> Decision:
        """Approve with changed params. Only params are editable, and policy re-runs on them."""
        return self._resolve_approval(approval_id, reviewer, note, params)

    def reject(self, approval_id: str, reviewer: str, note: str = "") -> Decision:
        now = self.clock()
        with self.db.transaction():
            item, action = self._pending(approval_id)
            self.approvals.resolve(approval_id, ApprovalStatus.REJECTED, reviewer, now, note)
            return self._settle(
                action,
                DecisionStatus.REJECTED,
                "human_rejected",
                now,
                phase="rejected",
                reviewer=reviewer,
                note=note,
            )

    def _resolve_approval(self, approval_id, reviewer, note, params) -> Decision:
        now = self.clock()
        with self.db.transaction():
            item, action = self._pending(approval_id)
            edited = params is not None
            if edited:
                action = action.model_copy(update={"params": params})
                try:
                    canonical(params)
                    verdict = self.policy.evaluate(action)
                except (TypeError, ValueError):
                    verdict = None
                if verdict is None or verdict.verdict is Verdict.BLOCK:
                    reason = "edit_invalid_params" if verdict is None else f"edit_{verdict.reason}"
                    self.approvals.resolve(
                        approval_id,
                        ApprovalStatus.REJECTED,
                        reviewer,
                        now,
                        f"{note} [{reason}]".strip(),
                    )
                    return self._settle(
                        action,
                        DecisionStatus.REJECTED,
                        reason,
                        now,
                        phase="rejected",
                        reviewer=reviewer,
                        note=note,
                    )
            self.approvals.resolve(
                approval_id,
                ApprovalStatus.EDITED if edited else ApprovalStatus.APPROVED,
                reviewer,
                now,
                note,
                action.model_dump(mode="json") if edited else None,
            )
            return self._settle(
                action,
                DecisionStatus.AUTHORISED,
                "human_edited" if edited else "human_approved",
                now,
                phase="edited" if edited else "approved",
                reviewer=reviewer,
                note=note,
                edited_params=params if edited else None,
            )

    def _pending(self, approval_id):
        item = self.approvals.get(approval_id)
        if item.status is not ApprovalStatus.PENDING:
            raise ApprovalError(f"approval {approval_id} already {item.status.value}")
        return item, ProposedAction.model_validate(item.action)

    def _settle(self, action, status, reason, now, *, phase, **extra) -> Decision:
        self.db.connection.execute(
            "UPDATE gateway_actions SET status=?, reason=?, action=?, updated_at=? "
            "WHERE idempotency_key=?",
            (
                status.value,
                reason,
                canonical(action.model_dump(mode="json")),
                now.isoformat(),
                action.idempotency_key,
            ),
        )
        self._ledger(phase, action, now, reason=reason, **extra)
        return self._decision(self._row(action.idempotency_key))

    # ----------------------------------------------------------------- execute

    async def run_pending(self, limit: int = 50) -> int:
        """Execute authorised actions. Also resumes any left ``executing`` by a crash."""
        async with self._run_lock:
            rows = self.db.connection.execute(
                "SELECT idempotency_key FROM gateway_actions WHERE status IN (?,?) "
                "ORDER BY decided_at, idempotency_key LIMIT ?",
                (*PENDING_EXECUTION, limit),
            ).fetchall()
            done = 0
            for row in rows:
                done += await self._execute(row["idempotency_key"])
            return done

    async def _execute(self, key: str) -> int:
        row = self._row(key)
        action = ProposedAction.model_validate_json(row["action"])
        executor = self.executors.get(action.executor)
        if executor is None:
            log.warning("no executor registered for %s; %s stays authorised", action.executor, key)
            return 0
        cfg = self.policy.policy.gateway
        result, error = None, "not_attempted"
        for attempt in range(1, cfg.max_retries + 2):
            self._transition(
                key,
                DecisionStatus.EXECUTING,
                f"attempt_{attempt}",
                phase=f"attempt:{attempt}",
                action=action,
                unique=True,
            )
            verified = False
            try:
                result = await _resolve(executor.execute(action))
                verified = bool(await _resolve(executor.verify(action, result)))
                error = "verification_failed"
            except Exception as exc:  # executors are third-party code; contain, then retry
                error = f"{type(exc).__name__}: {exc}"
            if verified:
                return await self._succeed(key, action, result)
            log.warning("execution of %s failed (attempt %d): %s", key, attempt, error)
            if attempt <= cfg.max_retries:
                await asyncio.sleep(cfg.retry_backoff_seconds * attempt)
        return await self._fail(key, action, executor, result, error)

    async def _succeed(self, key, action, result) -> int:
        self._transition(
            key,
            DecisionStatus.EXECUTED,
            "executed",
            phase="executed",
            action=action,
            receipt=result.receipt.model_dump(mode="json"),
        )
        for event in result.events:
            if self.publish is None:
                break
            try:
                await self.publish(event)
            except Exception:
                log.exception("could not publish %s for %s", event.type, key)
        return 1

    async def _fail(self, key, action, executor, result, error) -> int:
        try:
            await _resolve(executor.compensate(action, result))
        except Exception as exc:
            self._transition(
                key,
                DecisionStatus.FAILED_UNCOMPENSATED,
                "compensation_failed",
                phase="compensation_failed",
                action=action,
                error=error,
                compensation_error=f"{type(exc).__name__}: {exc}",
            )
        else:
            self._transition(
                key,
                DecisionStatus.COMPENSATED,
                "execution_failed",
                phase="compensated",
                action=action,
                error=error,
            )
        if self.publish:
            await self.emit(self._decision(self._row(key)))
        return 1

    def _transition(
        self, key, status, reason, *, phase, action, receipt=None, unique=False, **extra
    ):
        now = self.clock()
        with self.db.transaction():
            self.db.connection.execute(
                "UPDATE gateway_actions SET status=?, reason=?, updated_at=?, "
                "receipt=COALESCE(?, receipt) WHERE idempotency_key=?",
                (
                    status.value,
                    reason,
                    now.isoformat(),
                    canonical(receipt) if receipt else None,
                    key,
                ),
            )
            self._ledger(
                phase,
                action,
                now,
                reason=reason,
                receipt=receipt,
                record_suffix=now.isoformat() if unique else "",
                **extra,
            )

    # ----------------------------------------------------------------- helpers

    def _row(self, key):
        return self.db.connection.execute(
            "SELECT * FROM gateway_actions WHERE idempotency_key=?", (key,)
        ).fetchone()

    def get(self, key: str) -> Decision | None:
        row = self._row(key)
        return self._decision(row) if row else None

    def list_actions(self, incident_id: str | None = None) -> list[dict]:
        """Every action with its current decision, oldest first (for the API and dashboard)."""
        rows = self.db.connection.execute(
            "SELECT * FROM gateway_actions ORDER BY decided_at, idempotency_key"
        ).fetchall()
        out = []
        for row in rows:
            action = json.loads(row["action"])
            if incident_id is None or action["incident_id"] == incident_id:
                out.append(
                    {
                        "action": action,
                        "decision": self._decision(row).model_dump(mode="json"),
                        "receipt": json.loads(row["receipt"]) if row["receipt"] else None,
                    }
                )
        return out

    def _decision(self, row) -> Decision:
        action = json.loads(row["action"])
        return Decision(
            action_id=action["id"],
            idempotency_key=row["idempotency_key"],
            incident_id=action["incident_id"],
            status=DecisionStatus(row["status"]),
            reason=row["reason"],
            consequence=Consequence(row["consequence"]) if row["consequence"] else None,
            approval_id=row["approval_id"],
            decided_at=datetime.fromisoformat(row["decided_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _ledger(self, phase, action, now, *, record_suffix="", stable=False, **extra):
        record = f"action:{action.idempotency_key}:{phase}" + (
            f":{record_suffix}" if record_suffix else ""
        )
        self.ledger.append(
            record,
            "action",
            {
                "phase": phase,
                "idempotency_key": action.idempotency_key,
                **({} if stable else {"action_id": action.id}),
                "incident_id": action.incident_id,
                "executor": action.executor,
                "action_type": action.action_type,
                **({"at": now.isoformat()} if now else {}),
                **{k: v for k, v in extra.items() if v is not None},
            },
        )
