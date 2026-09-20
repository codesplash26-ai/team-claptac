"""Validated settings; demo thresholds are configuration, not operational advice."""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from atlas.reasoning import PlannerConfig

ROOT = Path(__file__).resolve().parents[2]


def planner_config_for(agent: str, path=None) -> PlannerConfig:
    """Resolve shared defaults and a teammate's per-agent search overrides."""
    data = yaml.safe_load(Path(path or ROOT / "config/reasoning.yaml").read_text()) or {}
    override = data.get("agents", {}).get(agent, {})
    return PlannerConfig(
        **{**data.get("defaults", {}), **{k: v for k, v in override.items() if k != "enabled"}}
    )


class SentinelConfig(BaseModel):
    freshness_seconds: int = Field(default=300, gt=0)
    future_tolerance_seconds: int = Field(default=30, ge=0)
    window_seconds: int = Field(default=300, gt=0)
    water_threshold_cm: float = Field(default=25, gt=0)
    rise_threshold_cm_min: float = Field(default=5, gt=0)
    rainfall_threshold_mm: float = Field(default=20, gt=0)
    soil_threshold_pct: float = Field(default=80, gt=0, le=100)
    minimum_confidence: float = Field(default=0.65, ge=0, le=1)
    corroboration_minimum: float = Field(default=0.4, ge=0, le=1)
    rearm_ratio: float = Field(default=0.8, gt=0, lt=1)
    cooldown_seconds: int = Field(default=60, ge=0)
    escalation_delta: float = Field(default=0.5, gt=0)
    conflict_water_cm: float = Field(default=8, gt=0)
    source_reliability: dict[str, float] = Field(default_factory=dict)
    reasoning_enabled: bool = False
    verification_success_probability: float = Field(default=0.7, ge=0, le=1)


class Settings(BaseModel):
    database_path: str = str(ROOT / "data" / "atlas.db")
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = Field(default=1883, gt=0, le=65535)
    mqtt_topic: str = "atlas/sensors/#"
    sentinel: SentinelConfig = Field(default_factory=SentinelConfig)
    reasoning: PlannerConfig = Field(default_factory=PlannerConfig)

    @classmethod
    def load(cls):
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
        sentinel = yaml.safe_load((ROOT / "config/sentinel.yaml").read_text()) or {}
        reasoning = yaml.safe_load((ROOT / "config/reasoning.yaml").read_text()) or {}
        path = os.getenv("DATABASE_URL", "sqlite:///" + str(ROOT / "data/atlas.db"))
        if not path.startswith("sqlite:///"):
            raise ValueError("This runtime supports sqlite:/// DATABASE_URL values")
        defaults = reasoning.get("defaults", {})
        override = reasoning.get("agents", {}).get("a1_sentinel", {})
        sentinel["reasoning_enabled"] = override.get("enabled", False)
        return cls(
            database_path=path.removeprefix("sqlite:///"),
            mqtt_enabled=os.getenv("MQTT_ENABLED", "false").lower() == "true",
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=int(os.getenv("MQTT_PORT", "1883")),
            mqtt_topic=os.getenv("MQTT_TOPIC", "atlas/sensors/#"),
            sentinel=SentinelConfig(**sentinel),
            reasoning=PlannerConfig(
                **{**defaults, **{k: v for k, v in override.items() if k != "enabled"}}
            ),
        )
