from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from provider_bench.engine import BenchmarkEngine
from provider_bench.models import AppConfig, RunStatus

from .fakes import FakeProvider


async def test_concurrency_and_burst_execute_configured_parallel_batches(tmp_path: Path) -> None:
    class TrackingProvider(FakeProvider):
        active = 0
        max_active = 0

        async def chat(self, **kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            try:
                await asyncio.sleep(0.005)
                return await super().chat(**kwargs)
            finally:
                self.active -= 1

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
                "concurrency": {
                    "enabled": True,
                    "levels": [1, 2, 4],
                    "requests_per_level": 2,
                },
                "burst": {"enabled": True, "sizes": [2, 3]},
            },
        }
    )
    result = await BenchmarkEngine(provider_factory=lambda _: provider).run(
        config, run_id="load-plugins"
    )
    plugins = result.providers["fake"].plugins
    assert plugins["concurrency"].request_count == 8
    assert plugins["concurrency"].metrics["max_stable_concurrency"] == 4
    assert [level["success_rate"] for level in plugins["concurrency"].metrics["levels"]] == [1, 1, 1]
    assert plugins["burst"].request_count == 5
    assert [batch["burst_size"] for batch in plugins["burst"].metrics["batches"]] == [2, 3]
    assert provider.max_active == 4


async def test_quality_tool_structured_identity_and_billing_plugins(tmp_path: Path) -> None:
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
                "quality": {"enabled": True},
                "tool_calling": {"enabled": True},
                "structured_output": {"enabled": True},
                "model_identity": {"enabled": True, "repetitions": 2},
                "billing": {"enabled": True},
            },
        }
    )
    result = await BenchmarkEngine(
        provider_factory=lambda item: FakeProvider(item.name, item.model)
    ).run(config, run_id="remaining-plugins")
    plugins = result.providers["fake"].plugins

    assert all(plugin.status == RunStatus.COMPLETED for plugin in plugins.values())
    assert plugins["quality"].metrics["cases"] == 7
    assert plugins["quality"].metrics["pass_rate"] == 1
    assert plugins["quality"].metrics["evaluators"]["llm_judge"]["score"] == 0.95
    assert plugins["tool_calling"].metrics["success_rate"] == 1
    assert plugins["structured_output"].metrics["schema_compliance_rate"] == 1
    assert plugins["model_identity"].metrics["behavior_consistency"] == 1
    assert plugins["billing"].metrics["usage_present_rate"] == 1
    assert (tmp_path / "remaining-plugins/benchmarks/quality/requests.jsonl").is_file()


async def test_latency_thinking_mode_measures_ttfr_ttfc_and_overhead(tmp_path: Path) -> None:
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
                "latency": {
                    "enabled": True,
                    "warmup": 0,
                    "repetitions": 3,
                    "thinking": True,
                    "thinking_prompt": "逐步推理：3x+5y=41 且 x+y=11，求 x。",
                    "extra": {"reasoning_effort": "high"},
                },
            },
        }
    )
    result = await BenchmarkEngine(provider_factory=lambda _: provider).run(
        config, run_id="latency-thinking"
    )
    metrics = result.providers["fake"].plugins["latency"].metrics

    measure_call = next(
        call for call in provider.calls if call["case_id"] == "latency.measure.1"
    )
    assert measure_call["extra"] == {
        "enable_thinking": True,
        "reasoning_effort": "high",
    }
    assert measure_call["messages"][-1]["content"] == "逐步推理：3x+5y=41 且 x+y=11，求 x。"

    assert metrics["ttfr_ms"]["p50"] == 20
    assert metrics["ttfc_ms"]["p50"] == 60
    assert metrics["thinking_overhead_ms"]["p50"] == 40
    assert metrics["details"][0]["ttfr_ms"] == 20
    assert metrics["details"][0]["ttfc_ms"] == 60
    report = (tmp_path / "latency-thinking/report.html").read_text(encoding="utf-8")
    assert "TTFR（首个推理 token）" in report
    assert "TTFC（首个正文 token）" in report


async def test_multi_provider_run_generates_metric_and_identity_comparison(
    tmp_path: Path,
) -> None:
    config = AppConfig.model_validate(
        {
            "providers": [
                {
                    "name": "official",
                    "base_url": "https://official.test/v1",
                    "api_key": "official-secret",
                    "model": "official-model",
                },
                {
                    "name": "candidate",
                    "base_url": "https://candidate.test/v1",
                    "api_key": "candidate-secret",
                    "model": "candidate-model",
                },
            ],
            "output_dir": str(tmp_path),
            "benchmarks": {
                "latency": {"enabled": True, "warmup": 0, "repetitions": 2},
                "model_identity": {"enabled": True, "repetitions": 1},
            },
        }
    )
    result = await BenchmarkEngine(
        provider_factory=lambda item: FakeProvider(item.name, item.model)
    ).run(config, run_id="provider-comparison")

    assert result.comparisons["reference"] == "official"
    candidate = result.comparisons["candidates"]["candidate"]
    assert candidate["metrics"]
    assert candidate["identity"]["behavior_similarity"] == 1
    assert candidate["identity"]["reported_model_equal"] is False
    report = (tmp_path / "provider-comparison/report.html").read_text(encoding="utf-8")
    assert "Provider comparison" in report
    assert "candidate vs official" in report
