# Hardware and LoRa (Hasindu)

**Goal:** readings, field reports and mission acknowledgements travel from the field to ATLAS over LoRa
with no internet, and the rest of the system cannot tell the difference from MQTT or HTTP.

```
node (Pi/ESP32 + sensors + Ra-02) --433 MHz--> gateway (Pi + Ra-02) --> atlas/ingest/lora.py --> bus
```

## You own
`hardware/lora/` (node firmware, gateway service, protocol, wiring), `atlas/ingest/lora.py`,
`atlas/ingest/field.py`, `hardware/pi/` (existing sensor node: you take it over from Aeshan; coordinate).

## The module
Ra-02 / SX1278, 433 MHz, SPI, 3.3 V only, up to +20 dBm, 10 km is line-of-sight. It is a bare radio:
no LoRaWAN, so we define the protocol (`hardware/lora/protocol.md`, a draft for you to confirm).
Stamp-hole package: you need a breakout board. Never transmit without an antenna. Buy at least three
modules (two nodes, one gateway). Check the Sri Lankan rules for 433 MHz (TRCSL) before the demo; this
is not verified.

## Tasks, in order
- [ ] Order breakout boards and 433 MHz antennas; bench-test one module with the SPI library of your choice.
- [ ] Confirm or change the frame layout in `protocol.md`; tell the team if anything in `docs/contracts.md` moves.
- [ ] `atlas/ingest/lora.py`: codec (encode/decode/HMAC), `LoraGateway.poll_once`, duplicate and gap handling, ACKs.
- [ ] Tests using `MockLoraLink`: round trip, bad signature dropped, duplicate dropped, oversize refused.
- [ ] Gateway service: radio in, existing ingest out (`run_mqtt_listener`'s callback shape or POST `/ingest/sensor`).
- [ ] Node firmware: batch readings, `age_seconds` instead of a clock, retry until ACK, offline buffering (reuse `hardware/pi/outbox.py`).
- [ ] Field report and mission ack uplinks; settle open decisions 1 and 2 in `docs/contracts.md` with Dinuka.
- [ ] Range test and airtime budget (spreading factor vs range vs how often a node may send). Record numbers in `hardware/lora/README.md`.
- [ ] Demo fallback: mock node with `flood_ramp` must produce the same messages.

## Use meanwhile
`MockLoraLink`, `hardware/pi/sensor_node.py` with `driver: mock`. Nothing else in ATLAS is waiting for you.

## Done when
A reading sent from a node over the air appears as an `OBSERVATION_VALIDATED` event in the ledger, a
tampered frame does not, and the same test passes with the mock link and with real radios.

## Do not
Change `SensorReading`, `atlas/core/events.py`, or A1. If the radio needs something they do not offer,
open a contract PR.
