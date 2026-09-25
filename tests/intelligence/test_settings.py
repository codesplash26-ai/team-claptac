import json
import os
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from atlas.api.main import create_app
from atlas.core.config import Settings
from atlas.intelligence.providers import OpenAIProvider
from atlas.intelligence.settings import SettingsStore, SettingsUpdate
from atlas.tools.llm import LLMClient, LLMResult, LLMSettings, ToolSpec


@pytest.fixture(autouse=True)
def no_external_keys(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    monkeypatch.setenv("LLM_PROVIDER", "none")
    for name in ("LLM_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client():
    with TestClient(create_app(Settings(database_path=":memory:"))) as c:
        yield c


def update(**kw):
    return SettingsUpdate(provider="openai", model="test-model", **kw)


def test_saved_keys_persist_privately_and_are_never_public(tmp_path):
    path = tmp_path / "local/settings.json"
    store = SettingsStore(path=path)
    response = store.save(update(api_key="secret-first", reasoning_effort="high"))
    assert response["has_api_key"]
    assert "secret-first" not in json.dumps(response)
    assert os.stat(path).st_mode & 0o777 == 0o600
    loaded = SettingsStore(path=path)
    assert loaded.active == "openai" and loaded.key_for("openai") == ("secret-first", "saved")
    assert loaded.profiles["openai"].reasoning_effort == "high"
    store.save(update(reasoning_effort="low"))
    assert store.key_for("openai")[0] == "secret-first"
    store.save(update(api_key="secret-second"))
    assert "secret-first" not in path.read_text()
    store.save(update(clear_api_key=True))
    assert not store.public()["has_api_key"] and store.build_provider() is None


def test_keys_never_cross_providers_and_removal_suppresses_environment(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "generic-openai-only")
    store = SettingsStore(persistent=False)
    assert store.key_for("anthropic")[0] == ""
    assert store.key_for("openai")[0] == "generic-openai-only"
    store.save(SettingsUpdate(provider="anthropic", model="test-claude", api_key="claude-key"))
    assert store.key_for("openai")[0] == "generic-openai-only"
    store.save(update(clear_api_key=True))
    assert store.key_for("openai") == ("", "removed")
    assert store.key_for("anthropic")[0] == "claude-key"


@pytest.mark.parametrize(
    "params",
    [
        {
            "provider": "anthropic",
            "model": "test",
            "thinking_mode": "enabled",
            "thinking_budget": 4096,
            "max_output_tokens": 4096,
        },
        {"provider": "openai", "model": "test", "thinking_mode": "enabled"},
        {"provider": "anthropic", "model": "test", "reasoning_effort": "xhigh"},
        {"provider": "openai", "model": ""},
        {"provider": "openai", "model": "test", "api_key": "private-key", "clear_api_key": True},
    ],
)
def test_invalid_settings_are_rejected(params):
    with pytest.raises(ValidationError):
        SettingsUpdate(**params)


def test_saving_applies_immediately_without_touching_env_or_leaking_secrets(client):
    response = client.put(
        "/intelligence/settings",
        json={
            "provider": "openai",
            "model": "test-model",
            "api_key": "private-key",
            "reasoning_effort": "high",
            "timeout_seconds": 90,
        },
    )
    assert response.status_code == 200
    service = client.app.state.system.intelligence
    assert service.provider.model == "test-model"
    assert service.provider.reasoning_effort == "high" and service.provider.api_mode == "responses"
    assert service.request_timeout == 90
    assert os.getenv("LLM_PROVIDER") == "none"
    assert "private-key" not in response.text
    assert "private-key" not in client.get("/intelligence/settings").text
    assert "private-key" not in json.dumps(service.runtime.ledger.records())
    assert client.put("/intelligence/settings", json={"provider": "none"}).status_code == 200
    assert service.provider is None


def test_validation_errors_do_not_echo_credentials(client):
    response = client.put(
        "/intelligence/settings",
        json={
            "provider": "anthropic",
            "model": "test",
            "api_key": "NEVER-ECHO-THIS",
            "thinking_mode": "enabled",
            "thinking_budget": 4096,
            "max_output_tokens": 4096,
        },
    )
    assert response.status_code == 422
    assert "NEVER-ECHO-THIS" not in response.text
    assert "input" not in response.json()["detail"][0]


def test_only_admin_can_change_or_test_settings(client, monkeypatch):
    monkeypatch.setenv(
        "ATLAS_TOKENS",
        json.dumps(
            [
                {"token": "admin", "name": "a", "role": "admin"},
                {"token": "viewer", "name": "v", "role": "viewer"},
                {"token": "lead", "name": "l", "role": "ai_lead"},
            ]
        ),
    )
    for endpoint, method in (
        ("/intelligence/settings", client.put),
        ("/intelligence/settings/test", client.post),
    ):
        assert method(endpoint, json={"provider": "none"}).status_code == 401
        for t in ("viewer", "lead"):
            assert (
                method(
                    endpoint, json={"provider": "none"}, headers={"Authorization": f"Bearer {t}"}
                ).status_code
                == 403
            )
        assert (
            method(
                endpoint, json={"provider": "none"}, headers={"Authorization": "Bearer admin"}
            ).status_code
            == 200
        )


def test_connection_test_uses_draft_and_never_saves_or_sends_incident_data(client, monkeypatch):
    seen = []

    class Fake:
        async def complete(self, **kwargs):
            seen.append(kwargs)
            return LLMResult("OK", tokens_in=2, tokens_out=1)

    service = client.app.state.system.intelligence
    monkeypatch.setattr(service.settings, "build_provider", lambda update: Fake())
    result = client.post(
        "/intelligence/settings/test",
        json={"provider": "openai", "model": "draft", "api_key": "secret-draft"},
    )
    assert result.status_code == 200 and result.json()["ok"]
    assert service.settings.active == "none" and not service.settings.keys
    assert seen[0]["messages"] == [{"role": "user", "content": "Reply with OK."}]
    assert "secret-draft" not in json.dumps(service.runtime.ledger.records())


def test_connection_failure_is_safe_and_does_not_claim_success(client, monkeypatch):
    class Fake:
        async def complete(self, **kwargs):
            raise RuntimeError("oops secret-provider-key")

    monkeypatch.setattr(
        client.app.state.system.intelligence.settings, "build_provider", lambda update: Fake()
    )
    response = client.post(
        "/intelligence/settings/test", json={"provider": "openai", "model": "test"}
    )
    assert response.status_code == 200 and not response.json()["ok"]
    assert "secret-provider-key" not in response.text


@pytest.mark.parametrize(
    "mode, effort",
    [("enabled", "default"), ("adaptive", "high"), ("disabled", "default"), ("default", "default")],
)
async def test_anthropic_payload_has_selected_thinking(mode, effort):
    seen = []

    async def create(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="OK")])

    llm = LLMClient(
        LLMSettings(
            llm_api_key="secret",
            llm_model="test",
            thinking_mode=mode,
            thinking_budget=2048,
            reasoning_effort=effort,
            llm_max_tokens=8192,
        )
    )
    llm._client = SimpleNamespace(messages=SimpleNamespace(create=create))
    await llm.complete(system="s", messages=[])
    assert seen[0]["max_tokens"] == 8192
    if mode == "default":
        assert "thinking" not in seen[0]
    else:
        assert seen[0]["thinking"]["type"] == mode
    if mode == "enabled":
        assert seen[0]["thinking"]["budget_tokens"] == 2048
    if effort != "default":
        assert seen[0]["output_config"] == {"effort": effort}
    assert "secret" not in repr(llm.settings)


