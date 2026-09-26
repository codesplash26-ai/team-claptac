"""Bounded provider adapters. Missing credentials leave the offline path available."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Protocol

import httpx

from atlas.tools.llm import LLMClient, LLMResult, LLMSettings, LLMUnavailable, RateLimiter, ToolSpec


class LLMProvider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    async def complete(
        self, *, system: str, messages: list[dict[str, Any]], tools: list[ToolSpec] | None = None
    ) -> LLMResult: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client=None):
        self.client = client or LLMClient()
        self.model = self.client.settings.llm_model

    @property
    def available(self):
        return self.client.available

    async def complete(self, **kwargs):
        return await self.client.complete(**kwargs)


class OpenAIProvider:
    """Chat-completions adapter with a bounded read-only tool loop; no extra SDK needed."""

    name = "openai"

    def __init__(
        self,
        api_key=None,
        model=None,
        *,
        transport=None,
        settings=None,
        reasoning_effort="default",
        api_mode="chat_completions",
    ):
        self.api_key = (
            api_key
            if api_key is not None
            else os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
        )
        self.model = model or os.getenv("LLM_MODEL") or "gpt-4.1-mini"
        self.settings = settings or LLMSettings.from_env()
        self.reasoning_effort = reasoning_effort
        self.api_mode = api_mode
        self.limiter = RateLimiter(self.settings.llm_requests_per_minute)
        self.transport = transport

    @property
    def available(self):
        return bool(self.api_key)

    async def complete(self, *, system, messages, tools=None):
        if not self.available:
            raise LLMUnavailable("OpenAI API key is not configured")
        if self.api_mode == "responses":
            return await self._responses(system=system, messages=messages, tools=tools)
        specs = {tool.name: tool for tool in tools or []}
        history = [{"role": "system", "content": system}, *messages]
        calls, tokens_in, tokens_out = [], 0, 0
        async with httpx.AsyncClient(
            timeout=self.settings.llm_timeout_seconds, transport=self.transport
        ) as client:
            for _ in range(7):
                payload = {
                    "model": self.model,
                    "messages": history,
                    "max_completion_tokens": self.settings.llm_max_tokens,
                }
                if self.reasoning_effort != "default":
                    payload["reasoning_effort"] = self.reasoning_effort
                if specs:
                    payload["tools"] = [
                        {
                            "type": "function",
                            "function": {
                                "name": t.name,
                                "description": t.description,
                                "parameters": t.parameters,
                            },
                        }
                        for t in specs.values()
                    ]
                data = await self._request(client, payload)
                usage = data.get("usage", {})
                tokens_in += usage.get("prompt_tokens", 0)
                tokens_out += usage.get("completion_tokens", 0)
                message = data["choices"][0]["message"]
                if not message.get("tool_calls"):
                    return LLMResult(
                        message.get("content"),
                        calls,
                        data,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                    )
                history.append(message)
                for call in message["tool_calls"]:
                    name = call["function"]["name"]
                    try:
                        args = json.loads(call["function"]["arguments"])
                        if name not in specs:
                            raise ValueError("Unknown read-only tool")
                        result = specs[name].handler(**args)
                        if asyncio.iscoroutine(result):
                            result = await result
                    except Exception:
                        result = {"error": "Tool unavailable or arguments invalid"}
                    calls.append({"id": call["id"], "name": name})
                    history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(result, default=str),
                        }
                    )
        raise LLMUnavailable("Provider exceeded the tool-round budget")

    async def _responses(self, *, system, messages, tools):
        specs = {tool.name: tool for tool in tools or []}
        history = list(messages)
        calls, tokens_in, tokens_out = [], 0, 0
        async with httpx.AsyncClient(
            timeout=self.settings.llm_timeout_seconds, transport=self.transport
        ) as client:
            for _ in range(7):
                payload = {
                    "model": self.model,
                    "instructions": system,
                    "input": history,
                    "max_output_tokens": self.settings.llm_max_tokens,
                    "store": False,
                    "include": ["reasoning.encrypted_content"],
                }
                if self.reasoning_effort != "default":
                    payload["reasoning"] = {"effort": self.reasoning_effort}
                if specs:
                    payload["tools"] = [
                        {
                            "type": "function",
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in specs.values()
                    ]
                data = await self._request(client, payload, endpoint="responses")
                usage = data.get("usage") or {}
                tokens_in += usage.get("input_tokens", 0)
                tokens_out += usage.get("output_tokens", 0)
                if data.get("status") in {"incomplete", "failed"}:
                    raise LLMUnavailable(
                        "Response incomplete; increase output token limit or lower reasoning effort"
                    )
                output = data.get("output", [])
                requested = [item for item in output if item.get("type") == "function_call"]
                if not requested:
                    text = "\n".join(
                        block["text"]
                        for item in output
                        if item.get("type") == "message"
                        for block in item.get("content", [])
                        if block.get("type") == "output_text"
                    )
                    return LLMResult(text, calls, tokens_in=tokens_in, tokens_out=tokens_out)
                # Preserve opaque reasoning items between tool calls, never expose them in traces.
                history.extend(output)
                for call in requested:
                    try:
                        spec = specs[call["name"]]
                        result = spec.handler(**json.loads(call["arguments"]))
                        if asyncio.iscoroutine(result):
                            result = await result
                    except Exception:
                        result = {"error": "Tool unavailable or arguments invalid"}
                    calls.append({"id": call["call_id"], "name": call["name"]})
                    history.append(
                        {
                            "type": "function_call_output",
                            "call_id": call["call_id"],
                            "output": json.dumps(result, default=str),
                        }
                    )
        raise LLMUnavailable("Provider exceeded the tool-round budget")

    async def _request(self, client, payload, endpoint="chat/completions"):
        for attempt in range(self.settings.llm_max_retries + 1):
            await self.limiter.acquire()
            try:
                response = await client.post(
                    "https://api.openai.com/v1/" + endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                elif response.is_error:
                    raise LLMUnavailable(f"Provider refused request ({response.status_code})")
                return response.json()
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt >= self.settings.llm_max_retries:
                    raise LLMUnavailable("Provider unavailable after retries") from None
                await asyncio.sleep(min(2**attempt, 4))


def provider_from_env() -> LLMProvider | None:
    from dotenv import load_dotenv

    load_dotenv()
    name = os.getenv("LLM_PROVIDER", "anthropic").lower()
    if name in {"none", "offline"}:
        return None
    if name not in {"anthropic", "openai"}:
        raise ValueError("LLM_PROVIDER must be anthropic, openai, or none")
    provider = OpenAIProvider() if name == "openai" else AnthropicProvider()
    return provider if provider.available else None
