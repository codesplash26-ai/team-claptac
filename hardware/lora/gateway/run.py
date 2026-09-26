"""Gateway Pi: radio in, the same SensorReading ATLAS already accepts out.

    python run.py --config config.yaml
    python run.py --config config.yaml --mock   # no radio: injects a rising water level

Never transmit without a 433 MHz antenna on the Ra-02.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
LORA = Path(__file__).resolve().parents[1]
for path in (ROOT, LORA):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from atlas.ingest.lora import (  # noqa: E402
    FrameType,
    HmacFrameCodec,
    LoraFrame,
    LoraGateway,
    MockLoraLink,
    NodeConfig,
    ReadingItem,
    pack_reading_payload,
)
from atlas.ingest.sensors import SensorType  # noqa: E402

log = logging.getLogger("atlas-lora-gateway")


def load_key(cfg: dict) -> bytes:
    hex_key = os.environ.get("LORA_HMAC_KEY") or cfg.get("hmac_key_hex") or ""
    if not hex_key:
        raise SystemExit("Set LORA_HMAC_KEY in the environment or hmac_key_hex in the config")
    try:
        key = bytes.fromhex(hex_key)
    except ValueError as exc:
        raise SystemExit("LORA_HMAC_KEY must be hex") from exc
    if len(key) < 16:
        raise SystemExit("HMAC key must be at least 16 bytes")
    return key


def load_nodes(cfg: dict) -> tuple[dict[int, NodeConfig], dict[int, str]]:
    nodes = {
        int(node_id): NodeConfig(
            name=spec["name"],
            lat=spec["lat"],
            lon=spec["lon"],
            area_id=spec["area_id"],
        )
        for node_id, spec in (cfg.get("nodes") or {}).items()
    }
    areas = {int(code): name for code, name in (cfg.get("areas") or {}).items()}
    return nodes, areas


def build_forwarder(cfg: dict):
    dest = cfg.get("atlas") or {}
    kind = dest.get("kind", "http")
    if kind == "http":
        import requests

        url = dest["url"]

        def send(event) -> None:
            payload = {k: v for k, v in event.payload.items() if k != "kind"}
            response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()

        return send
    if kind == "mqtt":
        import paho.mqtt.client as mqtt

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        client.connect(dest.get("host", "localhost"), int(dest.get("port", 1883)))
        client.loop_start()
        prefix = dest.get("topic_prefix", "atlas/sensors")

        def send(event) -> None:
            payload = {k: v for k, v in event.payload.items() if k != "kind"}
            topic = f"{prefix}/{payload['node_id']}/{payload['type']}"
            client.publish(topic, json.dumps(payload), qos=1)

        return send
    raise SystemExit(f"Unknown atlas.kind: {kind}")


def build_radio(cfg: dict):
    from sx1278 import SX1278Link

    radio = dict(cfg.get("radio") or {})
    log.warning("Transmit only with a 433 MHz antenna on the Ra-02 ANT pad")
    return SX1278Link(**radio)


def inject_mock_reading(link: MockLoraLink, codec: HmacFrameCodec, key: bytes, seq: int, cm: float) -> None:
    payload = pack_reading_payload([ReadingItem(SensorType.WATER_LEVEL, 0, cm, 0)])
    frame = LoraFrame(1, seq % 65536, FrameType.READING, payload)
    link.inbox.append(codec.encode(frame, key))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--mock", action="store_true", help="no radio; inject flood_ramp readings")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    logging.basicConfig(level=cfg.get("log_level", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    key = load_key(cfg)
    nodes, areas = load_nodes(cfg)
    codec = HmacFrameCodec()
    link = MockLoraLink() if args.mock else build_radio(cfg)
    gateway = LoraGateway(link, codec, key, build_forwarder(cfg), nodes=nodes, areas=areas)

    log.info("LoRa gateway started mock=%s nodes=%s", args.mock, list(nodes))
    seq = 0
    try:
        while True:
            if args.mock:
                seq += 1
                t = min(1.0, seq / 12)
                cm = 5 + 25 * t
                inject_mock_reading(link, codec, key, seq, cm)
                log.info("mock water_level %.1f cm", cm)
            emitted = gateway.poll_once(timeout_s=1.0)
            if emitted:
                log.info("forwarded %s reading(s)", emitted)
            if args.mock:
                time.sleep(10)
    except KeyboardInterrupt:
        log.info("stopped at %s", datetime.now(timezone.utc).isoformat())
    finally:
        closer = getattr(link, "close", None)
        if closer:
            closer()


if __name__ == "__main__":
    main()
