"""Typed publish/subscribe event bus. Agents never message each other directly.

In-process async bus for Week 1+. Can later be swapped for Redis or NATS without
changing agent code (agents only see publish/subscribe).
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from atlas.core.events import Event, EventType

log = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None] | None]


class EventBus:
    """In-process async event bus with typed subscriptions."""

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Handler]] = defaultdict(list)
        self._history: list[Event] = []
        self._lock = asyncio.Lock()

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def subscribe_many(self, event_types: tuple[EventType, ...], handler: Handler) -> None:
        for event_type in event_types:
            self.subscribe(event_type, handler)

    async def publish(self, event: Event) -> list[Any]:
        async with self._lock:
            self._history.append(event)
        log.debug("publish %s from %s", event.type, event.source)
        results: list[Any] = []
        for handler in list(self._handlers.get(event.type, [])):
            result = handler(event)
            if asyncio.iscoroutine(result):
                result = await result
            results.append(result)
        return results

    async def publish_many(self, events: list[Event]) -> list[Any]:
        out: list[Any] = []
        for event in events:
            out.extend(await self.publish(event))
        return out

    @property
    def history(self) -> list[Event]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history.clear()
