# LoRa link (Ra-02 / SX1278, 433 MHz)

Owner: Hasindu. Plan and tasks: `docs/workstreams/hardware-lora.md`. Frame format: `protocol.md`.

```
Sensors -> node (Pi or ESP32 + Ra-02) ~~ 433 MHz ~~> gateway (Pi + Ra-02) -> atlas/ingest/lora.py -> bus
                                          <~~ ACK ~~
```

| Folder | What goes here |
|---|---|
| `node/` | firmware or Python for the field node: read sensors, batch, sign, send, wait for ACK, buffer offline |
| `gateway/` | the gateway service: receive, verify, drop duplicates, ACK, hand readings to ATLAS |
| `protocol.md` | the over-the-air frame format (a draft; the contract) |

## The module
Ra-02 uses a Semtech SX1278: 433 MHz, SPI (half duplex), supply 1.8-3.7 V (use 3.3 V), up to +20 dBm,
sensitivity to -148 dBm, and a "10 km" range that only holds in line of sight with little interference.
The listing does not say which antenna is included: assume none and buy a 433 MHz antenna.

- **3.3 V only.** Do not connect it to a 5 V Arduino or level-shift accordingly. Raspberry Pi GPIO is 3.3 V.
- **Stamp-hole pads, not breadboard pins.** You need an adapter board.
- **Never transmit without an antenna.**
- **No LoRaWAN.** This is the raw LoRa radio. Point to point and star topologies work; there is no
  built-in addressing, security, or retry, which is why the protocol adds them.
- **Rules.** Check Sri Lanka's regulator (TRCSL) requirements for 433 MHz devices before the demo. Not verified.

## Setup notes (fill in as you go)
- Wiring table (BCM pins) for Pi + Ra-02: NSS, RESET, DIO0, SCK, MOSI, MISO, 3.3 V, GND.
- SPI library chosen and why.
- Radio settings used (frequency, spreading factor, bandwidth, coding rate, sync word, TX power).
- Range test results and the airtime budget (how often a node may send).
