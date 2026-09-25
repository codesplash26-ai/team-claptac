# LoRa node (field side)

Reads the existing Pi sensor drivers (`hardware/pi/sensors.py`), packs a signed READING frame, waits for the gateway ACK, and retries. The sequence counter is stored in `lora-seq.json` so a reboot does not replay old sequence numbers.

```bash
cp config.example.yaml config.yaml   # same HMAC key as the gateway
python run.py --config config.yaml
python run.py --config config.yaml --mock-radio   # encode only; no SPI
```

Antenna on the Ra-02 before any real transmit. Use `driver: mock` + `flood_ramp` until the ultrasonic is wired.
