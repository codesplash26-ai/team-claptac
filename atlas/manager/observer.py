"""The observer: traces in the OpenTelemetry model.  (Owner: Dinuka)

ATLAS's agents are instrumented by construction (the bus times every handler, the Gateway every executor call),
so this module reads those timings and presents them the way OpenTelemetry does, using the GenAI
semantic conventions (``gen_ai.*``) where they fit:

    trace          one incident, from the outside input that started it to the last action it caused
    root span      ``atlas.incident`` (INTERNAL)
    input span     an outside input: a sensor reading, a report, a person (PRODUCER)
    handler span   an agent handling a message: ``gen_ai.operation.name = invoke_agent`` (CONSUMER)
    executor span  the Gateway calling A8/A9: ``execute_tool`` (CLIENT)
    llm span       a model call: ``chat``, with token counts (CLIENT); none exist yet

``to_otlp`` produces standard OTLP/JSON, and ``OtlpExporter`` posts it to any OTLP/HTTP endpoint
(``ATLAS_OTLP_ENDPOINT``), so ATLAS's traces can be viewed in any OpenTelemetry-compatible tool (LangSmith, Langfuse, Phoenix and
others). Trace and span ids are derived from ids we already have, so exporting the same
run twice produces the same ids (idempotent for backends that dedupe).
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
from typing import Any

from pydantic import BaseModel, Field

from atlas.dashboard.dev.runs import Span, list_runs, run_spans
from atlas.manager import VERSION

SPAN_KIND = {"root": 1, "handler": 5, "executor": 3, "input": 4, "llm": 3}  # OTLP SpanKind numbers
OPERATION = {"handler": "invoke_agent", "executor": "execute_tool", "llm": "chat"}
MAX_ATTR = 4000


def trace_id_for(incident_id: str) -> str:
    return hashlib.sha256(("trace:" + incident_id).encode()).hexdigest()[:32]


def span_id_for(key: str) -> str:
    return hashlib.sha256(("span:" + key).encode()).hexdigest()[:16]


class OtelSpan(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: str  # root | handler | executor | input | llm
    start_ns: int
    end_ns: int
    status: str  # OK | ERROR | UNSET
    status_message: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    agent: str = ""
    depth: int = 0


class TraceSummary(BaseModel):
    trace_id: str
    incident_id: str
    name: str
    start_ns: int
    end_ns: int
    duration_ms: float
    spans: int
    errors: int
    agents: list[str]
    status: str
    hazard: str | None = None
    area_id: str | None = None


def _attrs(span: Span, incident_id: str, event: dict | None) -> dict[str, Any]:
    a: dict[str, Any] = {
        "atlas.incident.id": incident_id,
        "atlas.event.id": span.event_id,
        "atlas.event.type": span.event_type,
        "atlas.span.kind": span.kind,
        "atlas.summary": span.summary,
        "atlas.outputs": span.outputs,
        "atlas.status": span.status,
    }
    if span.kind in OPERATION:
        a["gen_ai.operation.name"] = OPERATION[span.kind]
        a["gen_ai.agent.name"] = span.agent
        a["gen_ai.system"] = "atlas"
    if span.kind == "executor":
        a["gen_ai.tool.name"] = span.agent
    if span.kind == "llm":
        a["gen_ai.usage.input_tokens"] = 0
        a["gen_ai.usage.output_tokens"] = 0
    if event is not None:
        a["atlas.input"] = json.dumps(event, default=str)[:MAX_ATTR]
    return a


def _load_events(runtime, event_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    conn = runtime.db.connection
    for start in range(0, len(event_ids), 400):
        chunk = event_ids[start : start + 400]
        for row in conn.execute(
            f"SELECT body FROM ledger WHERE kind='event' AND record_id IN ({','.join('?' * len(chunk))})",
            ["event:" + i for i in chunk],
        ):
            body = json.loads(row["body"])
            out[body["id"]] = {
                "type": body["type"],
                "source": body["source"],
                "payload": body.get("payload", {}),
            }
    return out


def build_trace(runtime, incident_id: str) -> list[OtelSpan]:
    spans = run_spans(runtime, incident_id)
    if not spans:
        return []
    trace_id = trace_id_for(incident_id)
    events = _load_events(runtime, list({s.event_id for s in spans}))
    root_id = span_id_for("root:" + incident_id)
    first = min(s.start for s in spans)
    last = max(s.start + s.duration_ms / 1000 for s in spans)
    out = [
        OtelSpan(
            trace_id=trace_id,
            span_id=root_id,
            parent_span_id=None,
            name="atlas.incident",
            kind="root",
            start_ns=int(first * 1e9),
            end_ns=int(last * 1e9),
            status="ERROR" if any(s.status == "error" for s in spans) else "OK",
            attributes={"atlas.incident.id": incident_id, "atlas.spans": len(spans)},
            agent="atlas",
        )
    ]
    by_id = {s.id: s for s in spans}
    for s in spans:
        out.append(
            OtelSpan(
                trace_id=trace_id,
                span_id=span_id_for(s.id),
                parent_span_id=span_id_for(s.parent_id) if s.parent_id in by_id else root_id,
                name=f"{s.agent} {OPERATION.get(s.kind, s.kind)}"
                if s.kind != "input"
                else f"{s.agent} -> {s.event_type}",
                kind=s.kind,
                start_ns=int(s.start * 1e9),
                end_ns=int((s.start + s.duration_ms / 1000) * 1e9),
                status="ERROR" if s.status == "error" else "OK",
                status_message=s.error,
                attributes=_attrs(s, incident_id, events.get(s.event_id)),
                agent=s.agent,
                depth=s.depth + 1,
            )
        )
    return out


def summaries(runtime) -> list[TraceSummary]:
    out = []
    for run in list_runs(runtime):
        out.append(
            TraceSummary(
                trace_id=trace_id_for(run.incident_id),
                incident_id=run.incident_id,
                name=f"atlas.incident {run.incident_id.replace('incident:', '')}",
                start_ns=int(run.started * 1e9),
                end_ns=int(run.ended * 1e9),
                duration_ms=run.duration_ms,
                spans=run.spans,
                errors=run.errors,
                agents=run.agents,
                status="ERROR" if run.errors else "OK",
                hazard=run.hazard,
                area_id=run.area_id,
            )
        )
    return out


def find(runtime, trace_id: str) -> tuple[TraceSummary, list[OtelSpan]] | None:
    for summary in summaries(runtime):
        if summary.trace_id == trace_id:
            return summary, build_trace(runtime, summary.incident_id)
    return None


# ---------------------------------------------------------------------------- OTLP


def _value(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def to_otlp(spans: list[OtelSpan], *, environment: str = "live", project: str = "atlas") -> dict:
    """OTLP/JSON for one or more traces (``resourceSpans``)."""
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": "atlas"}},
                        {"key": "service.version", "value": {"stringValue": VERSION}},
                        {"key": "deployment.environment", "value": {"stringValue": environment}},
                        {"key": "atlas.project", "value": {"stringValue": project}},
                    ]
                },
                "scopeSpans": [
                    {
                        "scope": {"name": "atlas.agent-manager", "version": VERSION},
                        "spans": [
                            {
                                "traceId": s.trace_id,
                                "spanId": s.span_id,
                                **({"parentSpanId": s.parent_span_id} if s.parent_span_id else {}),
                                "name": s.name,
                                "kind": SPAN_KIND[s.kind],
                                "startTimeUnixNano": str(s.start_ns),
                                "endTimeUnixNano": str(max(s.end_ns, s.start_ns)),
                                "attributes": [
                                    {"key": k, "value": _value(v)} for k, v in s.attributes.items()
                                ],
                                "status": {
                                    "code": 2 if s.status == "ERROR" else 1,
                                    **({"message": s.status_message} if s.status_message else {}),
                                },
                            }
                            for s in spans
                        ],
                    }
                ],
            }
        ]
    }


class ExportError(RuntimeError):
    pass


class OtlpExporter:
    """POSTs OTLP/JSON to ``<endpoint>/v1/traces``. Configure with ATLAS_OTLP_ENDPOINT and, for a hosted
    backend, ATLAS_OTLP_HEADERS (``key=value,key2=value2``, e.g. an API key)."""

    def __init__(
        self,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 15,
    ):
        self.endpoint = (endpoint or os.getenv("ATLAS_OTLP_ENDPOINT") or "").rstrip("/")
        raw = os.getenv("ATLAS_OTLP_HEADERS", "")
        self.headers = (
            headers
            if headers is not None
            else dict(p.split("=", 1) for p in raw.split(",") if "=" in p)
        )
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.endpoint)

    def push(self, payload: dict) -> int:
        if not self.configured:
            raise ExportError("no OTLP endpoint configured (set ATLAS_OTLP_ENDPOINT)")
        url = (
            self.endpoint if self.endpoint.endswith("/v1/traces") else self.endpoint + "/v1/traces"
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Content-Type": "application/json", **self.headers},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status
        except Exception as exc:
            raise ExportError(f"OTLP export failed: {exc}") from exc