async def test_openai_responses_reasoning_and_tool_continuation():
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url.path == "/v1/responses"
        if len(requests) == 1:
            output = [
                {"type": "reasoning", "id": "r", "encrypted_content": "opaque", "summary": []},
                {"type": "function_call", "call_id": "c1", "name": "read", "arguments": "{}"},
            ]
        else:
            assert body["input"][-1]["type"] == "function_call_output"
            assert any(item.get("encrypted_content") == "opaque" for item in body["input"])
            output = [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": output,
                "usage": {"input_tokens": 3, "output_tokens": 4},
            },
        )

    provider = OpenAIProvider(
        "secret",
        "test",
        api_mode="responses",
        reasoning_effort="high",
        transport=httpx.MockTransport(respond),
        settings=LLMSettings(llm_max_tokens=8192),
    )
    result = await provider.complete(
        system="s",
        messages=[{"role": "user", "content": "q"}],
        tools=[
            ToolSpec("read", "Read only", {"type": "object", "properties": {}}, lambda: {"fact": 1})
        ],
    )
    assert result.text == "done" and result.tokens_in == 6 and result.tokens_out == 8
    assert all(
        r["reasoning"] == {"effort": "high"} and r["max_output_tokens"] == 8192 and not r["store"]
        for r in requests
    )
    assert "opaque" not in str(result.tool_calls) and result.raw is None


def test_settings_ui_receives_actual_permissions_in_dev_and_legacy_modes(client, monkeypatch):
    me = client.get("/am/v1/me").json()
    assert me["dev_mode"] and "llm:configure" in me["permissions"]
    monkeypatch.setenv("ATLAS_OPERATOR_TOKEN", "legacy-admin")
    assert "llm:configure" not in client.get("/am/v1/me").json()["permissions"]
    me = client.get("/am/v1/me", headers={"X-Operator-Token": "legacy-admin"}).json()
    assert "llm:configure" in me["permissions"] and not me["dev_mode"]
    assert client.get("/am/v1/me", headers={"X-Operator-Token": "bad"}).status_code == 401
