# LoRa frame protocol (v1)

The frame format the LoRa nodes and gateway use. `atlas/ingest/lora.py` and the node firmware
implement it and must stay in step.

## Goals
A tiny, authenticated, duplicate-safe message format that fits one radio packet (255 bytes max) and maps
straight onto the existing `SensorReading`, so nothing downstream changes.

## Radio settings (starting point, to be tuned in the range test)
| Setting | Start with | Note |
|---|---|---|
| Frequency | 433 MHz band, exact channel TBD | confirm what is permitted locally |
| Spreading factor / bandwidth / coding rate | SF9 / 125 kHz / 4:5 | trade range against airtime |
| Sync word | private (0x12) | keeps our frames apart from LoRaWAN traffic |
| Preamble | 8 symbols | |
| TX power | start low, raise only as range needs | |

## Frame
```
 0     1     2      3-4       5-6     7      8 .. 8+n-1        last 8
+-----+-----+------+---------+-------+------+-----------------+----------+
|0xA7 | ver | type | node_id |  seq  | len  |     payload     | HMAC tag |
+-----+-----+------+---------+-------+------+-----------------+----------+
 1     1     1      2         2       1      n (<= 239)        8
```
- `ver` = 1. `node_id` and `seq` are big-endian unsigned 16-bit. `len` = payload length in bytes.
- `tag` = first 8 bytes of HMAC-SHA256(key, header + payload). A frame with a bad tag is dropped and counted.
  One shared key per deployment at first, from `.env` (`LORA_HMAC_KEY`); per-node keys later.
- `seq` increases by one per frame per node. The gateway drops repeats and records gaps (lost frames).

## Types
| type | name | direction | payload |
|---|---|---|---|
| 0x01 | READING | node to gateway | `count` (1 byte), then `count` times: `sensor` (1: id from `SENSOR_TYPE_IDS`), `index` (1: which sensor of that type), `value` (4: float32), `age_s` (2: seconds since measured) |
| 0x02 | FIELD_REPORT | node to gateway | `area_code` (2), `kind` (1), `people` (2), `urgency` (1: 0-100), then UTF-8 text |
| 0x03 | MISSION_ACK | node to gateway | `mission` (6: first 6 bytes of the mission id), `change` (1), `percent` (1), then UTF-8 note |
| 0x04 | HEARTBEAT | node to gateway | `battery_mV` (2), `uptime_s` (4), `boot` flag (1) |
| 0x10 | ACK | gateway to node | `node_id` (2), `seq` (2) being acknowledged |

Nodes have no clock. `age_s` says how old a reading is; the gateway stamps arrival time and sets
`timestamp = arrival - age`. Units follow the sensor type (`SensorReading.unit`): water level cm, rainfall
mm, soil moisture %, temperature degrees C, humidity %, pressure hPa, tilt degrees. The gateway derives a
stable `measurement_id` from `node_id`, `seq` and position, so a resent frame is idempotent downstream.

`area_code` is an index into a table in the gateway's config that maps to area ids such as
`ratnapura-demo-river`; the same table maps `node_id` to the node's location.

## Behaviour
- Node: send, wait for ACK, retry with back-off and a cap, keep unsent readings in the outbox
  (`hardware/pi/outbox.py`), never block sensing on the radio.
- Gateway: verify, drop duplicates, ACK, convert, emit events. Log RSSI and SNR per frame.
- Airtime: budget how often a node may transmit from the range-test numbers; do not assume it is free.

## Known risks (decide before the demo)
- **Replay.** A recorded valid frame can be resent. The sequence window blunts it; a node that reboots
  loses its counter. Persist the counter, or add a boot counter to the header.
- **Shared key.** One leaked node exposes the deployment. Per-node keys if time allows.
- **Confidentiality.** Frames are signed, not encrypted. Nothing sensitive should go over the air.
- **Range.** 10 km is a best case. Plan on a fraction of it in hilly terrain and measure.
