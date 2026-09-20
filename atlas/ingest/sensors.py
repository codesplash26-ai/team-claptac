"""Sensor ingest: receives readings from Raspberry Pi nodes (MQTT or HTTP).

Readings become OBSERVATION events for A1 Sentinel. The payload shape must
match hardware/pi/sensor_node.py.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from enum import Enum
from hashlib import sha256
from typing import Callable

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

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
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    area_id: str | None = None


class SensorReading(BaseModel):
    node_id: str = Field(min_length=1)
    sensor_id: str = Field(min_length=1)
    measurement_id: str | None = None
    type: SensorType
    value: float = Field(allow_inf_nan=False)
    unit: str
    timestamp: datetime
    location: SensorLocation | None = None

    @field_validator("timestamp")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None:
            raise ValueError("Sensor timestamp must include timezone")
        return value

    @model_validator(mode="after")
    def check_units(self):
        units = {
            "water_level": {"cm", "m"},
            "rainfall": {"mm"},
            "soil_moisture": {"%"},
            "temperature": {"C"},
            "humidity": {"%"},
            "pressure": {"hPa"},
            "tilt": {"deg"},
        }
        if self.unit not in units[self.type.value]:
            raise ValueError("Unit does not match sensor type")
        if self.type.value != "temperature" and self.value < 0:
            raise ValueError("Measurement must be nonnegative")
        if self.type.value in {"humidity", "soil_moisture"} and self.value > 100:
            raise ValueError("Percentage must be <= 100")
        return self


def to_event(reading: SensorReading) -> Event:
    payload = {"kind": "sensor", **reading.model_dump(mode="json")}
    identity = (
        json.dumps(payload, sort_keys=True)
        if not reading.measurement_id
        else f"{reading.node_id}:{reading.sensor_id}:{reading.measurement_id}"
    )
    return Event(
        id=sha256(identity.encode()).hexdigest(),
        type=EventType.OBSERVATION,
        source=f"sensor:{reading.node_id}/{reading.sensor_id}",
        created_at=reading.timestamp,
        payload=payload,
    )


def run_mqtt_listener(
    on_event: Callable[[Event], None],
    host: str = "localhost",
    port: int = 1883,
    topic: str = "atlas/sensors/#",
    stop_event: threading.Event | None = None,
) -> None:
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
            client.ack(msg.mid, msg.qos)
            return
        try:
            on_event(to_event(reading))
            client.ack(msg.mid, msg.qos)
        except Exception:
            log.exception("sensor event processing failed; delivery remains pending if recorded")

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id="atlas-ingest",
        clean_session=False,
        manual_ack=True,
    )
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect_async(host, port)
    client.loop_start()
    try:
        (stop_event or threading.Event()).wait()
    finally:
        client.disconnect()
        client.loop_stop()
