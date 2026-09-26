"""The LoRa gateway's link health reaches the console: per-node signal, messages, gaps, drops."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.core.config import Settings
from atlas.ingest.lora import (
    FrameType,
    HmacFrameCodec,
    LinkStatus,
    LoraFrame,
    LoraGateway,
    MockLoraLink,
    NodeConfig,
    ReadingItem,
    pack_reading_payload,
)
from atlas.ingest.sensors import SensorType

KEY = b"k" * 32


def frame(seq):
    payload = pack_reading_payload([ReadingItem(SensorType.WATER_LEVEL, 0, 12.0, 0)])
    return HmacFrameCodec().encode(LoraFrame(1, seq, FrameType.READING, payload), KEY)


def test_the_gateway_reports_signal_messages_gaps_and_drops():
    link = MockLoraLink()
    node = NodeConfig(name="rtp-node-01", lat=6.68, lon=80.4, area_id="ratnapura-demo-river")
    gateway = LoraGateway(
        link, HmacFrameCodec(), KEY, lambda event: None, nodes={1: node},
        clock=lambda: datetime(2026, 9, 26, tzinfo=timezone.utc),
    )  # fmt: skip
    link.last_rssi_dbm, link.last_snr_db = -91.0, 6.5
    for raw in (frame(1), frame(1), frame(4), b"\x00" * 40):  # a repeat, a gap of two, garbage
        link.inbox.append(raw)
        gateway.poll_once()
    status = LinkStatus.model_validate(gateway.status("gw-test"))
    (n,) = status.nodes
    assert (n.name, n.frames, n.gaps, n.rssi_dbm) == ("rtp-node-01", 2, 1, -91.0)
    assert status.duplicates == 1 and status.bad_signatures == 1


def test_the_api_keeps_each_gateways_last_report():
    with TestClient(create_app(Settings(database_path=":memory:"))) as client:
        assert client.get("/links").json() == []
        body = {
            "gateway": "gw-1",
            "reported_at": "2026-09-26T00:00:00+00:00",
            "nodes": [{"node": 1, "name": "n1", "frames": 3}],
        }
        assert client.post("/ingest/link-status", json=body).status_code == 202
        (link,) = client.get("/links").json()
        assert (
            link["gateway"] == "gw-1"
            and link["nodes"][0]["frames"] == 3
            and link["age_seconds"] >= 0
        )
        assert client.post("/ingest/link-status", json={"gateway": ""}).status_code == 422
