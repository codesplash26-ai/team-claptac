"""LoRa link: frames from field nodes in, ATLAS events out.  (Owner: Hasindu)

    node (Pi/ESP32 + sensors + Ra-02) --LoRa 433 MHz--> gateway (Pi + Ra-02) --> this module --> bus

Everything downstream is unchanged: the gateway turns radio frames into the existing
``SensorReading`` (see ``atlas/ingest/sensors.py``) and hands it to the same ingest path as MQTT or
HTTP. The frame layout is a DRAFT in ``hardware/lora/protocol.md``; Hasindu confirms or changes it,
then this module and the node firmware follow the same spec.

What is here now is the contract: names, types and signatures the rest of the team can code against.
The bodies raise ``NotImplementedError`` until the hardware work lands. ``MockLoraLink`` lets everyone
else test the full path without a radio.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Callable, Protocol

from atlas.core.events import Event
from atlas.ingest.field import FieldReport, MissionAck
from atlas.ingest.sensors import SensorReading, SensorType

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 255  # SX127x FIFO limit


class FrameType(IntEnum):
    READING = 0x01  # one or more sensor readings
    FIELD_REPORT = 0x02  # a responder's need or observation
    MISSION_ACK = 0x03  # a team acknowledges or updates a mission
    HEARTBEAT = 0x04  # node alive, battery, signal
    ACK = 0x10  # gateway -> node: frame received


# Compact sensor ids on the air; must stay in step with SensorType.
SENSOR_TYPE_IDS: dict[SensorType, int] = {
    SensorType.WATER_LEVEL: 1,
    SensorType.RAINFALL: 2,
    SensorType.SOIL_MOISTURE: 3,
    SensorType.TEMPERATURE: 4,
    SensorType.HUMIDITY: 5,
    SensorType.PRESSURE: 6,
    SensorType.TILT: 7,
}


@dataclass(frozen=True)
class LoraFrame:
    """One decoded, authenticated frame. Nodes have no clock: they send ``age_seconds`` (how long ago
    the value was measured) and the gateway stamps the receive time."""

    node_id: int
    sequence: int  # per-node counter; lets the gateway drop duplicates and see gaps
    frame_type: FrameType
    payload: bytes
    rssi_dbm: float | None = None
    snr_db: float | None = None


class FrameCodec(Protocol):
    def encode(self, frame: LoraFrame, key: bytes) -> bytes:
        """Serialise and sign. Must not exceed ``MAX_FRAME_BYTES``."""

    def decode(self, raw: bytes, key: bytes) -> LoraFrame:
        """Verify the signature and parse. Raise ``ValueError`` for anything not authentic."""


class LoraLink(Protocol):
    """A radio (or a fake one). The gateway loop reads frames from it and can send ACKs back."""

    def receive(self, timeout_s: float) -> bytes | None: ...

    def send(self, raw: bytes) -> None: ...


class MockLoraLink:
    """In-memory stand-in for the radio, so the pipeline can be tested without hardware."""

    def __init__(self) -> None:
        self.inbox: list[bytes] = []
        self.sent: list[bytes] = []

    def receive(self, timeout_s: float) -> bytes | None:
        return self.inbox.pop(0) if self.inbox else None

    def send(self, raw: bytes) -> None:
        self.sent.append(raw)


class LoraGateway:
    """Reads frames, drops bad or duplicate ones, sends ACKs, and emits ATLAS events.

    ``on_event`` is the same callback shape the MQTT listener uses, so the runtime wiring is shared.
    """

    def __init__(
        self,
        link: LoraLink,
        codec: FrameCodec,
        key: bytes,
        on_event: Callable[[Event], None],
    ) -> None:
        self.link, self.codec, self.key, self.on_event = link, codec, key, on_event

    def poll_once(self, timeout_s: float = 1.0) -> int:
        """Handle at most one frame; return how many events were emitted."""
        raise NotImplementedError("Hasindu: LoRa gateway loop")

    def readings_from(self, frame: LoraFrame, received_at) -> list[SensorReading]:
        """Turn a READING frame into SensorReadings (timestamp = received_at - age)."""
        raise NotImplementedError("Hasindu: READING payload decoding")

    def field_report_from(self, frame: LoraFrame, received_at) -> FieldReport:
        raise NotImplementedError("Hasindu: FIELD_REPORT payload decoding")

    def mission_ack_from(self, frame: LoraFrame, received_at) -> MissionAck:
        raise NotImplementedError("Hasindu: MISSION_ACK payload decoding")
