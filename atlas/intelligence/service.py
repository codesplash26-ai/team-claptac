"""Durable per-cycle advice worker and snapshot evidence lookup; no LLM bus subscriptions."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from hashlib import sha256

from atlas.core.events import Event, EventType
from atlas.intelligence.advisor import Advisor
from atlas.intelligence.context import capture
from atlas.intelligence.contracts import Advice, ReasoningStep
from atlas.intelligence.planning import compare_routes
from atlas.intelligence.settings import SettingsStore

log = logging.getLogger(__name__)
_AUTO = object()


class IntelligenceService:
    def __init__(self, system, provider=_AUTO):
        self.system, self.runtime = system, system.runtime
        self.settings = SettingsStore(persistent=self.runtime.settings.database_path != ":memory:")
        self.provider = self.settings.build_provider() if provider is _AUTO else provider
        self._task = None
        self._lock = asyncio.Lock()
        self.runtime.db.connection.executescript("""
            CREATE TABLE IF NOT EXISTS intelligence_advice (
                incident_id TEXT, cycle INTEGER, body TEXT NOT NULL, event_body TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(incident_id,cycle));
            CREATE TABLE IF NOT EXISTS intelligence_evidence (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS intelligence_budget (incident_id TEXT PRIMARY KEY, calls INTEGER);
        """)

    def save_evidence(self, context):
        with self.runtime.db.transaction():
            for ref, document in context.evidence.items():
                self.runtime.db.connection.execute(
                    "INSERT OR IGNORE INTO intelligence_evidence VALUES (?,?)",
                    (ref, json.dumps(document)),
                )

    def evidence(self, ref):
        row = self.runtime.db.connection.execute(
            "SELECT body FROM intelligence_evidence WHERE id=?", (ref,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, incident_id=None):
        rows = self.runtime.db.connection.execute(
            "SELECT body FROM intelligence_advice WHERE (? IS NULL OR incident_id=?) "
            "ORDER BY rowid DESC LIMIT 100",
            (incident_id, incident_id),
        )
        return [Advice.model_validate_json(r[0]) for r in rows]

    async def tick(self):
        async with self._lock:
            # Restart recovery publishes the identical stored event, never re-calls a model.
            pending = self.runtime.db.connection.execute(
                "SELECT event_body FROM intelligence_advice WHERE published=0"
            ).fetchall()
            for row in pending:
                await self._publish(Event.model_validate_json(row[0]))
            for incident in self.system.orchestration.commander.incidents():
                ident, cycle = incident["incident_id"], incident.get("cycle", 0)
                if (
                    not incident.get("summary")
                    or self.runtime.db.connection.execute(
                        "SELECT 1 FROM intelligence_advice WHERE incident_id=? AND cycle=?",
                        (ident, cycle),
                    ).fetchone()
                ):
                    continue
                context = capture(self.system, incident)
                version = self.runtime.cop.version
                provider = self.provider
                row = self.runtime.db.connection.execute(
                    "SELECT calls FROM intelligence_budget WHERE incident_id=?", (ident,)
                ).fetchone()
                calls = row[0] if row else 0
                if calls >= int(os.getenv("INTELLIGENCE_MAX_CALLS_PER_INCIDENT", "4")):
                    provider = None
                if provider and provider.available:
                    self.runtime.db.connection.execute(
                        "INSERT INTO intelligence_budget VALUES (?,1) ON CONFLICT(incident_id) "
                        "DO UPDATE SET calls=calls+1",
                        (ident,),
                    )
                advice = await Advisor(
                    provider, context, context, timeout=self.request_timeout
                ).advise(incident)
                if os.getenv("INTELLIGENCE_PLANNER", "false").lower() == "true":
                    comparison = await compare_routes(
                        incident,
                        self.runtime,
                        group_size=self.system.orchestration.commander.config.group_size,
                    )
                    ref = context.add("planning:" + ident, comparison)
                    advice.trace.steps.append(
                        ReasoningStep(
                            kind="check",
                            text="Shadow route comparison: " + comparison["status"],
                            refs=[ref],
                        )
                    )
                current = self.system.orchestration.commander.get_incident(ident)
                if self.runtime.cop.version != version or current.get("cycle") != cycle:
                    advice.concerns.append(
                        "Snapshot changed during analysis; this advice is historical."
                    )
                    advice.recommend_replan = False
                event = Event(
                    id=sha256(f"advice:{ident}:{cycle}".encode()).hexdigest(),
                    type=EventType.REASONING_COMPLETED,
                    source="intelligence_advisor",
                    incident_id=ident,
                    created_at=self.runtime.clock(),
                    payload=advice.model_dump(mode="json"),
                )
                with self.runtime.db.transaction():
                    self.save_evidence(context)
                    self.runtime.db.connection.execute(
                        "INSERT INTO intelligence_advice VALUES (?,?,?,?,0)",
                        (ident, cycle, advice.model_dump_json(), event.model_dump_json()),
                    )
                await self._publish(event)

    async def _publish(self, event):
        await self.runtime.bus.publish(event)
        self.runtime.db.connection.execute(
            "UPDATE intelligence_advice SET published=1 WHERE incident_id=? AND cycle=?",
            (event.incident_id, event.payload["cycle"]),
        )

    @property
    def request_timeout(self):
        return self.settings.profiles[self.settings.active].timeout_seconds

    def configure(self, update):
        provider = self.settings.build_provider(update)
        result = self.settings.save(update)
        self.provider = provider
        return result

    def start_worker(self):
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop_worker(self):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Intelligence worker failed; retrying durable work")
            await asyncio.sleep(0.5)
