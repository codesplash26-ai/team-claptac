"""Serial async event bus with durable per-subscriber delivery receipts.

Handlers must limit mutations to the shared database and return output events.
External side effects require the Gateway's own idempotent execution protocol.
"""

import asyncio

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

    def subscribe(self, name, event_types, handler):
        if name in self.handlers:
            raise ValueError(f"Duplicate subscriber: {name}")
        self.handlers[name] = (set(event_types), handler)

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
                    with self.db.transaction():
                        outputs = await handler(event)
                        for output in outputs or []:
                            self._record(output)
                            self.db.connection.execute(
                                "INSERT OR IGNORE INTO event_causes VALUES (?,?,?)",
                                (output.id, event.id, name),
                            )
                        self.db.connection.execute(
                            "INSERT INTO deliveries VALUES (?,?)", (event.id, name)
                        )
                    progressed = True
            if not progressed:
                return
