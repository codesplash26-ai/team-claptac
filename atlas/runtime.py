"""Shared local runtime; register additional agents before start/recovery."""

import asyncio
import threading
from datetime import datetime, timezone

from atlas.agents.a1_sentinel import Sentinel
from atlas.core.config import Settings
from atlas.core.cop import COP
from atlas.core.event_bus import EventBus
from atlas.core.storage import Database
from atlas.memory.knowledge_graph import KnowledgeGraph
from atlas.memory.ledger import Ledger
from atlas.reasoning import Planner


class Runtime:
    def __init__(self, settings=None, *, clock=None):
        self.settings = settings or Settings.load()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.db = Database(self.settings.database_path)
        self.cop = COP(self.db)
        self.ledger = Ledger(self.db)
        self.bus = EventBus(self.db, self.ledger)
        self.graph = KnowledgeGraph()
        self.sentinel = Sentinel(
            self.bus,
            self.cop,
            self.ledger,
            config=self.settings.sentinel,
            planner=Planner(self.settings.reasoning),
            clock=self.clock,
        )
        self.register(self.sentinel)
        self._mqtt_stop = threading.Event()
        self._mqtt_thread = None

    def register(self, agent):
        self.bus.subscribe(agent.name, agent.subscribes_to, agent.handle)

    async def start(self):
        if not self.ledger.verify():
            raise ValueError("Ledger integrity check failed")
        await self.bus.recover()
        if self.settings.mqtt_enabled:
            from atlas.ingest.sensors import run_mqtt_listener

            loop = asyncio.get_running_loop()

            def on_event(event):
                # MQTT's thread submits into the single serialized async bus.
                future = asyncio.run_coroutine_threadsafe(self.bus.publish(event), loop)
                future.result(timeout=30)

            self._mqtt_thread = threading.Thread(
                target=run_mqtt_listener,
                kwargs={
                    "on_event": on_event,
                    "host": self.settings.mqtt_host,
                    "port": self.settings.mqtt_port,
                    "topic": self.settings.mqtt_topic,
                    "stop_event": self._mqtt_stop,
                },
                daemon=True,
            )
            self._mqtt_thread.start()

    async def close(self):
        self._mqtt_stop.set()
        if self._mqtt_thread:
            await asyncio.to_thread(self._mqtt_thread.join, 35)
        self.db.close()

    def snapshot(self):
        return self.cop.snapshot(now=self.clock())
