"""CAP 1.2 alert XML builder and validator.

Covers the mandatory structure of OASIS CAP 1.2 (Alert, Update, Cancel) and the
enumerations that matter for interoperability. Pure functions, no I/O.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field, field_validator

NS = "urn:oasis:names:tc:emergency:cap:1.2"
_SENT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$")


class Status(str, Enum):
    ACTUAL = "Actual"
    EXERCISE = "Exercise"
    SYSTEM = "System"
    TEST = "Test"
    DRAFT = "Draft"


class MsgType(str, Enum):
    ALERT = "Alert"
    UPDATE = "Update"
    CANCEL = "Cancel"
    ACK = "Ack"
    ERROR = "Error"


class Scope(str, Enum):
    PUBLIC = "Public"
    RESTRICTED = "Restricted"
    PRIVATE = "Private"


class Category(str, Enum):
    GEO = "Geo"
    MET = "Met"
    SAFETY = "Safety"
    RESCUE = "Rescue"
    FIRE = "Fire"
    HEALTH = "Health"
    ENV = "Env"
    TRANSPORT = "Transport"
    INFRA = "Infra"
    CBRNE = "CBRNE"
    OTHER = "Other"


class Urgency(str, Enum):
    IMMEDIATE = "Immediate"
    EXPECTED = "Expected"
    FUTURE = "Future"
    PAST = "Past"
    UNKNOWN = "Unknown"


class Severity(str, Enum):
    EXTREME = "Extreme"
    SEVERE = "Severe"
    MODERATE = "Moderate"
    MINOR = "Minor"
    UNKNOWN = "Unknown"


class Certainty(str, Enum):
    OBSERVED = "Observed"
    LIKELY = "Likely"
    POSSIBLE = "Possible"
    UNLIKELY = "Unlikely"
    UNKNOWN = "Unknown"


class ResponseType(str, Enum):
    SHELTER = "Shelter"
    EVACUATE = "Evacuate"
    PREPARE = "Prepare"
    EXECUTE = "Execute"
    AVOID = "Avoid"
    MONITOR = "Monitor"
    ASSESS = "Assess"
    ALLCLEAR = "AllClear"
    NONE = "None"


class CapArea(BaseModel):
    description: str = Field(min_length=1)
    geocodes: dict[str, str] = Field(default_factory=dict)
    circle: str | None = None  # "lat,lon radius_km"


class CapInfo(BaseModel):
    language: str = Field(min_length=2)  # RFC 3066, e.g. "si-LK"
    categories: list[Category] = Field(min_length=1)
    event: str = Field(min_length=1)
    response_types: list[ResponseType] = Field(default_factory=list)
    urgency: Urgency
    severity: Severity
    certainty: Certainty
    effective: datetime | None = None
    expires: datetime | None = None
    sender_name: str | None = None
    headline: str | None = Field(default=None, max_length=160)
    description: str | None = None
    instruction: str | None = None
    areas: list[CapArea] = Field(default_factory=list)


class CapAlert(BaseModel):
    identifier: str = Field(min_length=1)
    sender: str = Field(min_length=1)
    sent: datetime
    status: Status = Status.ACTUAL
    msg_type: MsgType
    scope: Scope = Scope.PUBLIC
    references: list[tuple[str, str, datetime]] = Field(default_factory=list)  # sender, id, sent
    note: str | None = None
    infos: list[CapInfo] = Field(default_factory=list)

    @field_validator("identifier", "sender")
    @classmethod
    def no_reserved_chars(cls, value):
        if any(ch in value for ch in " ,<&"):
            raise ValueError("CAP identifier and sender may not contain spaces, commas, < or &")
        return value

    @field_validator("sent")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("CAP timestamps need a timezone")
        return value


def _stamp(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat()


def build_cap_xml(alert: CapAlert) -> str:
    if alert.msg_type in (MsgType.UPDATE, MsgType.CANCEL) and not alert.references:
        raise ValueError(f"a CAP {alert.msg_type.value} must reference the alert it changes")
    if alert.msg_type is not MsgType.CANCEL and not alert.infos:
        raise ValueError("a CAP Alert or Update needs at least one <info> block")
    ET.register_namespace("", NS)
    root = ET.Element(f"{{{NS}}}alert")

    def add(parent, tag, text=None):
        node = ET.SubElement(parent, f"{{{NS}}}{tag}")
        if text is not None:
            node.text = str(text)
        return node

    add(root, "identifier", alert.identifier)
    add(root, "sender", alert.sender)
    add(root, "sent", _stamp(alert.sent))
    add(root, "status", alert.status.value)
    add(root, "msgType", alert.msg_type.value)
    add(root, "scope", alert.scope.value)
    if alert.note:
        add(root, "note", alert.note)
    if alert.references:
        add(root, "references", " ".join(f"{s},{i},{_stamp(t)}" for s, i, t in alert.references))
    for info in alert.infos:
        node = add(root, "info")
        add(node, "language", info.language)
        for category in info.categories:
            add(node, "category", category.value)
        add(node, "event", info.event)
        for response in info.response_types:
            add(node, "responseType", response.value)
        add(node, "urgency", info.urgency.value)
        add(node, "severity", info.severity.value)
        add(node, "certainty", info.certainty.value)
        for tag, value in (("effective", info.effective), ("expires", info.expires)):
            if value is not None:
                add(node, tag, _stamp(value))
        for tag, value in (
            ("senderName", info.sender_name),
            ("headline", info.headline),
            ("description", info.description),
            ("instruction", info.instruction),
        ):
            if value:
                add(node, tag, value)
        for area in info.areas:
            area_node = add(node, "area")
            add(area_node, "areaDesc", area.description)
            if area.circle:
                add(area_node, "circle", area.circle)
            for name, value in area.geocodes.items():
                geocode = add(area_node, "geocode")
                add(geocode, "valueName", name)
                add(geocode, "value", value)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _enum(value: str, enum: type[Enum], what: str, errors: list[str]) -> None:
    if value not in {member.value for member in enum}:
        errors.append(f"{what}: {value!r} is not a valid CAP value")


def validate_cap_xml(xml: str) -> list[str]:
    """Return a list of problems; empty means structurally valid CAP 1.2."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [f"not well-formed XML: {exc}"]
    errors: list[str] = []
    if root.tag != f"{{{NS}}}alert":
        return [f"root must be <alert> in namespace {NS}"]

    def child(parent, tag):
        return parent.find(f"{{{NS}}}{tag}")

    def text(parent, tag):
        node = child(parent, tag)
        return node.text.strip() if node is not None and node.text else None

    for tag in ("identifier", "sender", "sent", "status", "msgType", "scope"):
        if not text(root, tag):
            errors.append(f"alert: missing required <{tag}>")
    if text(root, "sent") and not _SENT.match(text(root, "sent")):
        errors.append("sent: must be YYYY-MM-DDThh:mm:ss+hh:mm")
    for tag, enum in (("status", Status), ("msgType", MsgType), ("scope", Scope)):
        if text(root, tag):
            _enum(text(root, tag), enum, tag, errors)
    msg_type = text(root, "msgType")
    if msg_type in ("Update", "Cancel") and not text(root, "references"):
        errors.append(f"{msg_type}: <references> is required")
    infos = root.findall(f"{{{NS}}}info")
    if msg_type in ("Alert", "Update") and not infos:
        errors.append(f"{msg_type}: at least one <info> is required")
    for number, info in enumerate(infos, 1):
        where = f"info[{number}]"
        for tag in ("event", "urgency", "severity", "certainty"):
            if not text(info, tag):
                errors.append(f"{where}: missing required <{tag}>")
        if not info.findall(f"{{{NS}}}category"):
            errors.append(f"{where}: missing required <category>")
        for node in info.findall(f"{{{NS}}}category"):
            _enum(node.text or "", Category, f"{where} category", errors)
        for node in info.findall(f"{{{NS}}}responseType"):
            _enum(node.text or "", ResponseType, f"{where} responseType", errors)
        for tag, enum in (("urgency", Urgency), ("severity", Severity), ("certainty", Certainty)):
            if text(info, tag):
                _enum(text(info, tag), enum, f"{where} {tag}", errors)
        for area in info.findall(f"{{{NS}}}area"):
            if not text(area, "areaDesc"):
                errors.append(f"{where}: <area> needs <areaDesc>")
        for tag in ("effective", "expires"):
            if text(info, tag) and not _SENT.match(text(info, tag)):
                errors.append(f"{where} {tag}: must be YYYY-MM-DDThh:mm:ss+hh:mm")
    return errors


def expiry(sent: datetime, minutes: int) -> datetime:
    return sent + timedelta(minutes=minutes)
