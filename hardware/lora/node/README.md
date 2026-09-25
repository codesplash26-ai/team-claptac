# LoRa node (field side)

Owner: Hasindu. Firmware or Python for the sensor node's radio: batch readings from the existing sensor
drivers (`hardware/pi/sensors.py`), sign and send frames per `../protocol.md`, wait for the gateway's
ACK, retry with back-off, keep unsent readings in an outbox while the link is down. Sensing must never
block on the radio. Start with `driver: mock` sensors so the radio can be tested without a river.
