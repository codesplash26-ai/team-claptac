"""Bounded disk-backed FIFO; acknowledgement removes only the sent message."""

import json
import sqlite3
from pathlib import Path


class Outbox:
    def __init__(self, path="sensor-buffer.db", capacity=5000):
        if capacity <= 0:
            raise ValueError("Buffer capacity must be positive")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.capacity = capacity
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, body TEXT NOT NULL)"
        )
        self.db.commit()

    def __len__(self):
        return self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def append(self, payload):
        if len(self) >= self.capacity:
            raise BufferError("Outbox full; new reading rejected, retained readings unchanged")
        with self.db:
            self.db.execute(
                "INSERT INTO outbox(body) VALUES (?)", (json.dumps(payload, allow_nan=False),)
            )

    def peek(self):
        row = self.db.execute("SELECT seq,body FROM outbox ORDER BY seq LIMIT 1").fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def acknowledge(self, sequence):
        with self.db:
            self.db.execute("DELETE FROM outbox WHERE seq=?", (sequence,))

    def close(self):
        self.db.close()
