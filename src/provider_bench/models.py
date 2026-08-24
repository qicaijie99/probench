from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DISABLED = "DISABLED"


class ProviderConfig(BaseModel):
    name: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    api_key: SecretStr
    model: str = Field(min_length=1)
    timeout_seconds: float = Field(default=120.0, gt=0)
    stream_include_usage: bool = False
    max_connections: int = Field(default=256, gt=0)
    max_keepalive_connections: int = Field(default=128, gt=0)
    headers: dict[str, SecretStr] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    def safe_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data["api_key"] = "**********"
        data["headers"] = {name: "**********" for name in self.headers}
        return data

    @model_validator(mode="after")
    def validate_connections(self) -> ProviderConfig:
        if self.max_keepalive_connections > self.max_connections:
            raise ValueError("max_keepalive_connections cannot exceed max_connections")
        return self


class HardGate(BaseModel):
    metric: str
    operator: str = Field(pattern=r"^(>=|<=|>|<|==)$")
    value: float
    severity: str = Field(default="fail", pattern=r"^(warn|fail)$")


class ScoringConfig(BaseModel):
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "quality": 30.0,
            "latency": 15.0,
            "throughput": 10.0,
            "concurrency": 15.0,
            "reliability": 10.0,
            "compatibility": 4.0,
            "protocol": 4.0,
            "structured_output": 4.0,
            "tool_calling": 3.0,
            "tool_choice": 2.0,
            "features_thinking": 3.0,
            "features_param": 2.0,
            "cache": 5.0,
            "model_identity": 3.0,
            "billing": 4.0,
            "cost": 3.0,
        }
    )
    gates: list[HardGate] = Field(default_factory=list)
    latency_ttft_good_ms: float = Field(default=1000, gt=0)
    latency_ttft_fail_ms: float = Field(default=10000, gt=0)
    output_tps_target: float = Field(default=20, gt=0)
    warn_score_below: float = Field(default=80, ge=0, le=100)
    fail_score_below: float = Field(default=60, ge=0, le=100)

    @model_validator(mode="after")
    def validate_targets(self) -> ScoringConfig:
        if self.latency_ttft_fail_ms <= self.latency_ttft_good_ms:
            raise ValueError("latency_ttft_fail_ms must exceed latency_ttft_good_ms")
        if self.fail_score_below > self.warn_score_below:
            raise ValueError("fail_score_below cannot exceed warn_score_below")
        return self


class AppConfig(BaseModel):
    provider: ProviderConfig | None = None
    providers: list[ProviderConfig] = Field(default_factory=list)
    judge_provider: ProviderConfig | None = None
    reference_provider: str | None = None
    benchmarks: dict[str, dict[str, Any]] = Field(default_factory=dict)
    output_dir: Path = Path("outputs")
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_providers(self) -> AppConfig:
        if self.provider is None and not self.providers:
            raise ValueError("configure 'provider' or at least one entry in 'providers'")
        names = [provider.name for provider in self.selected_providers]
        if len(names) != len(set(names)):
            raise ValueError("provider names must be unique")
        if self.reference_provider is not None and self.reference_provider not in names:
            raise ValueError("reference_provider must name a configured provider")
        return self

    @property
    def selected_providers(self) -> list[ProviderConfig]:
        return ([self.provider] if self.provider is not None else []) + self.providers

    def safe_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        if data.get("provider"):
            data["provider"]["api_key"] = "**********"
        for provider in data.get("providers", []):
            provider["api_key"] = "**********"
        if data.get("judge_provider"):
            data["judge_provider"]["api_key"] = "**********"
        return data


class Usage(BaseModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RequestRecord(BaseModel):
    request_id: str
    provider: str
    case_id: str
    start_time: datetime
    first_token_time: datetime | None = None
    end_time: datetime
    ttfb_ms: float | None = None
    ttft_ms: float | None = None
    tpot_ms: float | None = None
    itl_ms: list[float] = Field(default_factory=list)
    e2e_ms: float
    tps: float | None = None
    tokens: int | None = None
    usage: Usage | None = None
    status: str
    status_code: int | None = None
    error: str | None = None
    request: dict[str, Any] = Field(default_factory=dict)
    response: dict[str, Any] = Field(default_factory=dict)


class PluginResult(BaseModel):
    name: str
    status: RunStatus
    started_at: datetime
    ended_at: datetime
    metrics: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    request_count: int = 0

    @classmethod
    def disabled(cls, name: str) -> PluginResult:
        now = datetime.now(UTC)
        return cls(name=name, status=RunStatus.DISABLED, started_at=now, ended_at=now)


class ProviderRunResult(BaseModel):
    provider: str
    model: str
    status: RunStatus
    plugins: dict[str, PluginResult]
    scorecard: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    run_id: str
    status: RunStatus
    started_at: datetime
    ended_at: datetime
    providers: dict[str, ProviderRunResult]
    config: dict[str, Any]
    comparisons: dict[str, Any] = Field(default_factory=dict)
