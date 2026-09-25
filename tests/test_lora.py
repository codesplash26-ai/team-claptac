from datetime import datetime, timezone

import pytest

from atlas.core.events import EventType
from atlas.ingest.lora import (
    MAX_PAYLOAD_BYTES,
    FrameType,
    HmacFrameCodec,
    LoraFrame,
    LoraGateway,
    MockLoraLink,
    NodeConfig,
    ReadingItem,
    pack_ack_payload,
    pack_field_report_payload,
    pack_mission_ack_payload,
    pack_reading_payload,
    unpack_ack_payload,
)
from atlas.ingest.sensors import SensorType

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
KEY = b"test-hmac-key-for-atlas-lora-32b"
NODE = NodeConfig("rtp-node-01", 6.6828, 80.3992, "ratnapura-demo-river")
CODEC = HmacFrameCodec()


def _reading_frame(seq=1, value=27.5, age=5, node_id=1) -> LoraFrame:
    payload = pack_reading_payload([ReadingItem(SensorType.WATER_LEVEL, 0, value, age)])
    return LoraFrame(node_id, seq, FrameType.READING, payload)


def _gateway(link=None, events=None, **kwargs):
    events = events if events is not None else []
    return LoraGateway(
        link or MockLoraLink(),
        CODEC,
        KEY,
        events.append,
        nodes={1: NODE},
        areas={1: "ratnapura-demo-river"},
        clock=lambda: NOW,
        **kwargs,
    )


def test_codec_round_trip():
    frame = _reading_frame()
    raw = CODEC.encode(frame, KEY)
    decoded = CODEC.decode(raw, KEY)
    assert decoded.node_id == 1 and decoded.sequence == 1
    assert decoded.frame_type is FrameType.READING
    assert decoded.payload == frame.payload


def test_bad_signature_is_dropped_without_ack():
    link = MockLoraLink()
    events = []
    gateway = _gateway(link, events)
    raw = bytearray(CODEC.encode(_reading_frame(), KEY))
    raw[-1] ^= 0x01
    link.inbox.append(bytes(raw))
    assert gateway.poll_once() == 0
    assert events == [] and link.sent == []
    assert gateway.bad_signatures == 1


def test_duplicate_is_acked_but_not_emitted():
    link = MockLoraLink()
    events = []
    gateway = _gateway(link, events)
    raw = CODEC.encode(_reading_frame(), KEY)
    link.inbox.append(raw)
    assert gateway.poll_once() == 1
    link.inbox.append(raw)
    assert gateway.poll_once() == 0
    assert len(events) == 1
    assert gateway.duplicates == 1
    assert len(link.sent) == 2


def test_oversize_payload_is_refused():
    frame = LoraFrame(1, 1, FrameType.READING, b"x" * (MAX_PAYLOAD_BYTES + 1))
    with pytest.raises(ValueError, match="oversize"):
        CODEC.encode(frame, KEY)


def test_reading_timestamp_is_receive_time_minus_age():
    gateway = _gateway()
    readings = gateway.readings_from(_reading_frame(age=5), NOW)
    assert len(readings) == 1
    reading = readings[0]
    assert reading.node_id == "rtp-node-01"
    assert reading.sensor_id == "wl-01"
    assert reading.value == pytest.approx(27.5)
    assert reading.unit == "cm"
    assert reading.timestamp == datetime(2026, 9, 26, 11, 59, 55, tzinfo=timezone.utc)
    assert reading.location.area_id == "ratnapura-demo-river"
    assert reading.measurement_id == "1:1:0"


def test_poll_once_emits_observation_and_acks():
    link = MockLoraLink()
    events = []
    gateway = _gateway(link, events)
    link.inbox.append(CODEC.encode(_reading_frame(), KEY))
    assert gateway.poll_once() == 1
    assert events[0].type is EventType.OBSERVATION
    assert events[0].payload["value"] == pytest.approx(27.5)
    ack = CODEC.decode(link.sent[0], KEY)
    assert ack.frame_type is FrameType.ACK
    assert unpack_ack_payload(ack.payload) == (1, 1)


def test_field_report_and_mission_ack_decode():
    gateway = _gateway()
    report_frame = LoraFrame(
        1, 2, FrameType.FIELD_REPORT, pack_field_report_payload(1, "need", 12, 0.8, "help")
    )
    report = gateway.field_report_from(report_frame, NOW)
    assert report.area_id == "ratnapura-demo-river"
    assert report.kind == "need" and report.people == 12
    assert report.urgency == pytest.approx(0.8) and report.via == "lora"

    ack_frame = LoraFrame(
        1, 3, FrameType.MISSION_ACK, pack_mission_ack_payload("m-abc123def456", "progress", 40, "ok")
    )
    ack = gateway.mission_ack_from(ack_frame, NOW)
    assert ack.mission_id == "m-abc1"
    assert ack.change == "progress" and ack.percent == 40


def test_ack_payload_round_trip():
    assert unpack_ack_payload(pack_ack_payload(7, 99)) == (7, 99)
