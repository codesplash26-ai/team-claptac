"""Base class every ATLAS agent inherits from."""
from __future__ import annotations

from abc import ABC, abstractmethod

from atlas.core.events import Event, EventType


class Agent(ABC):
    name: str = "base"
    subscribes_to: tuple[EventType, ...] = ()

    def __init__(self, bus, cop, ledger):
        self.bus = bus
        self.cop = cop
        self.ledger = ledger

    @abstractmethod
    async def handle(self, event: Event) -> list[Event]:
        """Process one event and return the events to publish."""
