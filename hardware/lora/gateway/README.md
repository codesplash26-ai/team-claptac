# LoRa gateway

Receives signed frames on a Pi + Ra-02, ACKs them, and posts the same `SensorReading` JSON that MQTT and HTTP already use. Downstream (A1 and the rest) does not change.

```bash
cp config.example.yaml config.yaml   # set HMAC key, laptop URL, node table
# Antenna on the Ra-02 before starting — the gateway transmits ACKs.
python run.py --config config.yaml
python run.py --config config.yaml --mock   # no radio; injects a rising water level
```

`--mock` is the laptop test and the demo-day fallback. Real radios need a 433 MHz antenna.
