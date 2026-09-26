"""LoRa link: frames from field nodes in, ATLAS events out.  (Owner: Hasindu)

    node (Pi/ESP32 + sensors + Ra-02) --LoRa 433 MHz--> gateway (Pi + Ra-02) --> this module --> bus

Everything downstream is unchanged: the gateway turns radio frames into the existing
``SensorReading`` (see ``atlas/ingest/sensors.py``) and hands it to the same ingest path as MQTT or
HTTP. Frame layout: ``hardware/lora/protocol.md``.

``MockLoraLink`` lets the pipeline be tested without a radio. A real Ra-02 still needs a 433 MHz
antenna; transmitting without one can damage the module.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import struct
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Callable, Protocol

from atlas.core.events import Event
from atlas.ingest.field import FieldReport, MissionAck
from atlas.ingest.sensors import SensorLocation, SensorReading, SensorType, to_event

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 255  # SX127x FIFO limit
MAGIC = 0xA7
HEADER_BYTES = 8
HMAC_BYTES = 8
MAX_PAYLOAD_BYTES = MAX_FRAME_BYTES - HEADER_BYTES - HMAC_BYTES  # 239
SEQ_WINDOW = 32768

SENSOR_UNITS: dict[SensorType, str] = {
    SensorType.WATER_LEVEL: "cm",
    SensorType.RAINFALL: "mm",
    SensorType.SOIL_MOISTURE: "%",
    SensorType.TEMPERATURE: "C",
    SensorType.HUMIDITY: "%",
    SensorType.PRESSURE: "hPa",
    SensorType.TILT: "deg",
}

SENSOR_ID_PREFIX: dict[SensorType, str] = {
    SensorType.WATER_LEVEL: "wl",
    SensorType.RAINFALL: "rain",
    SensorType.SOIL_MOISTURE: "soil",
    SensorType.TEMPERATURE: "temp",
    SensorType.HUMIDITY: "hum",
    SensorType.PRESSURE: "pres",
    SensorType.TILT: "tilt",
}

FIELD_KIND_IDS: dict[str, int] = {"need": 0, "hazard": 1, "road": 2, "all_clear": 3}
MISSION_CHANGE_IDS: dict[str, int] = {
    "acknowledged": 0,
    "progress": 1,
    "exception": 2,
    "completed": 3,
}


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

SENSOR_TYPES_BY_ID: dict[int, SensorType] = {i: t for t, i in SENSOR_TYPE_IDS.items()}
FIELD_KINDS_BY_ID: dict[int, str] = {i: k for k, i in FIELD_KIND_IDS.items()}
MISSION_CHANGES_BY_ID: dict[int, str] = {i: k for k, i in MISSION_CHANGE_IDS.items()}


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


@dataclass(frozen=True)
class NodeConfig:
    """Gateway-side identity for a numeric on-air node id. Location is not sent over the air."""

    name: str
    lat: float
    lon: float
    area_id: str


@dataclass(frozen=True)
class ReadingItem:
    sensor_type: SensorType
    index: int
    value: float
    age_seconds: int


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
        self.last_rssi_dbm: float | None = None
        self.last_snr_db: float | None = None

    def receive(self, timeout_s: float) -> bytes | None:
        return self.inbox.pop(0) if self.inbox else None

    def send(self, raw: bytes) -> None:
        self.sent.append(raw)


class HmacFrameCodec:
    """protocol.md: magic, version, HMAC-SHA256 truncated to 8 bytes."""

    def encode(self, frame: LoraFrame, key: bytes) -> bytes:
        if not key:
            raise ValueError("HMAC key is required")
        if frame.node_id < 0 or frame.node_id > 0xFFFF:
            raise ValueError("node_id must fit in 2 bytes")
        if frame.sequence < 0 or frame.sequence > 0xFFFF:
            raise ValueError("sequence must fit in 2 bytes")
        if len(frame.payload) > MAX_PAYLOAD_BYTES:
            raise ValueError("oversize")
        header = struct.pack(
            ">BBBHHB",
            MAGIC,
            PROTOCOL_VERSION,
            int(frame.frame_type),
            frame.node_id,
            frame.sequence,
            len(frame.payload),
        )
        body = header + frame.payload
        raw = body + hmac.new(key, body, hashlib.sha256).digest()[:HMAC_BYTES]
        if len(raw) > MAX_FRAME_BYTES:
            raise ValueError("oversize")
        return raw

    def decode(self, raw: bytes, key: bytes) -> LoraFrame:
        if not key:
            raise ValueError("HMAC key is required")
        if len(raw) < HEADER_BYTES + HMAC_BYTES:
            raise ValueError("frame too short")
        if len(raw) > MAX_FRAME_BYTES:
            raise ValueError("oversize")
        body, tag = raw[:-HMAC_BYTES], raw[-HMAC_BYTES:]
        magic, ver, ftype, node_id, sequence, length = struct.unpack(">BBBHHB", body[:HEADER_BYTES])
        if magic != MAGIC:
            raise ValueError("bad magic")
        if ver != PROTOCOL_VERSION:
            raise ValueError("unsupported version")
        payload = body[HEADER_BYTES:]
        if length != len(payload):
            raise ValueError("length mismatch")
        expected = hmac.new(key, body, hashlib.sha256).digest()[:HMAC_BYTES]
        if not hmac.compare_digest(tag, expected):
            raise ValueError("bad signature")
        try:
            frame_type = FrameType(ftype)
        except ValueError as exc:
            raise ValueError("unknown frame type") from exc
        return LoraFrame(node_id=node_id, sequence=sequence, frame_type=frame_type, payload=payload)


def pack_reading_payload(items: list[ReadingItem]) -> bytes:
    if not items or len(items) > 255:
        raise ValueError("READING payload needs 1–255 items")
    parts = [struct.pack("B", len(items))]
    for item in items:
        if item.index < 0 or item.index > 255:
            raise ValueError("sensor index must fit in 1 byte")
        if item.age_seconds < 0 or item.age_seconds > 0xFFFF:
            raise ValueError("age_seconds must fit in 2 bytes")
        parts.append(
            struct.pack(
                ">BBfH",
                SENSOR_TYPE_IDS[item.sensor_type],
                item.index,
                item.value,
                item.age_seconds,
            )
        )
    payload = b"".join(parts)
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("oversize")
    return payload


def unpack_reading_payload(payload: bytes) -> list[ReadingItem]:
    if not payload:
        raise ValueError("empty READING payload")
    count = payload[0]
    expected = 1 + count * 8
    if len(payload) != expected:
        raise ValueError("READING payload length does not match count")
    items: list[ReadingItem] = []
    offset = 1
    for _ in range(count):
        sensor_id, index, value, age = struct.unpack_from(">BBfH", payload, offset)
        offset += 8
        sensor_type = SENSOR_TYPES_BY_ID.get(sensor_id)
        if sensor_type is None:
            raise ValueError("unknown sensor type id")
        items.append(ReadingItem(sensor_type, index, value, age))
    return items


def pack_field_report_payload(area_code: int, kind: str, people: int, urgency: float, text: str) -> bytes:
    if kind not in FIELD_KIND_IDS:
        raise ValueError("unknown field-report kind")
    if area_code < 0 or area_code > 0xFFFF or people < 0 or people > 0xFFFF:
        raise ValueError("area_code and people must fit in 2 bytes")
    urgency_byte = max(0, min(100, int(round(urgency * 100))))
    encoded = text.encode("utf-8")
    payload = struct.pack(">HBHB", area_code, FIELD_KIND_IDS[kind], people, urgency_byte) + encoded
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("oversize")
    return payload


def pack_mission_ack_payload(mission_id: str, change: str, percent: int | None, note: str) -> bytes:
    if change not in MISSION_CHANGE_IDS:
        raise ValueError("unknown mission-ack change")
    prefix = mission_id.encode("ascii")[:6]
    if len(prefix) < 6:
        prefix = prefix.ljust(6, b"\x00")
    pct = 255 if percent is None else percent
    if pct != 255 and (pct < 0 or pct > 100):
        raise ValueError("percent must be 0–100")
    payload = prefix + struct.pack("BB", MISSION_CHANGE_IDS[change], pct) + note.encode("utf-8")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("oversize")
    return payload


def pack_heartbeat_payload(battery_mv: int, uptime_s: int, boot: bool) -> bytes:
    return struct.pack(">HIB", battery_mv, uptime_s, 1 if boot else 0)


def pack_ack_payload(node_id: int, sequence: int) -> bytes:
    return struct.pack(">HH", node_id, sequence)


def unpack_ack_payload(payload: bytes) -> tuple[int, int]:
    if len(payload) != 4:
        raise ValueError("ACK payload must be 4 bytes")
    return struct.unpack(">HH", payload)


def sensor_id_for(sensor_type: SensorType, index: int) -> str:
    return f"{SENSOR_ID_PREFIX[sensor_type]}-{index + 1:02d}"


def measurement_id_for(node_id: int, sequence: int, position: int) -> str:
    return f"{node_id}:{sequence}:{position}"


class LoraGateway:
    """Reads frames, drops bad or duplicate ones, sends ACKs, and emits ATLAS events.

    ``on_event`` is the same callback shape the MQTT listener uses, so the runtime wiring is shared.
    Field reports and mission acks have no EventType yet (open decision with Dinuka); they are
    decoded and handed to optional callbacks instead.
    """

    def __init__(
        self,
        link: LoraLink,
        codec: FrameCodec,
        key: bytes,
        on_event: Callable[[Event], None],
        *,
        nodes: dict[int, NodeConfig] | None = None,
        areas: dict[int, str] | None = None,
        clock: Callable[[], datetime] | None = None,
        on_field_report: Callable[[FieldReport], None] | None = None,
        on_mission_ack: Callable[[MissionAck], None] | None = None,
    ) -> None:
        self.link, self.codec, self.key, self.on_event = link, codec, key, on_event
        self.nodes = nodes or {}
        self.areas = areas or {}
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.on_field_report = on_field_report
        self.on_mission_ack = on_mission_ack
        self.last_seq: dict[int, int] = {}
        self.gaps: list[tuple[int, int, int]] = []
        self.bad_signatures = 0
        self.duplicates = 0

    def poll_once(self, timeout_s: float = 1.0) -> int:
        """Handle at most one frame; return how many events were emitted."""
        raw = self.link.receive(timeout_s)
        if raw is None:
            return 0
        try:
            frame = self.codec.decode(raw, self.key)
        except ValueError:
            self.bad_signatures += 1
            log.warning("dropping unauthenticated or malformed LoRa frame")
            return 0
        frame = self._with_radio_stats(frame)
        if frame.frame_type == FrameType.ACK:
            return 0
        self._ack(frame.node_id, frame.sequence)
        if self._is_replay(frame.node_id, frame.sequence):
            self.duplicates += 1
            return 0
        self._accept_seq(frame.node_id, frame.sequence)
        received_at = self.clock()
        try:
            return self._dispatch(frame, received_at)
        except ValueError:
            log.warning("authenticated frame %s from node %s had a bad payload", frame.frame_type, frame.node_id)
            return 0

    def readings_from(self, frame: LoraFrame, received_at) -> list[SensorReading]:
        """Turn a READING frame into SensorReadings (timestamp = received_at - age)."""
        if frame.frame_type != FrameType.READING:
            raise ValueError("not a READING frame")
        node = self.nodes.get(frame.node_id)
        node_name = node.name if node else str(frame.node_id)
        location = (
            SensorLocation(lat=node.lat, lon=node.lon, area_id=node.area_id) if node else None
        )
        readings = []
        for position, item in enumerate(unpack_reading_payload(frame.payload)):
            readings.append(
                SensorReading(
                    node_id=node_name,
                    sensor_id=sensor_id_for(item.sensor_type, item.index),
                    measurement_id=measurement_id_for(frame.node_id, frame.sequence, position),
                    type=item.sensor_type,
                    value=item.value,
                    unit=SENSOR_UNITS[item.sensor_type],
                    timestamp=received_at - timedelta(seconds=item.age_seconds),
                    location=location,
                )
            )
        return readings

    def field_report_from(self, frame: LoraFrame, received_at) -> FieldReport:
        if frame.frame_type != FrameType.FIELD_REPORT:
            raise ValueError("not a FIELD_REPORT frame")
        if len(frame.payload) < 6:
            raise ValueError("FIELD_REPORT payload too short")
        area_code, kind_id, people, urgency_byte = struct.unpack_from(">HBHB", frame.payload)
        kind = FIELD_KINDS_BY_ID.get(kind_id)
        if kind is None:
            raise ValueError("unknown field-report kind")
        text = frame.payload[6:].decode("utf-8", errors="replace")[:280]
        node = self.nodes.get(frame.node_id)
        area_id = self.areas.get(area_code) or (node.area_id if node else f"area-{area_code}")
        return FieldReport(
            node_id=node.name if node else str(frame.node_id),
            timestamp=received_at,
            via="lora",
            rssi_dbm=frame.rssi_dbm,
            report_id=f"lora:{frame.node_id}:{frame.sequence}",
            area_id=area_id,
            kind=kind,  # type: ignore[arg-type]
            people=people,
            urgency=urgency_byte / 100.0,
            text=text,
        )

    def mission_ack_from(self, frame: LoraFrame, received_at) -> MissionAck:
        if frame.frame_type != FrameType.MISSION_ACK:
            raise ValueError("not a MISSION_ACK frame")
        if len(frame.payload) < 8:
            raise ValueError("MISSION_ACK payload too short")
        prefix = frame.payload[:6].rstrip(b"\x00")
        change_id, percent = frame.payload[6], frame.payload[7]
        change = MISSION_CHANGES_BY_ID.get(change_id)
        if change is None:
            raise ValueError("unknown mission-ack change")
        note = frame.payload[8:].decode("utf-8", errors="replace")[:160]
        node = self.nodes.get(frame.node_id)
        try:
            mission = prefix.decode("ascii")
        except UnicodeDecodeError:
            mission = prefix.hex()
        return MissionAck(
            node_id=node.name if node else str(frame.node_id),
            timestamp=received_at,
            via="lora",
            rssi_dbm=frame.rssi_dbm,
            mission_id=mission,
            change=change,  # type: ignore[arg-type]
            percent=None if percent == 255 else percent,
            note=note,
        )

    def _dispatch(self, frame: LoraFrame, received_at: datetime) -> int:
        if frame.frame_type == FrameType.READING:
            emitted = 0
            for reading in self.readings_from(frame, received_at):
                self.on_event(to_event(reading))
                emitted += 1
            return emitted
        if frame.frame_type == FrameType.FIELD_REPORT:
            report = self.field_report_from(frame, received_at)
            if self.on_field_report:
                self.on_field_report(report)
            return 0
        if frame.frame_type == FrameType.MISSION_ACK:
            ack = self.mission_ack_from(frame, received_at)
            if self.on_mission_ack:
                self.on_mission_ack(ack)
            return 0
        if frame.frame_type == FrameType.HEARTBEAT:
            log.info("heartbeat node=%s rssi=%s snr=%s", frame.node_id, frame.rssi_dbm, frame.snr_db)
            return 0
        return 0

    def _ack(self, node_id: int, sequence: int) -> None:
        frame = LoraFrame(node_id, sequence, FrameType.ACK, pack_ack_payload(node_id, sequence))
        self.link.send(self.codec.encode(frame, self.key))

    def _is_replay(self, node_id: int, sequence: int) -> bool:
        last = self.last_seq.get(node_id)
        if last is None:
            return False
        ahead = (sequence - last) % 65536
        return ahead == 0 or ahead > SEQ_WINDOW

    def _accept_seq(self, node_id: int, sequence: int) -> None:
        last = self.last_seq.get(node_id)
        if last is not None:
            ahead = (sequence - last) % 65536
            if ahead > 1:
                self.gaps.append((node_id, last, sequence))
                log.info("LoRa gap node=%s last=%s got=%s", node_id, last, sequence)
        self.last_seq[node_id] = sequence

    def _with_radio_stats(self, frame: LoraFrame) -> LoraFrame:
        rssi = getattr(self.link, "last_rssi_dbm", None)
        snr = getattr(self.link, "last_snr_db", None)
        if rssi is None and snr is None:
            return frame
        return replace(frame, rssi_dbm=rssi, snr_db=snr)
