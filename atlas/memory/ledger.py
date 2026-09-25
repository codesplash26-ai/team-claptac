"""Append-only, hash-linked journal. Record IDs deduplicate records, not action phases."""

import hashlib
import json

from atlas.core.storage import Database


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Ledger:
    def __init__(self, db: Database):
        self.db = db
        db.connection.executescript("""
            CREATE TABLE IF NOT EXISTS ledger (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL, body TEXT NOT NULL, previous_hash TEXT NOT NULL,
                hash TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS ledger_no_update BEFORE UPDATE ON ledger
                BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
            CREATE TRIGGER IF NOT EXISTS ledger_no_delete BEFORE DELETE ON ledger
                BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
        """)

    def append(self, record_id: str, kind: str, payload: dict) -> bool:
        body = canonical(payload)
        with self.db.transaction():
            existing = self.db.connection.execute(
                "SELECT kind, body FROM ledger WHERE record_id=?", (record_id,)
            ).fetchone()
            if existing:
                if tuple(existing) != (kind, body):
                    raise ValueError("Record ID reused with different content")
                return False
            last = self.db.connection.execute(
                "SELECT hash FROM ledger ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            previous = last[0] if last else "0" * 64
            digest = hashlib.sha256(
                canonical([previous, record_id, kind, body]).encode()
            ).hexdigest()
            self.db.connection.execute(
                "INSERT INTO ledger(record_id,kind,body,previous_hash,hash) VALUES (?,?,?,?,?)",
                (record_id, kind, body, previous, digest),
            )
        return True

    def records(self, kind=None):
        rows = self.db.connection.execute(
            "SELECT * FROM ledger WHERE (? IS NULL OR kind=?) ORDER BY seq", (kind, kind)
        )
        return [{**dict(row), "payload": json.loads(row["body"])} for row in rows]

    def verify(self) -> bool:
        previous = "0" * 64
        for row in self.records():
            digest = hashlib.sha256(
                canonical([previous, row["record_id"], row["kind"], row["body"]]).encode()
            ).hexdigest()
            if row["previous_hash"] != previous or row["hash"] != digest:
                return False
            previous = digest
        return True

    def records_after(self, seq, kind, limit=1000):
        """Keyset batches for delivery; do not deserialize already-delivered history."""
        rows = self.db.connection.execute(
            "SELECT * FROM ledger WHERE seq>? AND kind=? ORDER BY seq LIMIT ?",
            (seq, kind, limit),
        )
        return [{**dict(row), "payload": json.loads(row["body"])} for row in rows]
