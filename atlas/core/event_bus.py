"""Serial async event bus with durable per-subscriber delivery receipts.

Handlers must limit mutations to the shared database and return output events.
External side effects require the Gateway's own idempotent execution protocol.
"""

import asyncio
import time

from atlas.core.events import Event


class EventBus:
    def __init__(self, db, ledger):
        self.db, self.ledger = db, ledger
        self.handlers = {}
        self._lock = asyncio.Lock()
        db.connection.execute(
            "CREATE TABLE IF NOT EXISTS deliveries (event_id TEXT, subscriber TEXT, PRIMARY KEY(event_id,subscriber))"
        )
        # Which event's handling produced which. Kept beside the ledger, not in it: the same output
        # can be re-produced from a different parent, and event content must stay identical.
        db.connection.execute(
            "CREATE TABLE IF NOT EXISTS event_causes (event_id TEXT PRIMARY KEY, cause_id TEXT NOT NULL,"
            " subscriber TEXT NOT NULL)"
        )
        # One row per handler run, for the developer view: how long it took, whether it failed, and how
        # many events it produced. Written with the run's own transaction, so a rolled-back run leaves
        # no success row (a failure row is written after the rollback).
        db.connection.execute(
            "CREATE TABLE IF NOT EXISTS event_spans (event_id TEXT NOT NULL, subscriber TEXT NOT NULL,"
            " kind TEXT NOT NULL, started_at REAL NOT NULL, duration_ms REAL NOT NULL,"
            " ok INTEGER NOT NULL, error TEXT, outputs INTEGER NOT NULL DEFAULT 0,"
            " PRIMARY KEY (event_id, subscriber, kind, started_at))"
        )

    def subscribe(self, name, event_types, handler):
        if name in self.handlers:
            raise ValueError(f"Duplicate subscriber: {name}")
        self.handlers[name] = (set(event_types), handler)

    def record_span(self, event_id, subscriber, kind, started, duration_ms, ok, error=None, outputs=0):
        """Timing for one unit of work on an event (a handler run, an executor call, a model call)."""
        self.db.connection.execute(
            "INSERT OR IGNORE INTO event_spans VALUES (?,?,?,?,?,?,?,?)",
            (event_id, subscriber, kind, started, round(duration_ms, 3), int(ok), error, outputs),
        )

    def _record(self, event):
        return self.ledger.append("event:" + event.id, "event", event.model_dump(mode="json"))

    async def publish(self, event: Event):
        async with self._lock:
            inserted = self._record(event)
            await self._drain()
            return inserted

    async def recover(self):
        async with self._lock:
            await self._drain()

    async def _drain(self):
        # Failed handlers leave their input pending; COP, output events and receipt
        # commit together. Register subscribers before recovery on process startup.
        while True:
            progressed = False
            for row in self.ledger.records("event"):
                event = Event.model_validate(row["payload"])
                for name, (types, handler) in self.handlers.items():
                    if event.type not in types:
                        continue
                    if self.db.connection.execute(
                        "SELECT 1 FROM deliveries WHERE event_id=? AND subscriber=?",
                        (event.id, name),
                    ).fetchone():
                        continue
                    started, clock = time.time(), time.perf_counter()
                    try:
                        with self.db.transaction():
                            outputs = await handler(event)
                            for output in outputs or []:
                                self._record(output)
                                self.db.connection.execute(
                                    "INSERT OR IGNORE INTO event_causes VALUES (?,?,?)",
                                    (output.id, event.id, name),
                                )
                            self.record_span(
                                event.id, name, "handler", started,
                                (time.perf_counter() - clock) * 1000, True, None, len(outputs or []),
                            )
                            self.db.connection.execute(
                                "INSERT INTO deliveries VALUES (?,?)", (event.id, name)
                            )
                    except BaseException as exc:  # the transaction has rolled back; keep the evidence
                        self.record_span(
                            event.id, name, "handler", started,
                            (time.perf_counter() - clock) * 1000, False,
                            f"{type(exc).__name__}: {exc}"[:500], 0,
                        )
                        raise
                    progressed = True
            if not progressed:
                return
