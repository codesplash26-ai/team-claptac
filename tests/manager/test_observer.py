"""Traces in the OpenTelemetry model, and OTLP export."""

import json
import re
import time
import urllib.request

import pytest

from atlas.ingest.simulator import replay
from atlas.manager import observer
from atlas.manager.observer import (
    ExportError,
    OtlpExporter,
    build_trace,
    summaries,
    to_otlp,
    trace_id_for,
)
from tests.manager.conftest import run_flood
from tests.orchestration.conftest import reading, scenario

RISING = scenario(reading(0, 10), reading(120, 26), reading(130, 26))
INCIDENT = "incident:ratnapura-demo-river"


async def loop(system, runtime, clock):
    await runtime.start()
    await replay(runtime, RISING, clock)
    for _ in range(3):
        await system.control.gateway.run_pending()


async def test_a_trace_is_a_valid_opentelemetry_span_tree(system, runtime, clock):
    await loop(system, runtime, clock)
    spans = build_trace(runtime, INCIDENT)
    root = spans[0]
    assert root.kind == "root" and root.parent_span_id is None and root.name == "atlas.incident"
    assert {s.trace_id for s in spans} == {trace_id_for(INCIDENT)}
    assert all(
        re.fullmatch(r"[0-9a-f]{32}", s.trace_id) and re.fullmatch(r"[0-9a-f]{16}", s.span_id)
        for s in spans
    )
    ids = {s.span_id for s in spans}
    assert len(ids) == len(spans)  # unique
    assert all(s.parent_span_id in ids for s in spans[1:])  # a real tree, rooted
    assert all(s.end_ns >= s.start_ns for s in spans)
    assert root.start_ns <= min(s.start_ns for s in spans[1:])  # the root covers its children


async def test_spans_carry_the_generative_ai_semantic_conventions(system, runtime, clock):
    await loop(system, runtime, clock)
    spans = build_trace(runtime, INCIDENT)
    handler = next(s for s in spans if s.kind == "handler" and s.agent == "a3_incident_commander")
    assert handler.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert handler.attributes["gen_ai.agent.name"] == "a3_incident_commander"
    assert (
        handler.attributes["atlas.event.type"]
        and handler.attributes["atlas.incident.id"] == INCIDENT
    )
    tool = next(s for s in spans if s.kind == "executor")
    assert tool.attributes["gen_ai.operation.name"] == "execute_tool"
    assert tool.attributes["gen_ai.tool.name"] in ("a8_dispatch", "a9_comms")
    inp = next(s for s in spans if s.kind == "input")
    assert "gen_ai.operation.name" not in inp.attributes
    assert json.loads(handler.attributes["atlas.input"])["type"]  # what it received, as JSON


async def test_ids_are_stable_so_exporting_twice_is_idempotent(system, runtime, clock):
    await loop(system, runtime, clock)
    a = [(s.trace_id, s.span_id, s.parent_span_id) for s in build_trace(runtime, INCIDENT)]
    b = [(s.trace_id, s.span_id, s.parent_span_id) for s in build_trace(runtime, INCIDENT)]
    assert a == b
    assert trace_id_for("incident:x") != trace_id_for("incident:y")


async def test_otlp_json_has_the_standard_shape(system, runtime, clock):
    await loop(system, runtime, clock)
    payload = to_otlp(build_trace(runtime, INCIDENT), environment="replay", project="ratnapura")
    (rs,) = payload["resourceSpans"]
    resource = {a["key"]: a["value"] for a in rs["resource"]["attributes"]}
    assert resource["service.name"] == {"stringValue": "atlas"}
    assert resource["deployment.environment"] == {"stringValue": "replay"}
    (scope,) = rs["scopeSpans"]
    span = scope["spans"][1]
    assert set(span) >= {
        "traceId",
        "spanId",
        "name",
        "kind",
        "startTimeUnixNano",
        "endTimeUnixNano",
        "attributes",
        "status",
        "parentSpanId",
    }
    assert span["kind"] in (1, 3, 4, 5) and span["status"]["code"] in (1, 2)
    assert re.fullmatch(r"\d+", span["startTimeUnixNano"])  # OTLP/JSON uses strings for int64
    assert "parentSpanId" not in scope["spans"][0]  # the root has none
    values = {a["key"]: a["value"] for a in span["attributes"]}
    assert "intValue" in values["atlas.outputs"] and "stringValue" in values["atlas.event.type"]
    json.dumps(payload)  # serialisable


