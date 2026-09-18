"""Sensor ingest: receives readings from Raspberry Pi nodes (MQTT or HTTP).

Readings become OBSERVATION events for A1 Sentinel. The payload shape must
match hardware/pi/sensor_node.py.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from enum import Enum
from typing import Callable

from pydantic import BaseModel, ValidationError

from atlas.core.events import Event, EventType

log = logging.getLogger(__name__)


class SensorType(str, Enum):
    WATER_LEVEL = "water_level"
    RAINFALL = "rainfall"
    SOIL_MOISTURE = "soil_moisture"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    PRESSURE = "pressure"
    TILT = "tilt"


class SensorLocation(BaseModel):
    lat: float
    lon: float
    area_id: str | None = None


class SensorReading(BaseModel):
    node_id: str
    sensor_id: str
    type: SensorType
    value: float
    unit: str
    timestamp: datetime
    location: SensorLocation | None = None


def to_event(reading: SensorReading) -> Event:
    return Event(
        type=EventType.OBSERVATION,
        source=f"sensor:{reading.node_id}/{reading.sensor_id}",
        payload={"kind": "sensor", **reading.model_dump(mode="json")},
    )


def run_mqtt_listener(on_event: Callable[[Event], None], host: str = "localhost",
                      port: int = 1883, topic: str = "atlas/sensors/#") -> None:
    """Blocking MQTT subscriber. Call from a background thread or its own process."""
    import paho.mqtt.client as mqtt

    def on_connect(client, userdata, flags, reason_code, properties):
        log.info("connected to MQTT %s:%s (%s), subscribing to %s", host, port, reason_code, topic)
        client.subscribe(topic, qos=1)

    def on_message(client, userdata, msg):
        try:
            reading = SensorReading.model_validate(json.loads(msg.payload))
        except (json.JSONDecodeError, ValidationError):
            log.warning("dropping malformed reading on %s", msg.topic)
            return
        on_event(to_event(reading))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(host, port)
    client.loop_forever()
