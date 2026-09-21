"""LLM client wrapper with tool-calling, retries, rate limiting, and prompt logging.

Used by A3 Incident Commander for orchestration and text generation only.
Deterministic scoring, routing and allocation stay in specialist tools.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from atlas.core.config import Settings, get_settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Base LLM failure."""


class LLMUnavailable(LLMError):
    """Provider down, rate-limited beyond retries, or no API key."""


class LLMRateLimited(LLMError):
    """Transient rate-limit; may be retried."""


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[Any] | Any]


@dataclass
class PromptLogEntry:
    timestamp: datetime
    model: str
    system: str
    messages: list[dict[str, Any]]
    tools: list[str]
    response_text: str | None
    tool_calls: list[dict[str, Any]]
    latency_ms: float
    error: str | None = None


@dataclass
class LLMResult:
    text: str | None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: Any = None
    used_fallback: bool = False


class RateLimiter:
    """Token-bucket style limiter: at most N requests per rolling minute."""

    def __init__(self, requests_per_minute: int) -> None:
        self.requests_per_minute = max(1, requests_per_minute)
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - 60.0
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.requests_per_minute:
                wait = 60.0 - (now - self._timestamps[0]) + 0.01
                await asyncio.sleep(max(wait, 0.01))
                return await self.acquire()
            self._timestamps.append(time.monotonic())


class LLMClient:
    """Anthropic Messages API wrapper with tool loops, retries and logging.

    Inputs: system prompt, messages, optional ToolSpec list.
    Outputs: LLMResult with final assistant text and any tool-call traces.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._limiter = RateLimiter(self.settings.llm_requests_per_minute)
        self.prompt_log: list[PromptLogEntry] = []
        self._client: Any = None

    @property
    def available(self) -> bool:
        return bool(self.settings.llm_api_key)

    def _get_client(self) -> Any:
        if not self.available:
            raise LLMUnavailable("LLM_API_KEY / ANTHROPIC_API_KEY is not set")
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise LLMUnavailable("anthropic package is not installed") from exc
            self._client = anthropic.AsyncAnthropic(api_key=self.settings.llm_api_key)
        return self._client

    def _tools_for_api(self, tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters
                if t.parameters.get("type") == "object"
                else {
                    "type": "object",
                    "properties": t.parameters,
                    "required": list(t.parameters.keys()),
                },
            }
            for t in tools
        ]

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None = None,
        max_tool_rounds: int = 6,
    ) -> LLMResult:
        """Run a chat completion, executing tool calls until the model stops or max rounds."""
        tools = tools or []
        tool_map = {t.name: t for t in tools}
        working_messages = list(messages)
        all_tool_calls: list[dict[str, Any]] = []

        for _round in range(max_tool_rounds + 1):
            result = await self._call_with_retries(
                system=system,
                messages=working_messages,
                tools=tools,
            )
            if not result.tool_calls:
                return LLMResult(
                    text=result.text,
                    tool_calls=all_tool_calls,
                    raw=result.raw,
                )

            # Append assistant turn + tool results for the next round.
            assistant_content = result.raw.content if result.raw is not None else []
            working_messages.append({"role": "assistant", "content": assistant_content})
            tool_results: list[dict[str, Any]] = []
            for call in result.tool_calls:
                all_tool_calls.append(call)
                name = call["name"]
                tool_input = call.get("input") or {}
                tool = tool_map.get(name)
                if tool is None:
                    payload: Any = {"error": f"unknown tool: {name}"}
                else:
                    try:
                        maybe = tool.handler(**tool_input)
                        payload = await maybe if asyncio.iscoroutine(maybe) else maybe
                    except Exception as exc:  # noqa: BLE001 — surface tool errors to the model
                        payload = {"error": str(exc)}
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call["id"],
                        "content": _json_dumps(payload),
                    }
                )
            working_messages.append({"role": "user", "content": tool_results})

        return LLMResult(
            text=None,
            tool_calls=all_tool_calls,
            used_fallback=False,
        )

    async def _call_with_retries(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
    ) -> LLMResult:
        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_retries + 1):
            started = time.perf_counter()
            try:
                await self._limiter.acquire()
                return await self._once(system=system, messages=messages, tools=tools, started=started)
            except LLMRateLimited as exc:
                last_error = exc
                await asyncio.sleep(2**attempt)
            except LLMUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                log.warning("LLM call failed (attempt %s): %s", attempt + 1, exc)
                await asyncio.sleep(2**attempt)

        raise LLMUnavailable(f"LLM failed after retries: {last_error}") from last_error

    async def _once(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        started: float,
    ) -> LLMResult:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.settings.llm_model,
            "max_tokens": 2048,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = self._tools_for_api(tools)

        error_msg: str | None = None
        try:
            response = await asyncio.wait_for(
                client.messages.create(**kwargs),
                timeout=self.settings.llm_timeout_seconds,
            )
        except TimeoutError as exc:
            error_msg = "timeout"
            self._log_prompt(system, messages, tools, None, [], started, error_msg)
            raise LLMUnavailable("LLM request timed out") from exc
        except Exception as exc:
            error_msg = str(exc)
            self._log_prompt(system, messages, tools, None, [], started, error_msg)
            name = type(exc).__name__
            if "RateLimit" in name or "429" in error_msg:
                raise LLMRateLimited(error_msg) from exc
            if "Authentication" in name or "api_key" in error_msg.lower():
                raise LLMUnavailable(error_msg) from exc
            raise

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in response.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "input": dict(block.input) if block.input else {},
                    }
                )

        text = "\n".join(text_parts) if text_parts else None
        self._log_prompt(system, messages, tools, text, tool_calls, started, None)
        return LLMResult(text=text, tool_calls=tool_calls, raw=response)

    def _log_prompt(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        response_text: str | None,
        tool_calls: list[dict[str, Any]],
        started: float,
        error: str | None,
    ) -> None:
        entry = PromptLogEntry(
            timestamp=datetime.now(timezone.utc),
            model=self.settings.llm_model,
            system=system,
            messages=_safe_messages(messages),
            tools=[t.name for t in tools],
            response_text=response_text,
            tool_calls=tool_calls,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=error,
        )
        self.prompt_log.append(entry)
        log.info(
            "llm call model=%s tools=%s latency_ms=%.0f error=%s",
            entry.model,
            entry.tools,
            entry.latency_ms,
            entry.error,
        )


def _json_dumps(payload: Any) -> str:
    try:
        return json.dumps(payload, default=str)
    except TypeError:
        return json.dumps({"repr": repr(payload)})


def _safe_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy messages into JSON-safe form for the prompt log."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": msg.get("role"), "content": content})
        else:
            out.append({"role": msg.get("role"), "content": _json_dumps(content)})
    return out
