"""Versioned current beliefs; snapshots are detached from the live store."""

from datetime import datetime, timedelta, timezone

from atlas.core.events import Belief
from atlas.core.storage import Database


class COP:
    def __init__(self, db: Database):
        self.db = db
        db.connection.executescript("""
            CREATE TABLE IF NOT EXISTS beliefs (
                subject TEXT, attribute TEXT, body TEXT NOT NULL,
                PRIMARY KEY(subject, attribute));
            CREATE TABLE IF NOT EXISTS cop_meta (id INTEGER PRIMARY KEY, version INTEGER);
            INSERT OR IGNORE INTO cop_meta VALUES (1, 0);
        """)

    @property
    def version(self) -> int:
        return self.db.connection.execute("SELECT version FROM cop_meta WHERE id=1").fetchone()[0]

    def put(self, belief: Belief) -> bool:
        if belief.updated_at.tzinfo is None:
            raise ValueError("Belief timestamps must include a timezone")
        if belief.ttl_seconds is not None and belief.ttl_seconds <= 0:
            raise ValueError("TTL must be positive")
        with self.db.transaction():
            old = self.get(belief.subject, belief.attribute, include_stale=True)
            if old and old.updated_at > belief.updated_at:
                return False
            if old == belief:
                return False
            self.db.connection.execute(
                "INSERT OR REPLACE INTO beliefs VALUES (?, ?, ?)",
                (belief.subject, belief.attribute, belief.model_dump_json()),
            )
            self.db.connection.execute("UPDATE cop_meta SET version=version+1 WHERE id=1")
        return True

    def get(self, subject, attribute, *, now=None, include_stale=False):
        row = self.db.connection.execute(
            "SELECT body FROM beliefs WHERE subject=? AND attribute=?", (subject, attribute)
        ).fetchone()
        if not row:
            return None
        belief = Belief.model_validate_json(row[0])
        now = now or datetime.now(timezone.utc)
        stale = belief.ttl_seconds is not None and now >= belief.updated_at + timedelta(
            seconds=belief.ttl_seconds
        )
        return belief if include_stale or not stale else None

    def snapshot(self, *, now=None) -> dict:
        now = now or datetime.now(timezone.utc)
        with self.db.transaction():
            beliefs = []
            expirations = []
            for row in self.db.connection.execute(
                "SELECT subject, attribute FROM beliefs ORDER BY subject, attribute"
            ):
                belief = self.get(*row, now=now)
                if belief:
                    beliefs.append(belief.model_dump(mode="json"))
                    if belief.ttl_seconds is not None:
                        expirations.append(
                            belief.updated_at + timedelta(seconds=belief.ttl_seconds)
                        )
            return {
                "version": self.version,
                "taken_at": now.isoformat(),
                "beliefs": beliefs,
                "valid_until": min(expirations).isoformat() if expirations else None,
            }
