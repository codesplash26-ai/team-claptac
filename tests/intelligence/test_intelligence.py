import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from atlas.api.main import create_app
from atlas.core.config import Settings
from atlas.core.events import Event, EventType
from atlas.intelligence.advisor import Advisor
from atlas.intelligence.context import ReadContext, capture
from atlas.intelligence.extraction import extract
from atlas.intelligence.nlquery import ask
from atlas.intelligence.providers import AnthropicProvider, OpenAIProvider, provider_from_env
from atlas.knowledge.graph import InMemoryWorldGraph
from atlas.tools.llm import LLMResult, RateLimiter
from tests.orchestration.conftest import reading, scenario


class FakeProvider:
    available = True
    name = "fake"
    model = "fake-v1"

    def __init__(self, data=None, error=None):
        self.data, self.error, self.calls = data, error, []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return LLMResult(json.dumps(self.data), tokens_in=10, tokens_out=20)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")


@pytest.fixture
def context():
    ctx = ReadContext()
    ctx.add("road:one", {"open": False}, confidence=0.6, provenance=["event:1"])
    return ctx


async def test_grounding_rejects_invented_entity_false_value_and_inflated_confidence(context):
    good = context.facts[0].model_dump()
    claims = [
        good,
        {**good, "subject": "road:invented"},
        {**good, "object": True},
        {**good, "confidence": 1.0},
        {**good, "evidence_ids": ["missing"]},
    ]
    provider = FakeProvider(
        {
            "summary": "EVERY ROAD IS OPEN",
            "claims": [
                {"text": "EVERY ROAD IS OPEN", "claim": c, "accepted": True} for c in claims
            ],
        }
    )
    advice = await Advisor(provider, context, context).advise({"incident_id": "i"})
    assert [c.accepted for c in advice.claims] == [True, False, False, False, False]
    assert "EVERY ROAD IS OPEN" not in advice.summary
    assert "false" in advice.summary
    assert advice.trace.tokens_in == 10 and advice.trace.tokens_out == 20
    assert any("Low confidence" in c for c in advice.concerns)


async def test_failure_cache_and_budget(context):
    provider = FakeProvider(error=ConnectionError("private credential must not leak"))
    advisor = Advisor(provider, context, context, max_calls=1)
    first = await advisor.advise({"incident_id": "i", "cycle": 1})
    first.summary = "changed by caller"
    cached = await advisor.advise({"incident_id": "i", "cycle": 1})
    await advisor.advise({"incident_id": "i", "cycle": 2})
    assert len(provider.calls) == 1
    assert cached.source == "template" and cached.summary != first.summary
    assert "private credential" not in cached.model_dump_json()


async def test_timeout_falls_back_without_blocking(context):
    class Slow(FakeProvider):
        async def complete(self, **kwargs):
            await asyncio.sleep(10)

    advice = await Advisor(Slow(), context, context, timeout=0.01).advise({"incident_id": "i"})
    assert advice.source == "template" and advice.claims[0].accepted


async def test_ask_is_grounded_and_sends_question_to_provider(context):
    answer = await ask("Which road is open?", provider=None, ontology=context, tools=context)
    assert answer.claims[0].accepted and not answer.used_llm
    provider = FakeProvider({"claims": []})
    await ask("Why is road:one closed?", provider=provider, ontology=context, tools=context)
    assert "Why is road:one closed?" in provider.calls[0]["messages"][0]["content"]
    assert [t.name for t in provider.calls[0]["tools"]] == ["search_facts", "graph_neighbors"]


async def test_extract_conservative_counts_and_ambiguous_entities():
    graph = InMemoryWorldGraph()
    graph.add_entity("area:kuruwita", "Area", name="Kuruwita")
    result = await extract(
        "Kuruwita: 12 people stranded, need rescue", node_id="n", provider=None, graph=graph
    )
    assert result.report.people == 12 and result.report.urgency is None
    assert result.report.area_id == "kuruwita"
    families = await extract(
        "Kuruwita: 12 families stranded", node_id="n", provider=None, graph=graph
    )
    assert families.report.people == 0 and families.unresolved
    unknown = await extract("Atlantis: 12 people stranded", node_id="n", provider=None, graph=graph)
    assert unknown.report is None
    negated = await extract("Kuruwita: road not blocked", node_id="n", provider=None, graph=graph)
    assert negated.report is None
    graph.add_entity("area:other", "Area", name="Kuruwita")
    assert (
        await extract("Kuruwita needs rescue", node_id="n", provider=None, graph=graph)
    ).report is None


async def test_rate_limiter_releases_lock_before_waiting(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("atlas.tools.llm.time.monotonic", lambda: now[0])
    limiter = RateLimiter(1)

    async def sleep(seconds):
        assert not limiter._lock.locked()
        now[0] += seconds

    monkeypatch.setattr("atlas.tools.llm.asyncio.sleep", sleep)
    await limiter.acquire()
    await limiter.acquire()
    assert len(limiter._timestamps) == 1


def test_provider_selection_and_anthropic_adapter(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda: None)
    for key in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert provider_from_env() is None
    client = SimpleNamespace(available=False, settings=SimpleNamespace(llm_model="test"))
    assert not AnthropicProvider(client).available
    monkeypatch.setenv("LLM_PROVIDER", "typo")
    with pytest.raises(ValueError):
        provider_from_env()


async def test_openai_tool_loop_with_mock_transport(context):
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        message = {"role": "assistant", "content": '{"claims":[]}'}
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c",
                        "type": "function",
                        "function": {"name": "search_facts", "arguments": '{"query":"road"}'},
                    }
                ],
            }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            },
        )

    provider = OpenAIProvider("test-key", "test-model", transport=httpx.MockTransport(respond))
    result = await provider.complete(system="s", messages=[], tools=context.tool_specs())
    assert result.tokens_in == 4 and result.tokens_out == 6
    assert "road:one" in requests[1]["messages"][-1]["content"]


