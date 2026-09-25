"""Agent 9 - Public Communications & SITREP (CAP 1.2, si/ta/en).

An *executor* the Gateway calls after a public action is authorised. It never decides
whether to alert: publish_alert (district scope), cancel_alert and publish_sitrep arrive
as Gateway-approved actions from the Incident Commander.

Safety-critical fields (event, area, severity, standard instructions) are rendered from
structured data through the deterministic ``PhraseBook``. A translator is used only for
free text; if it is missing or fails, that language is left out and the receipt says so,
rather than a public alert being blocked or a guess being sent.

Inputs : authorised publish_alert / cancel_alert / publish_sitrep actions.
Outputs: CAP 1.2 XML (one <info> per language), per-language delivery through a Channel,
         SITREPs, ALERT_PUBLISHED events, the ``alerts`` / ``sitreps`` tables.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from datetime import datetime, timezone
from hashlib import sha256
from typing import Protocol

from atlas.agents.base import Agent
from atlas.control.models import ExecutionResult, Receipt
from atlas.core.events import Event, EventType, ProposedAction
from atlas.tools.cap import (
    CapAlert,
    CapArea,
    CapInfo,
    Category,
    Certainty,
    MsgType,
    ResponseType,
    Severity,
    Urgency,
    build_cap_xml,
    expiry,
    validate_cap_xml,
)
from atlas.tools.translation import BCP47, LANGUAGES, PhraseBook, Translator

SEVERITIES = {s.value for s in Severity} - {"Unknown"}
RESPONSE = {
    "evacuate": ResponseType.EVACUATE,
    "avoid_area": ResponseType.AVOID,
    "prepare": ResponseType.PREPARE,
}


class CommsError(ValueError):
    """Bad parameters or an unknown alert."""


class Channel(Protocol):
    """A dissemination channel (SMS / WhatsApp / broadcast). Must tolerate a resend."""

    def send(self, alert_id: str, language: str, headline: str, body: str) -> None: ...


class LogChannel:
    """Default channel: records what would be sent. Replace with a real gateway."""

    def __init__(self, db):
        self.db = db
        db.connection.execute(
            "CREATE TABLE IF NOT EXISTS alert_deliveries (alert_id TEXT, language TEXT,"
            " headline TEXT, body TEXT, PRIMARY KEY (alert_id, language, headline))"
        )

    def send(self, alert_id, language, headline, body):
        self.db.connection.execute(
            "INSERT OR IGNORE INTO alert_deliveries VALUES (?,?,?,?)",
            (alert_id, language, headline, body),
        )


async def _resolve(value):
    return await value if inspect.isawaitable(value) else value


class Comms(Agent):
    name = "a9_comms"
    subscribes_to = ()

    def __init__(
        self,
        bus,
        cop,
        ledger,
        *,
        clock=None,
        channel: Channel | None = None,
        translator: Translator | None = None,
        sender: str = "atlas.claptac",
        phrases: PhraseBook | None = None,
    ):
        super().__init__(bus, cop, ledger)
        self.db = cop.db
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.channel = channel or LogChannel(self.db)
        self.translator, self.sender = translator, sender
        self.phrases = phrases or PhraseBook()
        self.db.connection.executescript("""
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, action_key TEXT NOT NULL,
                msg_type TEXT NOT NULL, status TEXT NOT NULL, area TEXT NOT NULL,
                event TEXT NOT NULL, severity TEXT, cap_xml TEXT NOT NULL,
                texts TEXT NOT NULL, delivered TEXT NOT NULL, missing TEXT NOT NULL,
                sent_at TEXT NOT NULL, references_id TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS alerts_key ON alerts(action_key);
            CREATE TABLE IF NOT EXISTS sitreps (
                id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, action_key TEXT NOT NULL UNIQUE,
                generated_at TEXT NOT NULL, texts TEXT NOT NULL, data TEXT NOT NULL);
        """)

    async def handle(self, event: Event) -> list[Event]:
        return []

    # ------------------------------------------------------------ reads

    def get_alert(self, alert_id: str) -> dict | None:
        row = self.db.connection.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        return self._alert(row) if row else None

    def list_alerts(self, incident_id: str | None = None) -> list[dict]:
        rows = self.db.connection.execute(
            "SELECT * FROM alerts WHERE (? IS NULL OR incident_id=?) ORDER BY sent_at, id",
            (incident_id, incident_id),
        )
        return [self._alert(row) for row in rows]

    def list_sitreps(self, incident_id: str | None = None) -> list[dict]:
        rows = self.db.connection.execute(
            "SELECT * FROM sitreps WHERE (? IS NULL OR incident_id=?) ORDER BY generated_at, id",
            (incident_id, incident_id),
        )
        return [
            {**dict(r), "texts": json.loads(r["texts"]), "data": json.loads(r["data"])}
            for r in rows
        ]

    @staticmethod
    def _alert(row) -> dict:
        out = dict(row)
        for name in ("texts", "delivered", "missing"):
            out[name] = json.loads(out[name])
        return out

    # ---------------------------------------- Gateway executor protocol

    async def execute(self, action: ProposedAction) -> ExecutionResult:
        handler = {
            "publish_alert": self._publish_alert,
            "cancel_alert": self._cancel_alert,
            "publish_sitrep": self._sitrep,
        }.get(action.action_type)
        if handler is None:
            raise CommsError(f"A9 cannot execute {action.action_type!r}")
        ref, events, detail = await handler(action, action.params, self.clock())
        return ExecutionResult(
            receipt=Receipt(ref=ref, executed_at=self.clock(), detail=detail), events=events
        )

    def verify(self, action: ProposedAction, result: ExecutionResult) -> bool:
        if action.action_type == "publish_sitrep":
            return (
                self.db.connection.execute(
                    "SELECT 1 FROM sitreps WHERE id=?", (result.receipt.ref,)
                ).fetchone()
                is not None
            )
        alert = self.get_alert(result.receipt.ref)
        return bool(
            alert
            and not validate_cap_xml(alert["cap_xml"])
            and set(alert["delivered"]) >= set(alert["texts"])
        )

    def compensate(self, action: ProposedAction, result: ExecutionResult | None) -> None:
        """A public message cannot be unsent. Mark it failed so a human can decide."""
        self.db.connection.execute(
            "UPDATE alerts SET status='delivery_failed' WHERE action_key=? AND status='active'",
            (action.idempotency_key,),
        )

    # --------------------------------------------------------- alerts

    def _identifier(self, key: str) -> str:
        return "ATLAS-" + sha256(key.encode()).hexdigest()[:12]

    async def _publish_alert(self, action, p, now):
        key = action.idempotency_key
        alert_id = self._identifier(key)
        if self.get_alert(alert_id) is None:
            area = self._text(p, "area")
            event_code = self._text(p, "event")
            severity = p.get("severity", "Severe")
            if severity not in SEVERITIES:
                raise CommsError(f"severity must be one of {sorted(SEVERITIES)}")
            languages = self._languages(p)
            instruction_code = p.get("instruction", "evacuate")
            free_instruction = p.get("instruction_text")
            if instruction_code not in RESPONSE and not free_instruction:
                raise CommsError(f"unknown instruction {instruction_code!r}")
            texts, missing = await self._compose(
                event_code, area, severity, languages, instruction_code, free_instruction
            )
            if not texts:
                raise CommsError("no language could be rendered")
            infos = [
                self._info(lang, t, event_code, severity, p, instruction_code, area, now)
                for lang, t in texts.items()
            ]
            xml = build_cap_xml(
                CapAlert(
                    identifier=alert_id,
                    sender=self.sender,
                    sent=now,
                    msg_type=MsgType.ALERT,
                    infos=infos,
                )
            )
            problems = validate_cap_xml(xml)
            if problems:
                raise CommsError(f"generated CAP is invalid: {problems}")
            self.db.connection.execute(
                "INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    alert_id,
                    action.incident_id,
                    key,
                    "Alert",
                    "active",
                    area,
                    event_code,
                    severity,
                    xml,
                    json.dumps(texts, ensure_ascii=False),
                    "[]",
                    json.dumps(missing),
                    now.isoformat(),
                    None,
                ),
            )
        delivered = self._deliver(alert_id)
        alert = self.get_alert(alert_id)
        return (
            alert_id,
            [self._event(action, "alert", alert)],
            {"languages": sorted(delivered), "missing_languages": alert["missing"]},
        )

    async def _cancel_alert(self, action, p, now):
        target = self.get_alert(self._text(p, "alert_id"))
        if target is None or target["msg_type"] != "Alert":
            raise CommsError(f"unknown alert {p.get('alert_id')!r}")
        key = action.idempotency_key
        cancel_id = self._identifier(key)
        if self.get_alert(cancel_id) is None:
            if target["status"] != "active":
                raise CommsError(f"alert {target['id']} is {target['status']}, not active")
            texts = {
                lang: {
                    "headline": self.phrases.cancel_headline(target["area"], lang),
                    "body": self.phrases.cancelled(lang),
                }
                for lang in target["texts"]
            }
            infos = [
                CapInfo(
                    language=BCP47[lang],
                    categories=[Category.SAFETY],
                    event=self.phrases.event(target["event"], lang) or target["event"],
                    urgency=Urgency.UNKNOWN,
                    severity=Severity.UNKNOWN,
                    certainty=Certainty.UNKNOWN,
                    headline=t["headline"],
                    description=t["body"],
                )
                for lang, t in texts.items()
            ]
            sent_at = datetime.fromisoformat(target["sent_at"])
            xml = build_cap_xml(
                CapAlert(
                    identifier=cancel_id,
                    sender=self.sender,
                    sent=now,
                    msg_type=MsgType.CANCEL,
                    references=[(self.sender, target["id"], sent_at)],
                    infos=infos,
                )
            )
            self.db.connection.execute(
                "INSERT INTO alerts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    cancel_id,
                    action.incident_id,
                    key,
                    "Cancel",
                    "active",
                    target["area"],
                    target["event"],
                    target["severity"],
                    xml,
                    json.dumps(texts, ensure_ascii=False),
                    "[]",
                    "[]",
                    now.isoformat(),
                    target["id"],
                ),
            )
            self.db.connection.execute(
                "UPDATE alerts SET status='cancelled' WHERE id=?", (target["id"],)
            )
        delivered = self._deliver(cancel_id)
        return (
            cancel_id,
            [self._event(action, "cancel", self.get_alert(cancel_id))],
            {"languages": sorted(delivered), "cancels": target["id"]},
        )

    def _deliver(self, alert_id: str) -> list[str]:
        alert = self.get_alert(alert_id)
        delivered = list(alert["delivered"])
        for lang, text in alert["texts"].items():
            if lang not in delivered:
                self.channel.send(alert_id, lang, text["headline"], text["body"])
                delivered.append(lang)
                self.db.connection.execute(
                    "UPDATE alerts SET delivered=? WHERE id=?", (json.dumps(delivered), alert_id)
                )
        return delivered

    async def _compose(self, event_code, area, severity, languages, instruction_code, free):
        texts, missing = {}, []
        for lang in languages:
            event = self.phrases.event(event_code, lang)
            if event is None and lang == "en":
                event = event_code
            elif event is None:
                event = await self._translate(event_code, lang)
            instruction = self.phrases.instruction(instruction_code, lang) if free is None else None
            if free is not None:
                instruction = free if lang == "en" else await self._translate(free, lang)
            if event is None or instruction is None:
                missing.append(lang)
                continue
            severity_word = self.phrases.severity(severity, lang)
            texts[lang] = {
                "headline": self.phrases.headline(event, area, lang),
                "body": self.phrases.description(event, area, severity_word, lang),
                "instruction": instruction,
                "event": event,
            }
        return texts, missing

    async def _translate(self, text, lang):
        if self.translator is None:
            return None
        return await self.translator.translate(text, lang)

    def _info(self, lang, t, event_code, severity, p, instruction_code, area, now):
        return CapInfo(
            language=BCP47[lang],
            categories=[Category.MET],
            event=t["event"],
            response_types=[RESPONSE.get(instruction_code, ResponseType.AVOID)],
            urgency=Urgency(p.get("urgency", "Immediate")),
            severity=Severity(severity),
            certainty=Certainty(p.get("certainty", "Likely")),
            effective=now,
            expires=expiry(now, int(p.get("expires_minutes", 360))),
            sender_name="ATLAS Disaster Response Coordinator",
            headline=t["headline"][:160],
            description=t["body"],
            instruction=t["instruction"],
            areas=[CapArea(description=area, circle=p.get("circle"))],
        )

    def _languages(self, p) -> list[str]:
        languages = p.get("languages", list(LANGUAGES))
        if (
            not isinstance(languages, list)
            or not languages
            or any(lang not in LANGUAGES for lang in languages)
        ):
            raise CommsError(f"languages must be a non-empty subset of {list(LANGUAGES)}")
        return list(dict.fromkeys(languages))

    @staticmethod
    def _text(params, name) -> str:
        value = params.get(name)
        if not isinstance(value, str) or not value.strip():
            raise CommsError(f"parameter {name!r} is required")
        return value.strip()

    # --------------------------------------------------------- SITREP

    async def _sitrep(self, action, p, now):
        key = action.idempotency_key
        sitrep_id = "sr-" + sha256(key.encode()).hexdigest()[:12]
        row = self.db.connection.execute(
            "SELECT 1 FROM sitreps WHERE id=?", (sitrep_id,)
        ).fetchone()
        if row is None:
            data = self._situation(action.incident_id, now)
            summary = p.get("summary") if isinstance(p.get("summary"), str) else None
            texts = {}
            for lang in LANGUAGES:
                lines = [
                    f"{self.phrases.label('title', lang)} - "
                    f"{self.phrases.label('incident', lang)} {action.incident_id} "
                    f"({now.isoformat()})",
                    f"{self.phrases.label('missions', lang)}: {data['active_missions']}",
                    f"{self.phrases.label('pending', lang)}: {data['pending_approvals']}",
                    f"{self.phrases.label('alerts', lang)}: {data['active_alerts']}",
                ]
                if summary:
                    text = summary if lang == "en" else await self._translate(summary, lang)
                    if text:
                        lines.append(text)
                texts[lang] = "\n".join(lines)
            self.db.connection.execute(
                "INSERT INTO sitreps VALUES (?,?,?,?,?,?)",
                (
                    sitrep_id,
                    action.incident_id,
                    key,
                    now.isoformat(),
                    json.dumps(texts, ensure_ascii=False),
                    json.dumps(data),
                ),
            )
        event = Event(
            type=EventType.ALERT_PUBLISHED,
            source=self.name,
            created_at=self.clock(),
            incident_id=action.incident_id,
            payload={"kind": "sitrep", "sitrep_id": sitrep_id},
        )
        return sitrep_id, [event], {"kind": "sitrep"}

    def _situation(self, incident_id: str, now: datetime) -> dict:
        def count(sql, *args):
            try:
                return self.db.connection.execute(sql, args).fetchone()[0]
            except sqlite3.OperationalError:  # table not created: that subsystem is not wired
                return 0

        return {
            "active_missions": count(
                "SELECT COUNT(*) FROM missions WHERE incident_id=? AND status IN "
                "('created','acknowledged','in_progress')",
                incident_id,
            ),
            "pending_approvals": count(
                "SELECT COUNT(*) FROM approvals WHERE incident_id=? AND status='pending'",
                incident_id,
            ),
            "active_alerts": count(
                "SELECT COUNT(*) FROM alerts WHERE incident_id=? AND msg_type='Alert' "
                "AND status='active'",
                incident_id,
            ),
            "cop_version": self.cop.version,
        }

    def _event(self, action, kind, alert) -> Event:
        return Event(
            type=EventType.ALERT_PUBLISHED,
            source=self.name,
            created_at=self.clock(),
            incident_id=action.incident_id,
            payload={
                "kind": kind,
                "alert_id": alert["id"],
                "area": alert["area"],
                "languages": sorted(alert["texts"]),
                "missing_languages": alert["missing"],
            },
        )
