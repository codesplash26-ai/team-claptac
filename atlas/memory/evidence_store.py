"""Local evidence store with explicit role checks; no public API exposure."""

import hashlib
import json

from atlas.memory.ledger import canonical


class EvidenceStore:
    READ_ROLES = {"sentinel", "planner", "auditor"}

    def __init__(self, db):
        self.db = db
        db.connection.execute(
            "CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )

    def put(self, payload: dict) -> str:
        body = canonical(payload)
        key = hashlib.sha256(body.encode()).hexdigest()
        self.db.connection.execute("INSERT OR IGNORE INTO evidence VALUES (?,?)", (key, body))
        return key

    def get(self, evidence_id: str, *, role: str):
        if role not in self.READ_ROLES:
            raise PermissionError("Evidence requires an internal authorized role")
        row = self.db.connection.execute(
            "SELECT body FROM evidence WHERE id=?", (evidence_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None
