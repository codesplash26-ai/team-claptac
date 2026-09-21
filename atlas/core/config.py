"""Settings loaded from environment / .env."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")


class Settings(BaseModel):
    atlas_env: str = Field(default="dev")
    atlas_scope: str = Field(default="monitor and coordinate response for Ratnapura district")

    llm_api_key: str | None = None
    llm_model: str = "claude-sonnet-4-20250514"
    llm_max_retries: int = 3
    llm_requests_per_minute: int = 30
    llm_timeout_seconds: float = 60.0

    database_url: str = "sqlite:///./data/atlas.db"
    anomaly_threshold: float = 0.7

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_topic: str = "atlas/sensors/#"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        atlas_env=_env("ATLAS_ENV", "dev") or "dev",
        atlas_scope=_env("ATLAS_SCOPE", "monitor and coordinate response for Ratnapura district")
        or "monitor and coordinate response for Ratnapura district",
        llm_api_key=_env("LLM_API_KEY") or _env("ANTHROPIC_API_KEY"),
        llm_model=_env("LLM_MODEL", "claude-sonnet-4-20250514") or "claude-sonnet-4-20250514",
        llm_max_retries=int(_env("LLM_MAX_RETRIES", "3") or "3"),
        llm_requests_per_minute=int(_env("LLM_REQUESTS_PER_MINUTE", "30") or "30"),
        llm_timeout_seconds=float(_env("LLM_TIMEOUT_SECONDS", "60") or "60"),
        database_url=_env("DATABASE_URL", "sqlite:///./data/atlas.db") or "sqlite:///./data/atlas.db",
        anomaly_threshold=float(_env("ANOMALY_THRESHOLD", "0.7") or "0.7"),
        mqtt_host=_env("MQTT_HOST", "localhost") or "localhost",
        mqtt_port=int(_env("MQTT_PORT", "1883") or "1883"),
        mqtt_topic=_env("MQTT_TOPIC", "atlas/sensors/#") or "atlas/sensors/#",
    )
