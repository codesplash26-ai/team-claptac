"""Does this piece of evidence exist?  (Owner: Sumudu)

A claim may cite evidence ids; ``atlas.intelligence.grounding.ground`` rejects a claim whose evidence
does not exist and asks for a function ``ref -> bool``. ``EvidenceIndex.exists`` is that function:

    ground(advice, ontology, EvidenceIndex(runtime).exists)

It answers from the stores the ids came from, with one indexed lookup each, and it reads only: no
scans, no writes, and it never returns the content of anything (so it does not bypass the evidence
store's role check; it can only say whether an id is real).

What counts as evidence, in one table. Anything else is not evidence, and fails closed.

    form                                   is real if                                       written by
    -------------------------------------  -----------------------------------------------  ----------------
    <64 hex>                               a row in the evidence store, or an event id      A1 (Observation)
    <32 hex>                               an event id in the ledger                        the bus
    event:<id>                             a ledger record with that id                     the bus
    action:<key>[:<phase>]                 a ledger record for that action                  the Gateway, A8
    reasoning:<id>                         a ledger record with that id                     the planner
    cop:<subject>/<attribute>              a belief in the COP (even if since expired)      the COP
    cop-version:<n>                        n is not later than the COP's current version    derived facts
    a4-routing@cop-v<n>                    likewise                                         world graph
    mission:<id>                           a row in A8's missions table                     A8
    incident:<id>                          a row in A3's incidents table                    A3
    plan:<task id>                         a CANDIDATE_PLAN event answering that task       A4, A6, A7
    sensor_map:<area>                      an area in data/reference/sensor_map.yaml        A4
    data/... config/... atlas/...          a file in the repository under those folders     reference facts

Ids are matched exactly. A near miss (one character off a real hash) is not evidence.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from atlas.tools import geo
from atlas.tools.sensor_map import SensorMap

HEX64 = re.compile(r"[0-9a-f]{64}")
HEX32 = re.compile(r"[0-9a-f]{32}")
REPO_FOLDERS = ("data", "config", "atlas")


class EvidenceRef(BaseModel):
    ref: str
    kind: str  # evidence | event | action | reasoning | cop_belief | cop_version | mission | ...


class EvidenceIndex:
    def __init__(
        self, runtime: Any, *, sensor_map: SensorMap | None = None, root: Path | None = None
    ):
        self.runtime = runtime
        self.db = runtime.db.connection
        self.root = Path(root or geo.ROOT)
        self._areas = set((sensor_map or SensorMap.from_file()).areas)

    def __call__(self, ref: str) -> bool:
        return self.exists(ref)

    def exists(self, ref: str) -> bool:
        return self.explain(ref) is not None

    def explain(self, ref: str) -> EvidenceRef | None:
        """What kind of evidence this is, or None if it is not real."""
        if not isinstance(ref, str) or not ref or ref != ref.strip():
            return None
        for kind, check in self._checks(ref):
            try:
                if check():
                    return EvidenceRef(ref=ref, kind=kind)
            except sqlite3.OperationalError:
                continue  # a store this deployment does not have (no A3, no A8): nothing to find there
        return None

    # ---------------------------------------------------------------------------- routing

    def _checks(self, ref: str):
        if HEX64.fullmatch(ref):
            yield "evidence", lambda: self._row("SELECT 1 FROM evidence WHERE id=?", ref)
            yield "event", lambda: self._ledger(f"event:{ref}")
        elif HEX32.fullmatch(ref):
            yield "event", lambda: self._ledger(f"event:{ref}")
        prefix, sep, rest = ref.partition(":")
        if sep and rest:
            if prefix == "event":
                yield "event", lambda: self._ledger(ref)
            elif prefix == "reasoning":
                yield "reasoning", lambda: self._ledger(ref)
            elif prefix == "action":
                yield "action", lambda: self._action(rest)
            elif prefix == "cop":
                yield "cop_belief", lambda: self._belief(rest)
            elif prefix == "cop-version":
                yield "cop_version", lambda: self._version(rest)
            elif prefix == "mission":
                yield "mission", lambda: self._row("SELECT 1 FROM missions WHERE id=?", rest)
            elif prefix == "incident":
                yield "incident", lambda: self._incident(rest)
            elif prefix == "plan":
                yield "plan", lambda: self._plan(rest)
            elif prefix == "sensor_map":
                yield "sensor_map", lambda: rest in self._areas
        if ref.startswith("a4-routing@cop-v"):
            yield "routing_snapshot", lambda: self._version(ref.removeprefix("a4-routing@cop-v"))
        if ref.split("/", 1)[0] in REPO_FOLDERS:
            yield "reference_file", lambda: self._file(ref)

    # ------------------------------------------------------------------------- the lookups

    def _row(self, sql: str, value: str) -> bool:
        return self.db.execute(sql, (value,)).fetchone() is not None

    def _ledger(self, record_id: str) -> bool:
        return self._row("SELECT 1 FROM ledger WHERE record_id=?", record_id)

    def _action(self, key_and_phase: str) -> bool:
        """`action:<key>` (A8's provenance) or `action:<key>:<phase>` (the ledger's record id)."""
        if self._ledger(f"action:{key_and_phase}"):
            return True
        low, high = f"action:{key_and_phase}:", f"action:{key_and_phase};"  # ';' follows ':'
        row = self.db.execute(
            "SELECT 1 FROM ledger WHERE record_id>=? AND record_id<? LIMIT 1", (low, high)
        ).fetchone()
        return row is not None

    def _belief(self, spec: str) -> bool:
        subject, sep, attribute = spec.rpartition("/")
        if not sep or not subject or not attribute:
            return False
        return self.runtime.cop.get(subject, attribute, include_stale=True) is not None

    def _version(self, number: str) -> bool:
        return number.isdigit() and int(number) <= self.runtime.cop.version

    def _incident(self, key: str) -> bool:
        incident = key if key.startswith("incident:") else f"incident:{key}"
        return self._row("SELECT 1 FROM a3_incidents WHERE incident_id=?", incident)

    def _plan(self, task_id: str) -> bool:
        for record in self.runtime.ledger.records("event"):
            event = record["payload"]
            if event.get("type") == "candidate_plan" and event["payload"].get("task_id") == task_id:
                return True
        return False

    def _file(self, ref: str) -> bool:
        """A file inside the named repo folder. `data/../.env` resolves elsewhere and is refused."""
        if ".." in Path(ref).parts:
            return False
        folder = (self.root / ref.split("/", 1)[0]).resolve()
        path = (self.root / ref).resolve()
        return folder in path.parents and path.is_file()
