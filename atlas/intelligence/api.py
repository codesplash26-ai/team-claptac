"""Intelligence endpoints: persisted advice, read-only Q&A and unsubmitted report previews."""

import asyncio
import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from atlas.intelligence.context import capture
from atlas.intelligence.extraction import extract
from atlas.intelligence.nlquery import ask
from atlas.intelligence.settings import SettingsUpdate
from atlas.knowledge.graph import InMemoryWorldGraph
from atlas.manager.rbac import LLM_CONFIGURE, RUN_READ, SETTINGS_READ, TRACE_READ, requires

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class Question(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=2000)


class ReportText(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    node_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=280)


@router.get("/advice", dependencies=requires(RUN_READ))
async def advice(request: Request, incident_id: str | None = None):
    return request.app.state.system.intelligence.list(incident_id)


@router.get("/evidence/{ref}", dependencies=requires(TRACE_READ))
async def evidence(ref: str, request: Request):
    item = request.app.state.system.intelligence.evidence(ref)
    if item is None:
        raise HTTPException(404, "Unknown evidence reference")
    return item


@router.post("/ask", dependencies=requires(RUN_READ))
async def question(body: Question, request: Request):
    system = request.app.state.system
    context = capture(system)
    system.intelligence.save_evidence(context)
    return await ask(
        body.question,
        provider=system.intelligence.provider,
        ontology=context,
        tools=context,
        timeout=system.intelligence.request_timeout,
    )


def area_graph(system):
    graph = InMemoryWorldGraph()
    path = Path(__file__).resolve().parents[2] / "data/reference/vulnerable_populations.json"
    data = json.loads(path.read_text())
    for area in data:
        graph.add_entity("area:" + area["area_id"], "Area", name=area.get("name", ""))
    for belief in system.runtime.snapshot()["beliefs"]:
        if belief["subject"].startswith("area:") and not graph.has_entity(belief["subject"]):
            graph.add_entity(belief["subject"], "Area")
    return graph


@router.post("/extract", dependencies=requires(RUN_READ))
async def extraction(body: ReportText, request: Request):
    system = request.app.state.system
    return await extract(
        body.text,
        node_id=body.node_id,
        provider=system.intelligence.provider,
        graph=area_graph(system),
        timeout=system.intelligence.request_timeout,
    )


@router.get("/settings", dependencies=requires(SETTINGS_READ))
async def llm_settings(request: Request):
    return request.app.state.system.intelligence.settings.public()


async def settings_input(request):
    # FastAPI's default validation detail can echo the submitted body, including a key.
    try:
        return SettingsUpdate.model_validate(await request.json())
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_input=False, include_context=False)) from None
    except (ValueError, UnicodeError):
        raise HTTPException(422, "Invalid settings JSON") from None


@router.put("/settings", dependencies=requires(LLM_CONFIGURE))
async def save_llm_settings(request: Request):
    update = await settings_input(request)
    return request.app.state.system.intelligence.configure(update)


@router.post("/settings/test", dependencies=requires(LLM_CONFIGURE))
async def test_llm_settings(request: Request):
    update = await settings_input(request)
    service = request.app.state.system.intelligence
    provider = service.settings.build_provider(update)
    if provider is None:
        return {"ok": False, "message": "Select a provider and enter an API key before testing."}
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            provider.complete(
                system="This is a connection test. Reply with OK.",
                messages=[{"role": "user", "content": "Reply with OK."}],
                tools=[],
            ),
            timeout=update.timeout_seconds,
        )
        if not result.text or result.used_fallback:
            return {
                "ok": False,
                "message": "No text returned. Check the model and output token limit.",
            }
        return {
            "ok": True,
            "message": "Connection succeeded. Settings have not been saved by this test.",
            "provider": update.provider,
            "model": update.model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
        }
    except TimeoutError:
        return {
            "ok": False,
            "message": "Connection timed out. Increase timeout or lower thinking effort.",
        }
    except Exception:
        # Provider messages can contain credentials or echoed request bodies; never reflect them.
        return {
            "ok": False,
            "message": "Provider rejected the request or is unavailable. Check the API key, model access, thinking compatibility and output token limit.",
        }