async def test_an_error_span_maps_to_the_error_status(runtime, clock):
    async def boom(event):
        raise RuntimeError("kaput")

    from atlas.core.events import Event, EventType

    runtime.bus.subscribe("flaky", [EventType.INFORMATION_GAP], boom)
    with pytest.raises(RuntimeError):
        await runtime.bus.publish(
            Event(type=EventType.INFORMATION_GAP, source="x", incident_id="incident:e", payload={})
        )
    spans = build_trace(runtime, "incident:e")
    error = next(s for s in spans if s.agent == "flaky")
    assert error.status == "ERROR" and "kaput" in error.status_message
    assert spans[0].status == "ERROR"
    codes = [
        x["status"]["code"] for x in to_otlp(spans)["resourceSpans"][0]["scopeSpans"][0]["spans"]
    ]
    assert 2 in codes and codes.count(2) == 2  # the failing step and the root that contains it


async def test_summaries_list_one_trace_per_incident(system, runtime, clock):
    await loop(system, runtime, clock)
    (summary,) = summaries(runtime)
    assert summary.trace_id == trace_id_for(INCIDENT) and summary.status == "OK"
    assert summary.spans > 20 and summary.hazard == "flood" and summary.duration_ms > 0
    assert observer.find(runtime, summary.trace_id)[0].incident_id == INCIDENT
    assert observer.find(runtime, "0" * 32) is None


# ------------------------------------------------------------------------------ exporter


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_exporter_posts_json_with_headers_to_the_traces_path(monkeypatch):
    seen = {}

    def fake(request, timeout):
        seen.update(
            url=request.full_url,
            body=json.loads(request.data),
            headers=dict(request.headers),
            method=request.method,
        )
        return FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    exporter = OtlpExporter("http://collector:4318/", {"x-api-key": "k"})
    assert exporter.push({"resourceSpans": []}) == 200
    assert seen["url"] == "http://collector:4318/v1/traces" and seen["method"] == "POST"
    assert seen["body"] == {"resourceSpans": []}
    assert (
        seen["headers"]["X-api-key"] == "k"
        and seen["headers"]["Content-type"] == "application/json"
    )


def test_the_exporter_reads_its_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("ATLAS_OTLP_ENDPOINT", "https://otel.example/v1/traces")
    monkeypatch.setenv("ATLAS_OTLP_HEADERS", "authorization=Bearer abc,x-team=atlas")
    exporter = OtlpExporter()
    assert exporter.configured and exporter.headers == {
        "authorization": "Bearer abc",
        "x-team": "atlas",
    }
    monkeypatch.delenv("ATLAS_OTLP_ENDPOINT")
    with pytest.raises(ExportError, match="no OTLP endpoint"):
        OtlpExporter().push({})


def test_a_failing_endpoint_is_a_clear_error(monkeypatch):
    def boom(request, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(ExportError, match="connection refused"):
        OtlpExporter("http://x").push({})


# ----------------------------------------------------------------------------------- HTTP


def test_the_trace_api_lists_details_and_exports(client, monkeypatch):
    run_flood(client)
    (t,) = client.get("/am/v1/traces").json()
    detail = client.get(f"/am/v1/traces/{t['trace_id']}").json()
    stable = 0
    for _ in range(
        100
    ):  # executors finish a moment after the replay: wait until the span count holds
        again = client.get(f"/am/v1/traces/{t['trace_id']}").json()
        stable = stable + 1 if len(again["spans"]) == len(detail["spans"]) else 0
        detail = again
        if stable >= 15:
            break
        time.sleep(0.1)
    assert detail["trace"]["trace_id"] == t["trace_id"] and detail["spans"][0]["kind"] == "root"
    otlp = client.get(f"/am/v1/traces/{t['trace_id']}/otlp").json()
    assert otlp["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert client.get("/am/v1/traces/" + "0" * 32).status_code == 404
    # no endpoint configured: a clear 409, not a crash
    assert client.post(f"/am/v1/traces/{t['trace_id']}/export").status_code == 409
    posted = []
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout: posted.append(json.loads(req.data)) or FakeResponse(),
    )
    client.app.state.exporter = OtlpExporter("http://collector:4318")
    ok = client.post(f"/am/v1/traces/{t['trace_id']}/export").json()
    assert ok["exported"] == len(detail["spans"]) and ok["endpoint_status"] == 200
    assert len(posted[0]["resourceSpans"][0]["scopeSpans"][0]["spans"]) == len(detail["spans"])
