"""Human Incident Commander approval queue (approve / edit / reject).

This is the persistence and state machine only. Policy re-checks and ledger writes
for a resolution belong to the Gateway, which is the only caller that mutates it.
"""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256

from atlas.control.models import ApprovalItem, ApprovalStatus
from atlas.core.storage import Database


class ApprovalError(ValueError):
    """Unknown approval, or it was already resolved."""


class ApprovalQueue:
    def __init__(self, db: Database):
        self.db = db
        db.connection.executescript("""
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                incident_id TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT,
                reviewer TEXT, note TEXT NOT NULL DEFAULT '');
            CREATE INDEX IF NOT EXISTS approvals_status ON approvals(status, created_at);
        """)

    @staticmethod
    def approval_id(idempotency_key: str) -> str:
        return "ap-" + sha256(idempotency_key.encode()).hexdigest()[:12]

    def enqueue(self, action: dict, reason: str, now: datetime) -> ApprovalItem:
        key = action["idempotency_key"]
        self.db.connection.execute(
            "INSERT OR IGNORE INTO approvals(id, idempotency_key, incident_id, action, reason,"
            " status, created_at) VALUES (?,?,?,?,?,?,?)",
            (
                self.approval_id(key),
                key,
                action["incident_id"],
                json.dumps(action, sort_keys=True),
                reason,
                ApprovalStatus.PENDING.value,
                now.isoformat(),
            ),
        )
        return self.get(self.approval_id(key))

    def get(self, approval_id: str) -> ApprovalItem:
        row = self.db.connection.execute(
            "SELECT * FROM approvals WHERE id=?", (approval_id,)
        ).fetchone()
        if row is None:
            raise ApprovalError(f"unknown approval {approval_id}")
        return ApprovalItem(**{**dict(row), "action": json.loads(row["action"])})

    def list(self, status: ApprovalStatus | None = None) -> list[ApprovalItem]:
        rows = self.db.connection.execute(
            "SELECT id FROM approvals WHERE (? IS NULL OR status=?) ORDER BY created_at, id",
            (status and status.value, status and status.value),
        ).fetchall()
        return [self.get(row["id"]) for row in rows]

    def pending(self) -> list[ApprovalItem]:
        return self.list(ApprovalStatus.PENDING)

    def resolve(
        self,
        approval_id: str,
        status: ApprovalStatus,
        reviewer: str,
        now: datetime,
        note: str = "",
        action: dict | None = None,
    ) -> ApprovalItem:
        """PENDING -> APPROVED | EDITED | REJECTED. Terminal; a second call raises."""
        if status is ApprovalStatus.PENDING:
            raise ApprovalError("cannot resolve to pending")
        if not reviewer.strip():
            raise ApprovalError("a reviewer is required")
        item = self.get(approval_id)
        if item.status is not ApprovalStatus.PENDING:
            raise ApprovalError(f"approval {approval_id} already {item.status.value}")
        self.db.connection.execute(
            "UPDATE approvals SET status=?, resolved_at=?, reviewer=?, note=?,"
            " action=COALESCE(?, action) WHERE id=?",
            (
                status.value,
                now.isoformat(),
                reviewer.strip(),
                note,
                json.dumps(action, sort_keys=True) if action is not None else None,
                approval_id,
            ),
        )
        return self.get(approval_id)
