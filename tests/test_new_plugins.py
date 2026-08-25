from __future__ import annotations

import ssl
from pathlib import Path
from typing import Any

import httpx

from provider_bench.cache import extract_cache_usage
from provider_bench.engine import BenchmarkEngine
from provider_bench.models import AppConfig, RunStatus
from provider_bench.providers.openai import _error_kind, _is_ssl_error

from .fakes import FakeProvider


def test_extract_cache_usage_openai_and_anthropic_shapes() -> None:
    openai_usage = {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "prompt_tokens_details": {"cached_tokens": 80},
    }
    assert extract_cache_usage(openai_usage)["cache_read_tokens"] == 80
    assert extract_cache_usage(openai_usage)["prompt_tokens"] == 100

    anthropic_usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_creation_input_tokens": 60,
        "cache_read_input_tokens": 40,
    }
    result = extract_cache_usage(anthropic_usage)
    assert result["cache_read_tokens"] == 40
    assert result["cache_write_tokens"] == 60

    top_level = {"prompt_tokens": 90, "cached_tokens": 90}
    assert extract_cache_usage(top_level)["cache_read_tokens"] == 90

    assert extract_cache_usage(None)["cache_read_tokens"] == 0


async def test_features_protocol_and_cache_plugins(tmp_path: Path) -> None:
    class TrackingProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__(name="fake", model="fake-model")
            self.calls: list[dict[str, Any]] = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            return await super().chat(**kwargs)

    provider = TrackingProvider()
    config = AppConfig.model_validate(
        {
            "provider": {
                "name": "fake",
                "base_url": "https://fake.test/v1",
                "api_key": "secret",
                "model": "fake-model",
            },
            "output_dir": str(tmp_path),
            "benchmarks": {
                "features": {"enabled": True},
                "protocol": {"enabled": True},
                "cache": {"enabled": True, "prefix_chars": 64},
            },
        }
    )
    result = await BenchmarkEngine(provider_factory=lambda _: provider).run(
        config, run_id="new-plugins"
    )
    plugins = result.providers["fake"].plugins

    assert all(plugin.status == RunStatus.COMPLETED for plugin in plugins.values())

    features = plugins["features"].metrics
    omit_call = next(
        call for call in provider.calls if call["case_id"] == "features.param.omit_sampling"
    )
    assert omit_call["omit_temperature"] is True
    reject_call = next(
        call for call in provider.calls if call["case_id"] == "features.param.reject_temperature_1_1"
    )
    assert reject_call["extra"] == {"temperature": 1.1}
    reject_result = next(
        item
        for item in features["param_constraint_results"]
        if item["param"] == "temperature" and item["value"] == 1.1
    )
    assert reject_result["passed"] is False
    assert reject_result["warn"] is True
    assert "网关未拒绝越界参数（宽松）" in reject_result["note"]
    param_summary = features["param_constraints"]
    assert param_summary["warned"] == 7
    assert param_summary["passed"] == 6
    assert param_summary["failed"] == 0

    protocol = plugins["protocol"].metrics
    assert protocol["checks"]["ping"]["passed"] is True
    assert protocol["checks"]["image_base64"]["passed"] is True
    assert protocol["checks"]["video_base64"]["passed"] is True
    assert protocol["total"] == 5

    cache = plugins["cache"].metrics
    assert cache["measured"] == 2
    assert cache["hit_rate"] == 0.0


async def test_benchmark_plugin_sessions_turns_and_metrics(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "provider": {
                "name": "fake",
                "base_url": "https://fake.test/v1",
                "api_key": "secret",
                "model": "fake-model",
            },
            "output_dir": str(tmp_path),
            "benchmarks": {
                "benchmark": {
                    "enabled": True,
                    "sessions": 2,
                    "turns": 2,
                    "init_tokens": 16,
                    "output_tokens": 8,
                    "arrival_start": 100,
                    "arrival_end": 100,
                    "ramp_seconds": 0,
                },
            },
        }
    )
    result = await BenchmarkEngine(
        provider_factory=lambda item: FakeProvider(item.name, item.model)
    ).run(config, run_id="benchmark-plugin")
    metrics = result.providers["fake"].plugins["benchmark"].metrics
    assert metrics["total_requests"] == 4
    assert metrics["success_rate"] == 1.0
    assert metrics["stop_reason"] == "all_sessions_completed"
    assert len(metrics["per_round"]) == 2
    assert metrics["charts"]["scatter"]
    assert len(metrics["session_details"]) == 2
    assert metrics["session_details"][0]["turn"] == 1
    assert "baseline" in metrics
    assert "checks" in metrics["baseline"]


