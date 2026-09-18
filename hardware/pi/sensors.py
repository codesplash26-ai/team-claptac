"""Sensor drivers for the ATLAS Raspberry Pi node.

Hardware libraries are imported lazily so the node runs in `mock` mode on any
laptop without a Pi attached.
"""
from __future__ import annotations

import math
import random
import statistics
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Measurement:
    type: str      # water_level | rainfall | soil_moisture | temperature | humidity | pressure | tilt
    value: float
    unit: str


class Sensor(ABC):
    def __init__(self, sensor_id: str):
        self.sensor_id = sensor_id

    @abstractmethod
    def read(self) -> list[Measurement]:
        ...


class UltrasonicWaterLevel(Sensor):
    """JSN-SR04T / HC-SR04 mounted above the water, pointing down.

    level = mount_height - distance_to_surface
    NOTE: echo pin outputs 5V; use a voltage divider down to 3.3V.
    """

    def __init__(self, sensor_id, trigger_pin, echo_pin, mount_height_cm, samples=5):
        super().__init__(sensor_id)
        from gpiozero import DistanceSensor
        self._dev = DistanceSensor(echo=echo_pin, trigger=trigger_pin, max_distance=4)
        self.mount_height_cm = mount_height_cm
        self.samples = samples

    def read(self):
        readings = []
        for _ in range(self.samples):
            readings.append(self._dev.distance * 100)
            time.sleep(0.06)
        level = max(0.0, self.mount_height_cm - statistics.median(readings))
        return [Measurement("water_level", round(level, 1), "cm")]


class RainGauge(Sensor):
    """Tipping-bucket rain gauge on a GPIO pin; reports mm since last read."""

    def __init__(self, sensor_id, pin, mm_per_tip=0.2794):
        super().__init__(sensor_id)
        from gpiozero import Button
        self._tips = 0
        self._dev = Button(pin, pull_up=True, bounce_time=0.05)
        self._dev.when_pressed = self._on_tip
        self.mm_per_tip = mm_per_tip

    def _on_tip(self):
        self._tips += 1

    def read(self):
        tips, self._tips = self._tips, 0
        return [Measurement("rainfall", round(tips * self.mm_per_tip, 2), "mm")]


class BME280(Sensor):
    """Temperature / humidity / pressure over I2C."""

    def __init__(self, sensor_id, address=0x76, bus=1):
        super().__init__(sensor_id)
        import bme280
        import smbus2
        self._bme280 = bme280
        self._bus = smbus2.SMBus(bus)
        self._address = address
        self._params = bme280.load_calibration_params(self._bus, address)

    def read(self):
        d = self._bme280.sample(self._bus, self._address, self._params)
        return [
            Measurement("temperature", round(d.temperature, 2), "C"),
            Measurement("humidity", round(d.humidity, 2), "%"),
            Measurement("pressure", round(d.pressure, 2), "hPa"),
        ]


class SoilMoisture(Sensor):
    """Capacitive soil moisture sensor through an ADS1115 ADC (the Pi has no analog inputs).

    Calibrate dry_v (sensor in air) and wet_v (sensor in water) on your hardware.
    """

    def __init__(self, sensor_id, channel=0, dry_v=2.8, wet_v=1.2):
        super().__init__(sensor_id)
        import board
        import busio
        import adafruit_ads1x15.ads1115 as ADS
        from adafruit_ads1x15.analog_in import AnalogIn
        ads = ADS.ADS1115(busio.I2C(board.SCL, board.SDA))
        self._chan = AnalogIn(ads, getattr(ADS, f"P{channel}"))
        self.dry_v, self.wet_v = dry_v, wet_v

    def read(self):
        pct = (self.dry_v - self._chan.voltage) / (self.dry_v - self.wet_v) * 100
        return [Measurement("soil_moisture", round(min(100.0, max(0.0, pct)), 1), "%")]


class MockSensor(Sensor):
    """Synthetic data for development and as a demo-day fallback.

    scenario: "steady" (noise around baseline) or "flood_ramp" (rises to peak over ramp_seconds).
    """

    def __init__(self, sensor_id, type, unit, baseline, peak=None, ramp_seconds=300,
                 scenario="steady", noise=0.5):
        super().__init__(sensor_id)
        self.type, self.unit = type, unit
        self.baseline, self.peak = baseline, peak if peak is not None else baseline
        self.ramp_seconds, self.scenario, self.noise = ramp_seconds, scenario, noise
        self._start = time.monotonic()

    def read(self):
        value = self.baseline
        if self.scenario == "flood_ramp":
            t = min(1.0, (time.monotonic() - self._start) / self.ramp_seconds)
            value += (self.peak - self.baseline) * (1 - math.cos(math.pi * t)) / 2
        value += random.gauss(0, self.noise)
        return [Measurement(self.type, round(value, 2), self.unit)]


DRIVERS = {
    "ultrasonic_water_level": UltrasonicWaterLevel,
    "rain_gauge": RainGauge,
    "bme280": BME280,
    "soil_moisture": SoilMoisture,
    "mock": MockSensor,
}


def build_sensor(spec: dict) -> Sensor:
    spec = dict(spec)
    driver = spec.pop("driver")
    return DRIVERS[driver](**spec)
