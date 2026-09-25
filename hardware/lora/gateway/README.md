# LoRa gateway

Owner: Hasindu. A Pi with a Ra-02 that listens continuously, verifies and de-duplicates frames, ACKs
them, and hands the result to ATLAS through `atlas/ingest/lora.py` (`LoraGateway`). Its output is the
same as the MQTT and HTTP paths: `SensorReading`s into the existing ingest, so the Sentinel and
everything after it are unchanged. Also forwards field reports and mission acknowledgements. Config
holds the `node_id` and `area_code` tables and reads `LORA_HMAC_KEY` from the environment.
