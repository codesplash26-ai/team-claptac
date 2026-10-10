# ATLAS Hardware: Raspberry Pi Sensor Node

A Raspberry Pi reads physical sensors and sends the readings to ATLAS. There, they arrive as **machine feeds** for A1 Sentinel. The Pi only measures and forwards; deciding whether a reading is an anomaly is A1's job.

```
Sensors ─► Raspberry Pi (sensor_node.py) ─► MQTT broker ─► atlas/ingest/sensors.py ─► A1 Sentinel ─► COP
                                         └─ or HTTP POST /ingest/sensor ─┘
```

## Parts list (1 node)

| Part | Purpose | Notes |
|---|---|---|
| Raspberry Pi 4 or 5 + power supply + microSD (32 GB) | Node | Raspberry Pi OS Lite, 64-bit |
| JSN-SR04T waterproof ultrasonic sensor | Water / river level | Needs a voltage divider on the echo pin |
| Tipping-bucket rain gauge | Rainfall | Reed switch, one GPIO pin |
| BME280 breakout | Temperature, humidity, pressure | I2C |
| Capacitive soil moisture sensor v1.2 *(optional)* | Landslide risk | Analog, so it needs the ADC below |
| ADS1115 ADC *(optional)* | Analog input for the soil sensor | I2C |
| MPU6050 *(optional, future)* | Slope tilt / movement | Driver not written yet |
| 1 kΩ + 2 kΩ resistors, breadboard, jumper wires | Wiring | |

**Order parts in Week 0.** Local stock and shipping are the biggest schedule risk.

## Wiring (BCM pin numbers)

| Sensor | Sensor pin | Pi pin |
|---|---|---|
| JSN-SR04T | VCC / GND | 5V (pin 2) / GND (pin 6) |
| | TRIG | GPIO23 (pin 16) |
| | ECHO | GPIO24 (pin 18) **through a 1k/2k divider (5V → 3.3V)** |
| Rain gauge | two leads | GPIO17 (pin 11) and GND |
| BME280 | VIN / GND | 3.3V (pin 1) / GND (pin 9) |
| | SDA / SCL | GPIO2 (pin 3) / GPIO3 (pin 5) |
| ADS1115 | VDD / GND / SDA / SCL | 3.3V / GND / GPIO2 / GPIO3 (shares the I2C bus) |
| Soil sensor | AOUT | ADS1115 A0 |

> The Pi's GPIO pins are **3.3V only**. Connecting a 5V echo line directly can damage the Pi.

## Pi setup

```bash
sudo raspi-config                     # Interface Options → enable I2C
git clone <repo-url> ~/team-claptac && cd ~/team-claptac/hardware/pi
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml    # set node_id, location, broker IP, sensors
i2cdetect -y 1                        # BME280 should appear at 0x76 (or 0x77)
python sensor_node.py --config config.yaml
```

To run it on boot:

```bash
sudo cp atlas-sensor.service /etc/systemd/system/
sudo systemctl enable --now atlas-sensor
journalctl -u atlas-sensor -f
```

## Server / laptop side

Start an MQTT broker on the machine that runs ATLAS:

```bash
mosquitto -c hardware/mosquitto/mosquitto.conf
```

Watch the readings coming in:

```bash
mosquitto_sub -h localhost -t "atlas/sensors/#" -v
```

ATLAS subscribes using `atlas.ingest.sensors.run_mqtt_listener`. Nodes can also POST to `/ingest/sensor` on the FastAPI app (`transport.kind: http`).

## Message format

Topic: `atlas/sensors/<node_id>/<type>`

```json
{
  "node_id": "rtp-node-01",
  "sensor_id": "wl-01",
  "type": "water_level",
  "value": 27.5,
  "unit": "cm",
  "timestamp": "2026-09-18T10:00:00+00:00",
  "location": {"lat": 6.6828, "lon": 80.3992, "area_id": "ratnapura-demo-river"}
}
```

`type` must be one of `water_level`, `rainfall`, `soil_moisture`, `temperature`, `humidity`, `pressure`, `tilt`. The server rejects anything else.

## Developing without a Pi

In `config.yaml`, replace the hardware sensors with `driver: mock` entries. They work on any laptop. `scenario: flood_ramp` makes the water level rise smoothly from `baseline` to `peak` over `ramp_seconds`.

## Demo setup (tabletop flood model)

1. Mount the ultrasonic sensor above a clear container that represents the river, with `mount_height_cm` set to the distance from the sensor to the container floor.
2. Put the node's `area_id` on a road edge next to the river in the Ratnapura OSM graph (`data/reference/sensor_map.yaml`).
3. Pour in water. The water level crosses the A1 threshold, A1 emits a trigger, A3 wakes up, and A4 reroutes around the flooded edge. The dashboard shows the live reading and the new route.
4. **Fallback:** if the hardware fails on the day, switch the node to `mock` + `flood_ramp`. The same messages flow through the same pipeline.

## Safety

Keep water well away from the Pi and the breadboard. Only the waterproof JSN-SR04T probe should be anywhere near the container.