async def test_worker_persists_once_and_model_never_holds_bus_lock(system, clock):
    from atlas.ingest.simulator import replay
    from atlas.intelligence.service import IntelligenceService

    await system.runtime.start()
    await replay(system.runtime, scenario(reading(0, 10), reading(120, 26)), clock)
    before = system.runtime.cop.version
    ledger_before = len(system.runtime.ledger.records())
    started, release = asyncio.Event(), asyncio.Event()

    class Slow(FakeProvider):
        async def complete(self, **kwargs):
            assert not system.runtime.db.connection.in_transaction
            assert not system.runtime.bus._lock.locked()
            started.set()
            await release.wait()
            return LLMResult('{"claims":[]}')

    service = system.intelligence
    service.provider = Slow()
    work = asyncio.create_task(service.tick())
    await asyncio.wait_for(started.wait(), 5)
    await asyncio.wait_for(
        system.runtime.bus.publish(Event(type=EventType.INFORMATION_GAP, source="test")), 1
    )
    release.set()
    await work
    assert service.list() and system.runtime.cop.version == before
    assert len(system.runtime.ledger.records()) > ledger_before
    restarted = IntelligenceService(system, provider=None)
    count = len(system.runtime.ledger.records())
    await restarted.tick()
    assert len(system.runtime.ledger.records()) == count
    assert system.runtime.ledger.verify()


async def test_worker_retains_pending_publication_for_restart(system, clock, monkeypatch):
    from atlas.ingest.simulator import replay

    await system.runtime.start()
    await replay(system.runtime, scenario(reading(0, 10), reading(120, 26)), clock)
    service = system.intelligence
    real_publish = service._publish

    async def fail(event):
        raise RuntimeError("publication interrupted")

    monkeypatch.setattr(service, "_publish", fail)
    with pytest.raises(RuntimeError):
        await service.tick()
    assert service.list()
    monkeypatch.setattr(service, "_publish", real_publish)
    await service.tick()
    assert not system.runtime.db.connection.execute(
        "SELECT 1 FROM intelligence_advice WHERE published=0"
    ).fetchone()


def test_api_preview_and_permissions(monkeypatch):
    with TestClient(create_app(Settings(database_path=":memory:"))) as client:
        assert client.get("/intelligence/advice").status_code == 200
        result = client.post(
            "/intelligence/extract",
            json={"node_id": "n", "text": "ratnapura-demo-river: 12 people stranded"},
        )
        assert result.status_code == 200 and result.json()["report"]["people"] == 12
        assert client.post("/intelligence/ask", json={"question": "approvals"}).status_code == 200
        assert (
            client.post(
                "/intelligence/extract", json={"node_id": "n", "text": "x" * 281}
            ).status_code
            == 422
        )
        monkeypatch.setenv("ATLAS_TOKENS", '[{"token":"secret","name":"viewer","role":"viewer"}]')
        assert client.get("/intelligence/advice").status_code == 401
        assert client.post("/intelligence/ask", json={"question": "a"}).status_code == 401
        assert (
            client.get(
                "/intelligence/advice", headers={"Authorization": "Bearer secret"}
            ).status_code
            == 200
        )


async def test_context_only_contains_detached_data(system):
    ctx = capture(system)
    before = system.runtime.cop.version
    ctx.add("fake:1", {"open": True})
    assert system.runtime.cop.version == before


def test_snapshot_graph_exposes_only_explicit_known_references(context):
    context.add("incident:i", {"road_id": "one", "shelter_id": "invented"})
    links = context.graph_neighbors("road:one")["links"]
    assert len(links) == 1 and links[0]["target"] == "road:one"
    assert context.graph_neighbors("shelter:invented")["known"] is False


async def test_shadow_planner_uses_triage_group_and_rejects_stale_snapshot(system, clock):
    from atlas.core.events import Belief
    from atlas.intelligence.planning import compare_routes

    route = system.routing.evacuation.plan_evacuation(2151514485)
    incident = {
        "top_priority": "area",
        "plans": {
            "a4_evacuation": route.model_dump(mode="json"),
            "a5_triage": {"ranked_needs": [{"area_id": "area", "estimated_vulnerable": 12}]},
        },
    }
    version = system.runtime.cop.version
    result = await compare_routes(incident, system.runtime, group_size=20)
    assert result["status"] == "shadow"
    assert result["result"]["input_state"]["people"] == 12
    assert system.runtime.cop.version == version
    system.runtime.cop.put(
        Belief(subject="test", attribute="open", value=True, confidence=1, updated_at=clock.now)
    )
    assert (await compare_routes(incident, system.runtime, group_size=20))["status"] == "stale"


@pytest.mark.parametrize("raw", [None, [], {"claims": "not a list"}])
async def test_malformed_model_output_returns_grounded_fallback(context, raw):
    result = await Advisor(FakeProvider(raw), context, context).advise({"incident_id": "i"})
    assert result.source == "template" and result.claims[0].accepted


def test_api_rejects_whitespace_only_input():
    with TestClient(create_app(Settings(database_path=":memory:"))) as client:
        assert client.post("/intelligence/ask", json={"question": "   "}).status_code == 422
        assert (
            client.post("/intelligence/extract", json={"node_id": "n", "text": "  "}).status_code
            == 422
        )
