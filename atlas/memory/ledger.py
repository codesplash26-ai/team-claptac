"""Append-only Action Ledger with idempotency keys and receipts (source of truth)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LedgerEntry(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    idempotency_key: str
    incident_id: str
    action_type: str
    status: str  # proposed | authorised | escalated | blocked | executed | compensated
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)


class ActionLedger:
    """In-memory append-only ledger. Workstream A/D may later persist to SQLite."""

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._by_key: dict[str, LedgerEntry] = {}

    def append(self, entry: LedgerEntry) -> LedgerEntry:
        existing = self._by_key.get(entry.idempotency_key)
        if existing is not None:
            return existing
        self._entries.append(entry)
        self._by_key[entry.idempotency_key] = entry
        return entry

    def get_by_key(self, idempotency_key: str) -> LedgerEntry | None:
        return self._by_key.get(idempotency_key)

    def for_incident(self, incident_id: str) -> list[LedgerEntry]:
        return [e for e in self._entries if e.incident_id == incident_id]

    def all(self) -> list[LedgerEntry]:
        return list(self._entries)
