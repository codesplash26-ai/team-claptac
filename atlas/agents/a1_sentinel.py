"""Always-on structured sensing. No LLM is needed for mandatory detection rules."""

import json
from datetime import datetime, timezone
from hashlib import sha256

from atlas.agents.base import Agent
from atlas.agents.planning.sentinel import SentinelAdapter
from atlas.core.config import SentinelConfig
from atlas.core.events import Belief, Event, EventType, Observation, Trigger
from atlas.ingest.feeds import FeedReport
from atlas.ingest.sensors import SensorReading
from atlas.memory.evidence_store import EvidenceStore
from atlas.reasoning import PlanningRequest
from atlas.reasoning.tracing import record_result

# What a threshold crossing on each measurement is evidence of. A "warning" carries its own hazard.
HAZARD_OF = {
    "water_level": "flood",
    "water_rate_of_rise": "flood",
    "rainfall": "flood",
    "soil_moisture": "landslide",
    "tilt": "landslide",
    "earthquake": "earthquake",
}


class Sentinel(Agent):
    name = "a1_sentinel"
    subscribes_to = (EventType.OBSERVATION,)

    def __init__(self, bus, cop, ledger, *, config=None, planner=None, clock=None):
        super().__init__(bus, cop, ledger)
        self.config = config or SentinelConfig()
        if any(not 0 <= weight <= 1 for weight in self.config.source_reliability.values()):
            raise ValueError("Source reliability must be in [0,1]")
        self.planner = planner
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.db = cop.db
        self.evidence = EvidenceStore(self.db)
        self.db.connection.executescript("""
            CREATE TABLE IF NOT EXISTS observations (
                id TEXT PRIMARY KEY, area TEXT, attribute TEXT, source TEXT,
                timestamp REAL, value REAL, confidence REAL, evidence TEXT);
            CREATE INDEX IF NOT EXISTS observations_lookup ON observations(area,attribute,timestamp);
            CREATE TABLE IF NOT EXISTS sentinel_state (
                area TEXT, attribute TEXT, active INTEGER, score REAL, last_trigger REAL,
                PRIMARY KEY(area, attribute));
            CREATE TABLE IF NOT EXISTS sensor_status (
                node_id TEXT, sensor_type TEXT, last_seen REAL, observed_at REAL,
                value REAL, unit TEXT, PRIMARY KEY(node_id, sensor_type));
        """)

    def sensor_status(self, offline_after_seconds=60):
        """Dashboard handoff: transport liveness and measurement freshness are distinct."""
        now = self.clock().timestamp()
        return [
            {
                **dict(row),
                "online": now - row["last_seen"] < offline_after_seconds,
                "reading_stale": now - row["observed_at"] > self.config.freshness_seconds,
            }
            for row in self.db.connection.execute(
                "SELECT * FROM sensor_status ORDER BY node_id,sensor_type"
            )
        ]

    def _event(self, parent, kind, payload, now, incident_id=None):
        return Event(
            id=sha256(f"{parent.id}:{kind.value}".encode()).hexdigest(),
            type=kind,
            source=self.name,
            created_at=now,
            incident_id=incident_id,
            payload=payload,
        )

    async def handle(self, event):
        cfg, now = self.config, self.clock()
        evidence_id = self.evidence.put({"source": event.source, "payload": event.payload})

        def reject(reason):
            return [
                self._event(
                    event,
                    EventType.OBSERVATION_REJECTED,
                    {"reason": reason, "evidence_ids": [evidence_id]},
                    now,
                )
            ]

        warned_hazard = None
        try:
            if event.payload.get("kind") == "sensor":
                reading = SensorReading.model_validate(event.payload)
                area = reading.location.area_id if reading.location else None
                attribute, value, unit, observed = (
                    reading.type.value,
                    reading.value,
                    reading.unit,
                    reading.timestamp,
                )
                # Multiple instruments on a single node are not independent sources.
                source = "sensor:" + reading.node_id
                reliability = cfg.source_reliability.get(source, 0.8)
                self.db.connection.execute(
                    "INSERT OR REPLACE INTO sensor_status VALUES (?,?,?,?,?,?)",
                    (
                        reading.node_id,
                        attribute,
                        now.timestamp(),
                        observed.timestamp(),
                        value,
                        unit,
                    ),
                )
            else:
                report = FeedReport.model_validate(event.payload)
                area, attribute, value, unit, observed = (
                    report.area_id,
                    report.attribute,
                    report.value,
                    report.unit,
                    report.timestamp,
                )
                source = report.source
                warned_hazard = report.hazard
                reliability = cfg.source_reliability.get(
                    source, 0.9 if report.kind == "official_warning" else 0.5
                )
            expected = {
                "water_level": "cm",
                "rainfall": "mm",
                "soil_moisture": "%",
                "warning": "level",
                "temperature": "C",
                "humidity": "%",
                "pressure": "hPa",
                "tilt": "deg",
                "earthquake": "M",
            }
            if attribute == "water_level" and unit == "m":
                value, unit = value * 100, "cm"
            if unit != expected[attribute] or (attribute == "soil_moisture" and value > 100):
                return reject("invalid_unit_or_range")
        except (ValueError, KeyError):
            return reject("invalid_schema")
        if not area:
            return reject("unmapped_location")
        age = (now - observed).total_seconds()
        if age > cfg.freshness_seconds or age < -cfg.future_tolerance_seconds:
            return reject("stale_or_future")
        stamp = observed.timestamp()
        identity = sha256(json.dumps([source, area, attribute, stamp, value]).encode()).hexdigest()
        if self.db.connection.execute(
            "SELECT 1 FROM observations WHERE id=?", (identity,)
        ).fetchone():
            return reject("duplicate")
        previous = self.db.connection.execute(
            "SELECT * FROM observations WHERE area=? AND attribute=? AND source=? ORDER BY timestamp DESC LIMIT 1",
            (area, attribute, source),
        ).fetchone()
        if previous and stamp <= previous["timestamp"]:
            return reject("out_of_order")
        window_start = stamp - cfg.window_seconds
        peers = {}
        rows = self.db.connection.execute(
            "SELECT * FROM observations WHERE area=? AND attribute=? AND source!=? AND timestamp>=? AND timestamp<=? ORDER BY timestamp",
            (
                area,
                attribute,
                source,
                max(window_start, now.timestamp() - cfg.freshness_seconds),
                now.timestamp() + cfg.future_tolerance_seconds,
            ),
        )
        for row in rows:
            peers[row["source"]] = row
        # Rainfall is interval accumulation; comparing asynchronous intervals as
        # point observations would incorrectly flag conflicts.
        tolerance = {
            "water_level": cfg.conflict_water_cm,
            "soil_moisture": 10,
            "tilt": 1.0,  # degrees
            "earthquake": 0.5,  # agencies' magnitudes for one quake differ by a few tenths
        }.get(attribute, 0.5)
        comparable = attribute in {"water_level", "soil_moisture", "tilt", "earthquake", "warning"}
        trusted = [p for p in peers.values() if p["confidence"] >= cfg.minimum_confidence]
        conflict = comparable and any(abs(p["value"] - value) > tolerance for p in trusted)
        matches = [
            p
            for p in peers.values()
            if p["confidence"] >= cfg.corroboration_minimum
            and comparable
            and abs(p["value"] - value) <= tolerance
        ]
        confidence = max(
            0.0, min(0.99, reliability + 0.1 * len(matches) - (0.35 if conflict else 0))
        )
        evidence_ids = list(
            dict.fromkeys([evidence_id, *[p["evidence"] for p in trusted + matches]])
        )
        self.db.connection.execute(
            "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?)",
            (identity, area, attribute, source, stamp, value, reliability, evidence_id),
        )
        observation = Observation(
            observation_id=identity,
            area_id=area,
            attribute=attribute,
            value=value,
            unit=unit,
            observed_at=observed,
            received_at=now,
            source=source,
            confidence=confidence,
            evidence_ids=evidence_ids,
        )
        self.cop.put(
            Belief(
                subject="area:" + area,
                attribute=attribute,
                value=value,
                confidence=confidence,
                provenance=evidence_ids,
                updated_at=observed,
                ttl_seconds=cfg.freshness_seconds,
            )
        )
        outputs = [
            self._event(
                event, EventType.OBSERVATION_VALIDATED, observation.model_dump(mode="json"), now
            )
        ]
        if conflict:
            outputs.append(
                self._event(
                    event,
                    EventType.CONFLICT_FLAG,
                    {"area_id": area, "attribute": attribute, "evidence_ids": evidence_ids},
                    now,
                )
            )
            if cfg.reasoning_enabled and self.planner:
                result = await self.planner.plan(
                    PlanningRequest(
                        agent=self.name,
                        state={
                            "verification_success_probability": cfg.verification_success_probability
                        },
                        snapshot_version=self.cop.version,
                        evidence_ids=evidence_ids,
                        assumptions=[
                            "Synthetic verification success probability; independent of report confidence."
                        ],
                    ),
                    SentinelAdapter(),
                )
                outputs.append(record_result(self.ledger, result))
                outputs.append(
                    self._event(
                        event,
                        EventType.INFORMATION_GAP,
                        {
                            "area_id": area,
                            "recommendation": result.action.id
                            if result.action
                            else "flag_conflict",
                            "reasoning_id": result.id,
                            "evidence_ids": evidence_ids,
                        },
                        now,
                    )
                )
        score, reason = 0.0, attribute
        if attribute == "water_level":
            score = value / cfg.water_threshold_cm
            if (
                previous
                and stamp > previous["timestamp"]
                and stamp - previous["timestamp"] <= cfg.window_seconds
            ):
                rise = max(0, value - previous["value"]) * 60 / (stamp - previous["timestamp"])
                if rise / cfg.rise_threshold_cm_min > score:
                    score, reason = rise / cfg.rise_threshold_cm_min, "water_rate_of_rise"
        elif attribute == "rainfall":
            accumulated = self.db.connection.execute(
                "SELECT SUM(value) FROM observations WHERE area=? AND attribute=? AND source=? AND timestamp>=? AND timestamp<=?",
                (area, attribute, source, window_start, stamp),
            ).fetchone()[0]
            score = accumulated / cfg.rainfall_threshold_mm
        elif attribute == "soil_moisture":
            score = value / cfg.soil_threshold_pct
        elif attribute == "tilt":
            score = value / cfg.tilt_threshold_deg
        elif attribute == "earthquake":
            score = value / cfg.quake_threshold_magnitude
        elif attribute == "warning":
            score = value
        status = self.db.connection.execute(
            "SELECT * FROM sentinel_state WHERE area=? AND attribute=?", (area, attribute)
        ).fetchone()
        active, old_score, last = (
            (status["active"], status["score"], status["last_trigger"]) if status else (0, 0, 0)
        )
        if not conflict and confidence >= cfg.minimum_confidence:
            if score < cfg.rearm_ratio:
                active = 0
            if score >= 1 and (
                not active
                or (
                    score >= old_score + cfg.escalation_delta
                    and now.timestamp() - last >= cfg.cooldown_seconds
                )
            ):
                incident = "incident:" + area
                trigger = Trigger(
                    area_id=area,
                    reason=reason,
                    hazard=warned_hazard or HAZARD_OF.get(reason, "flood"),
                    score=score,
                    confidence=confidence,
                    evidence_ids=evidence_ids,
                    snapshot_version=self.cop.version,
                    observed_at=observed,
                    detection_latency_seconds=max(0, age),
                )
                outputs.append(
                    self._event(
                        event, EventType.TRIGGER, trigger.model_dump(mode="json"), now, incident
                    )
                )
                active, old_score, last = 1, score, now.timestamp()
            self.db.connection.execute(
                "INSERT OR REPLACE INTO sentinel_state VALUES (?,?,?,?,?)",
                (area, attribute, active, old_score, last),
            )
        return outputs
