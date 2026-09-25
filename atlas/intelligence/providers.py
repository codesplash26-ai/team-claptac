"""LLM providers behind one interface.  (Owner: Aeshan)

Anthropic is the default: ``atlas/tools/llm.py`` already wraps it (retries, rate limiting, prompt log,
tool loop). OpenAI is an optional fallback, added here rather than in that file so nothing else
changes. Choose with ``LLM_PROVIDER`` (``anthropic`` | ``openai``); keys come from the environment
(``LLM_API_KEY`` / ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``), never from the repository.

Do not use two providers at once for the same request: the fallback is for when the first is down.
"""

from __future__ import annotations

from typing import Any, Protocol

from atlas.tools.llm import LLMResult, ToolSpec


class LLMProvider(Protocol):
    name: str

    @property
    def available(self) -> bool:
        """False when there is no key or the provider is known to be down."""

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec] | None = None,
    ) -> LLMResult: ...


class AnthropicProvider:
    """Adapter over ``atlas.tools.llm.LLMClient``."""

    name = "anthropic"

    def __init__(self, client=None) -> None:
        raise NotImplementedError("Aeshan: adapt LLMClient to LLMProvider")


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        raise NotImplementedError("Aeshan: optional OpenAI adapter (same tool-loop contract)")


def provider_from_env() -> LLMProvider | None:
    """The configured provider, or None when no key is set (callers then use templates)."""
    raise NotImplementedError("Aeshan: choose provider from LLM_PROVIDER and available keys")
