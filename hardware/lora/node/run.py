"""Field node: read sensors, send a signed LoRa READING, wait for ACK, buffer offline.

    python run.py --config config.yaml
    python run.py --config config.yaml --mock-radio   # encode only; prints frames (no SPI)

Reuse hardware/pi sensor drivers. Never transmit without a 433 MHz antenna.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
PI = ROOT / "hardware" / "pi"
for path in (ROOT, PI, Path(__file__).resolve().parents[1]):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from sensors import build_sensor  # noqa: E402

from atlas.ingest.lora import (  # noqa: E402
    FrameType,
    HmacFrameCodec,
    LoraFrame,
    ReadingItem,
    pack_reading_payload,
    unpack_ack_payload,
)
from atlas.ingest.sensors import SensorType  # noqa: E402

log = logging.getLogger("atlas-lora-node")

SENSOR_TYPES = {item.value: item for item in SensorType}


def load_key(cfg: dict) -> bytes:
    hex_key = os.environ.get("LORA_HMAC_KEY") or cfg.get("hmac_key_hex") or ""
    if not hex_key:
        raise SystemExit("Set LORA_HMAC_KEY in the environment or hmac_key_hex in the config")
    key = bytes.fromhex(hex_key)
    if len(key) < 16:
        raise SystemExit("HMAC key must be at least 16 bytes")
    return key


def load_seq(path: Path) -> int:
    if not path.exists():
        return 1
    return int(json.loads(path.read_text())["seq"])


def save_seq(path: Path, seq: int) -> None:
    path.write_text(json.dumps({"seq": seq}))


def build_radio(cfg: dict):
    from sx1278 import SX1278Link

    radio = dict(cfg.get("radio") or {})
    log.warning("Transmit only with a 433 MHz antenna on the Ra-02 ANT pad")
    return SX1278Link(**radio)


def wait_for_ack(link, codec, key, node_id: int, seq: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        raw = link.receive(max(0.05, deadline - time.monotonic()))
        if raw is None:
            continue
        try:
            frame = codec.decode(raw, key)
        except ValueError:
            continue
        if frame.frame_type is not FrameType.ACK:
            continue
        ack_node, ack_seq = unpack_ack_payload(frame.payload)
        if ack_node == node_id and ack_seq == seq:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--mock-radio", action="store_true", help="do not open SPI; log encoded frames")
    args = parser.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    logging.basicConfig(level=cfg.get("log_level", "INFO"), format="%(asctime)s %(levelname)s %(message)s")

    node_id = int(cfg["on_air_node_id"])
    interval = float(cfg.get("interval_seconds", 20))
    retries = int(cfg.get("retries", 4))
    key = load_key(cfg)
    codec = HmacFrameCodec()
    sensors = [build_sensor(spec) for spec in cfg["sensors"]]
    seq_path = Path(cfg.get("seq_path", "lora-seq.json"))
    seq = load_seq(seq_path)
    link = None if args.mock_radio else build_radio(cfg)

    log.info("LoRa node %s started sensors=%s", node_id, [s.sensor_id for s in sensors])
    try:
        while True:
            items: list[ReadingItem] = []
            indexes: dict[str, int] = {}
            for sensor in sensors:
                try:
                    for measurement in sensor.read():
                        index = indexes.get(measurement.type, 0)
                        indexes[measurement.type] = index + 1
                        items.append(
                            ReadingItem(
                                SENSOR_TYPES[measurement.type],
                                index,
                                float(measurement.value),
                                0,
                            )
                        )
                except Exception:
                    log.exception("failed to read %s", sensor.sensor_id)
            if items:
                frame = LoraFrame(node_id, seq, FrameType.READING, pack_reading_payload(items))
                raw = codec.encode(frame, key)
                sent = False
                if args.mock_radio:
                    log.info("encoded %s bytes seq=%s items=%s", len(raw), seq, len(items))
                    sent = True
                else:
                    delay = 0.5
                    for attempt in range(retries):
                        link.send(raw)
                        if wait_for_ack(link, codec, key, node_id, seq, timeout_s=2.0):
                            sent = True
                            break
                        log.warning("no ACK seq=%s attempt=%s", seq, attempt + 1)
                        time.sleep(delay)
                        delay = min(delay * 2, 8)
                if sent:
                    seq = (seq % 65535) + 1
                    save_seq(seq_path, seq)
                else:
                    log.warning("keeping seq=%s for retry on the next loop", seq)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("stopped")
    finally:
        if link is not None:
            link.close()


if __name__ == "__main__":
    main()
