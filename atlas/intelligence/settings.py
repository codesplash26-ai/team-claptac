"""Local LLM profiles and credentials; keys never enter the ledger or public settings response."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from atlas.intelligence.providers import AnthropicProvider, OpenAIProvider
from atlas.tools.llm import LLMClient, LLMSettings

ProviderName = Literal["none", "openai", "anthropic"]
Effort = Literal["default", "none", "minimal", "low", "medium", "high", "xhigh", "max"]


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    model: str = Field(default="", max_length=150, pattern=r"^[a-zA-Z0-9._:/-]*$")
    reasoning_effort: Effort = "default"
    thinking_mode: Literal["default", "disabled", "enabled", "adaptive"] = "default"
    thinking_budget: int = Field(default=1024, ge=1024, le=64000)
    max_output_tokens: int = Field(default=4096, ge=256, le=128000)
    timeout_seconds: int = Field(default=60, ge=5, le=180)
    requests_per_minute: int = Field(default=30, ge=1, le=120)


class SettingsUpdate(ModelProfile):
    provider: ProviderName
    api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False

    @model_validator(mode="after")
    def compatible(self):
        if self.provider != "none" and not self.model:
            raise ValueError("Enter a model id for the selected provider")
        if self.api_key is not None:
            key = self.api_key.get_secret_value()
            if not key.strip() or any(c.isspace() for c in key):
                raise ValueError("API key must not be blank or contain whitespace")
            if self.clear_api_key:
                raise ValueError("Choose either replace key or remove key")
            if self.provider == "none":
                raise ValueError("Select a provider before setting an API key")
        if self.provider == "openai" and self.thinking_mode != "default":
            raise ValueError("OpenAI uses reasoning effort; leave Anthropic thinking at default")
        if self.provider == "anthropic":
            if self.reasoning_effort not in {"default", "low", "medium", "high", "max"}:
                raise ValueError("Choose default, low, medium, high or max effort for Anthropic")
            if self.thinking_mode == "enabled" and self.thinking_budget >= self.max_output_tokens:
                raise ValueError("Thinking budget must be smaller than the output token limit")
        return self


class SettingsStore:
    def __init__(self, *, persistent=True, path=None):
        from dotenv import load_dotenv

        load_dotenv()
        self.path = (
            Path(
                path
                or os.getenv("ATLAS_LLM_SETTINGS_PATH")
                or Path(__file__).resolve().parents[2] / ".local/llm-settings.json"
            )
            if persistent
            else None
        )
        active = os.getenv("LLM_PROVIDER", "anthropic").lower()
        if active == "offline":
            active = "none"
        if active not in {"none", "anthropic", "openai"}:
            raise ValueError("Unknown LLM_PROVIDER")
        self.active = active
        self.profiles = {
            "none": ModelProfile(),
            "openai": ModelProfile(model="gpt-4.1-mini"),
            "anthropic": ModelProfile(model="claude-sonnet-5"),
        }
        if active != "none":
            profile = self.profiles[active]
            self.profiles[active] = ModelProfile(
                model=os.getenv("LLM_MODEL") or profile.model,
                timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
                requests_per_minute=int(os.getenv("LLM_REQUESTS_PER_MINUTE", "30")),
            )
        self.keys = {}
        if self.path and self.path.exists():
            try:
                state = json.loads(self.path.read_text())
                if state["provider"] not in self.profiles:
                    raise ValueError()
                self.active = state["provider"]
                self.profiles.update(
                    {
                        k: ModelProfile.model_validate(v)
                        for k, v in state["profiles"].items()
                        if k in self.profiles
                    }
                )
                self.keys = {
                    k: str(v)
                    for k, v in state.get("keys", {}).items()
                    if k in {"openai", "anthropic"}
                }
            except (ValueError, KeyError, TypeError):
                raise ValueError(
                    "Saved LLM settings are invalid; repair the local settings file"
                ) from None

    def key_for(self, provider):
        if provider == "none":
            return "", "missing"
        if provider in self.keys:
            return self.keys[provider], "saved" if self.keys[provider] else "removed"
        key = os.getenv("OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY")
        # A generic env key belongs only to its configured provider, never silently to another.
        if not key and provider == os.getenv("LLM_PROVIDER", "anthropic").lower():
            key = os.getenv("LLM_API_KEY")
        return key or "", "environment" if key else "missing"

    def public(self):
        key, source = self.key_for(self.active)
        return {
            "provider": self.active,
            **self.profiles[self.active].model_dump(),
            "has_api_key": bool(key),
            "key_source": source,
            "storage": "local_file" if self.path else "memory",
            "profiles": {
                name: {
                    **p.model_dump(),
                    "has_api_key": bool(self.key_for(name)[0]),
                    "key_source": self.key_for(name)[1],
                }
                for name, p in self.profiles.items()
            },
        }

    def candidate(self, update):
        keys = dict(self.keys)
        if update.clear_api_key and update.provider != "none":
            keys[update.provider] = ""
        elif update.api_key is not None:
            keys[update.provider] = update.api_key.get_secret_value()
        profile = ModelProfile.model_validate(
            update.model_dump(exclude={"provider", "api_key", "clear_api_key"})
        )
        key = keys.get(update.provider, self.key_for(update.provider)[0])
        return profile, keys, key

    def build_provider(self, update=None):
        if update is None:
            name, profile = self.active, self.profiles[self.active]
            key = self.key_for(name)[0]
        else:
            name = update.provider
            profile, _, key = self.candidate(update)
        if name == "none" or not key:
            return None
        settings = LLMSettings(
            llm_api_key=key,
            llm_model=profile.model,
            llm_timeout_seconds=profile.timeout_seconds,
            llm_max_tokens=profile.max_output_tokens,
            llm_requests_per_minute=profile.requests_per_minute,
            thinking_mode=profile.thinking_mode,
            thinking_budget=profile.thinking_budget,
            reasoning_effort=profile.reasoning_effort,
        )
        if name == "anthropic":
            return AnthropicProvider(LLMClient(settings))
        return OpenAIProvider(
            key,
            profile.model,
            settings=settings,
            reasoning_effort=profile.reasoning_effort,
            api_mode="responses",
        )

    def save(self, update):
        profile, keys, _ = self.candidate(update)
        profiles = {**self.profiles, update.provider: profile}
        state = {
            "provider": update.provider,
            "profiles": {k: p.model_dump() for k, p in profiles.items()},
            "keys": keys,
        }
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, tmp = tempfile.mkstemp(prefix=".llm-", dir=self.path.parent)
            try:
                if hasattr(os, "fchmod"):  # POSIX; on Windows the file inherits the user's profile ACL
                    os.fchmod(fd, 0o600)
                with os.fdopen(fd, "w") as stream:
                    json.dump(state, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        self.active, self.profiles, self.keys = update.provider, profiles, keys
        return self.public()