def test_ssl_errors_are_classified() -> None:
    cause = ssl.SSLError("SSL: UNEXPECTED_EOF_WHILE_READING")
    error = httpx.ConnectError("connection error", request=None)
    error.__cause__ = cause
    assert _is_ssl_error(error) is True
    assert _error_kind(error) == ("ssl_error", None)

    timeout = httpx.ReadTimeout("timed out")
    assert _error_kind(timeout) == ("timeout", None)

    request = httpx.Request("POST", "https://x.test/v1/chat/completions")
    response = httpx.Response(429, request=request)
    status_error = httpx.HTTPStatusError("rate limit", request=request, response=response)
    assert _error_kind(status_error) == ("rate_limited", 429)


async def test_thinking_toggle_detects_switch(tmp_path: Path) -> None:
    class TrackingProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__(name="fake", model="fake-model")
            self.calls: list[dict[str, Any]] = []

        async def chat(self, **kwargs):
            self.calls.append(kwargs)
            return await super().chat(**kwargs)

    provider = TrackingProvider()
    config = AppConfig.model_validate(
        {
            "provider": {
                "name": "fake",
                "base_url": "https://fake.test/v1",
                "api_key": "secret",
                "model": "fake-model",
            },
            "output_dir": str(tmp_path),
            "benchmarks": {"features": {"enabled": True}},
        }
    )
    result = await BenchmarkEngine(provider_factory=lambda _: provider).run(
        config, run_id="thinking-toggle"
    )
    toggle = result.providers["fake"].plugins["features"].metrics["thinking_toggle"]
    assert toggle["toggle_works"] is True
    assert toggle["passed"] is True
    assert toggle["warn"] is False
    on_call = next(c for c in provider.calls if c["case_id"] == "features.thinking_toggle_on")
    off_call = next(c for c in provider.calls if c["case_id"] == "features.thinking_toggle_off")
    assert on_call["extra"] == {"enable_thinking": True}
    assert off_call["extra"] == {"enable_thinking": False}


async def test_thinking_toggle_warns_when_off_still_reasons(tmp_path: Path) -> None:
    class AlwaysReasoningProvider(FakeProvider):
        async def chat(self, **kwargs):
            result = await super().chat(**kwargs)
            result.response["reasoning_content"] = "thinking regardless of switch"
            return result

    config = AppConfig.model_validate(
        {
            "provider": {
                "name": "fake",
                "base_url": "https://fake.test/v1",
                "api_key": "secret",
                "model": "fake-model",
            },
            "output_dir": str(tmp_path),
            "benchmarks": {"features": {"enabled": True}},
        }
    )
    result = await BenchmarkEngine(
        provider_factory=lambda item: AlwaysReasoningProvider(item.name, item.model)
    ).run(config, run_id="thinking-toggle-warn")
    toggle = result.providers["fake"].plugins["features"].metrics["thinking_toggle"]
    assert toggle["passed"] is False
    assert toggle["warn"] is True
    assert "无法真正关闭思考" in toggle["note"]


async def test_quality_max_tokens_floor(tmp_path: Path) -> None:
    class TrackingProvider(FakeProvider):
        def __init__(self) -> None:
            super().__init__(name="fake", model="fake-model")
            self.case_budgets: list[tuple[str, int]] = []

        async def chat(self, **kwargs):
            if "judge" not in kwargs["case_id"]:
                self.case_budgets.append((kwargs["case_id"], kwargs["max_tokens"]))
            return await super().chat(**kwargs)

    provider = TrackingProvider()
    config = AppConfig.model_validate(
        {
            "provider": {
                "name": "fake",
                "base_url": "https://fake.test/v1",
                "api_key": "secret",
                "model": "fake-model",
            },
            "output_dir": str(tmp_path),
            "benchmarks": {"quality": {"enabled": True, "max_tokens": 512}},
        }
    )
    await BenchmarkEngine(provider_factory=lambda _: provider).run(config, run_id="quality-floor")
    assert provider.case_budgets
    assert all(budget >= 512 for _, budget in provider.case_budgets)
