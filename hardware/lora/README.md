# LoRa link (Ra-02 / SX1278, 433 MHz)

Frame format: `protocol.md`. Gateway: `gateway/`, node: `node/`, codec: `atlas/ingest/lora.py`.

```
Sensors -> node (Pi + Ra-02) ~~ 433 MHz ~~> gateway (Pi + Ra-02) -> atlas/ingest/lora.py -> ATLAS
                                          <~~ ACK ~~
```

**You need an antenna.** Every Ra-02 that transmits (the node, and the gateway when it ACKs) must have a 433 MHz antenna on the ANT pad. Transmitting without one can burn the module. The code cannot detect a missing antenna.

Software can be tested without radios:

```bash
# laptop: ATLAS API
uvicorn atlas.api.main:app --reload

# laptop: gateway with fake rising water (no SPI)
export LORA_HMAC_KEY=$(python -c "import os; print(os.urandom(32).hex())")
cp hardware/lora/gateway/config.example.yaml /tmp/lora-gw.yaml
# put the same key in hmac_key_hex or keep the env var
python hardware/lora/gateway/run.py --config /tmp/lora-gw.yaml --mock
```

With two Pis and two Ra-02 modules (plus antennas), drop `--mock` and use the real radio. Same `LORA_HMAC_KEY` on both sides.

## The module
Ra-02 uses a Semtech SX1278: 433 MHz, SPI (half duplex), supply 1.8-3.7 V (use 3.3 V), up to +20 dBm.
Sensitivity to -148 dBm. "10 km" is line of sight only.

- **3.3 V only.** Do not connect it to a 5 V Arduino or level-shift accordingly.
- **Stamp-hole pads, not breadboard pins.** You need an adapter board.
- **Never transmit without an antenna.**
- **No LoRaWAN.** This is the raw radio. Addressing, HMAC and retries are in `protocol.md`.
- **Rules.** Check TRCSL requirements for 433 MHz before the demo. Not verified.

## Wiring (BCM)

| Ra-02 | Pi BCM | Physical pin |
|---|---|---|
| NSS | GPIO8 (CE0) | 24 |
| RESET | GPIO22 | 15 |
| DIO0 | GPIO25 | 22 |
| SCK | GPIO11 | 23 |
| MOSI | GPIO10 | 19 |
| MISO | GPIO9 | 21 |
| 3.3V | 3.3V | 1 |
| GND | GND | 6 |
| ANT | 433 MHz antenna | required |

Enable SPI: `sudo raspi-config` → Interface Options → SPI.

## Radio settings (starting point)

Frequency 433 MHz, SF9, 125 kHz, 4/5, sync 0x12, preamble 8, TX 7 dBm. Raise power only after a range test.

## Setup notes
- SPI library: `spidev` + `gpiozero` (`hardware/lora/sx1278.py`).
- Range test results and airtime budget: fill in after the bench test.
