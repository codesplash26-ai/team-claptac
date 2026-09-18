"""ATLAS Raspberry Pi sensor node.

Reads the configured sensors on a fixed interval and sends each measurement to
ATLAS over MQTT (default) or HTTP. Readings are buffered in memory while the
network is down and flushed when it comes back.

The node only measures and forwards. Anomaly detection happens in A1 Sentinel.

Usage:
    python sensor_node.py --config config.yaml
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone

import yaml

from sensors import build_sensor

log = logging.getLogger("atlas-node")


class MqttTransport:
    def __init__(self, host, port=1883, topic_prefix="atlas/sensors", username=None, password=None):
        import paho.mqtt.client as mqtt
        self.topic_prefix = topic_prefix
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if username:
            self._client.username_pw_set(username, password)
        self._client.connect_async(host, port)
        self._client.loop_start()

    def send(self, payload: dict) -> bool:
        if not self._client.is_connected():
            return False
        topic = f"{self.topic_prefix}/{payload['node_id']}/{payload['type']}"
        info = self._client.publish(topic, json.dumps(payload), qos=1)
        return info.rc == 0


class HttpTransport:
    def __init__(self, url, timeout=5):
        import requests
        self._requests = requests
        self.url, self.timeout = url, timeout

    def send(self, payload: dict) -> bool:
        try:
            r = self._requests.post(self.url, json=payload, timeout=self.timeout)
            return r.ok
        except self._requests.RequestException:
            return False


def build_transport(cfg: dict):
    cfg = dict(cfg)
    kind = cfg.pop("kind", "mqtt")
    return MqttTransport(**cfg) if kind == "mqtt" else HttpTransport(**cfg)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    logging.basicConfig(level=cfg.get("log_level", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")

    node_id = cfg["node_id"]
    location = cfg.get("location")
    interval = cfg.get("interval_seconds", 10)
    sensors = [build_sensor(s) for s in cfg["sensors"]]
    transport = build_transport(cfg["transport"])
    buffer: deque[dict] = deque(maxlen=cfg.get("buffer_size", 5000))

    log.info("node %s started with %d sensors", node_id, len(sensors))
    while True:
        now = datetime.now(timezone.utc).isoformat()
        for sensor in sensors:
            try:
                for m in sensor.read():
                    buffer.append({
                        "node_id": node_id,
                        "sensor_id": sensor.sensor_id,
                        "type": m.type,
                        "value": m.value,
                        "unit": m.unit,
                        "timestamp": now,
                        "location": location,
                    })
            except Exception:
                log.exception("failed to read %s", sensor.sensor_id)

        while buffer and transport.send(buffer[0]):
            log.debug("sent %s", buffer.popleft())
        if buffer:
            log.warning("offline, %d readings buffered", len(buffer))

        time.sleep(interval)


if __name__ == "__main__":
    main()
