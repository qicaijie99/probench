from __future__ import annotations

from pathlib import Path

from provider_bench.engine import BenchmarkEngine
from provider_bench.models import AppConfig
from provider_bench.report import compare_runs, regenerate_report

from .fakes import FakeProvider


async def test_reports_can_be_regenerated_and_runs_have_metric_deltas(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "provider": {
                "name": "fake",
                "base_url": "https://fake.test/v1",
                "api_key": "secret",
                "model": "fake-model",
            },
            "output_dir": str(tmp_path),
            "benchmarks": {"latency": {"enabled": True, "warmup": 0, "repetitions": 2}},
        }
    )
    engine = BenchmarkEngine(provider_factory=lambda _: FakeProvider())
    await engine.run(config, run_id="first")
    await engine.run(config, run_id="second")

    comparison = compare_runs(tmp_path / "first", tmp_path / "second")
    assert comparison["providers"][0]["plugin_deltas"]
    markdown, html = regenerate_report(tmp_path / "first")
    assert markdown.is_file() and html.is_file()
    assert "Benchmark Report: first" in markdown.read_text(encoding="utf-8")
