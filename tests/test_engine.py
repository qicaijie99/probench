from __future__ import annotations

import json
from pathlib import Path

from provider_bench.engine import BenchmarkEngine
from provider_bench.models import AppConfig, RunStatus

from .fakes import FakeProvider


async def test_engine_runs_plugins_and_writes_complete_artifacts(tmp_path: Path) -> None:
    fake = FakeProvider()
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
                "compatibility": {"enabled": True},
                "latency": {"enabled": True, "warmup": 1, "repetitions": 3},
                "burst": {"enabled": False},
            },
            "scoring": {
                "gates": [
                    {
                        "metric": "compatibility.success_rate",
                        "operator": ">=",
                        "value": 0.95,
                    }
                ]
            },
        }
    )
    events: list[dict[str, object]] = []

    async def capture(event: dict[str, object]) -> None:
        events.append(event)

    result = await BenchmarkEngine(provider_factory=lambda _: fake).run(
        config, event_handler=capture, run_id="test-run"
    )

    assert result.status == RunStatus.COMPLETED
    assert result.providers["fake"].plugins["compatibility"].status == RunStatus.COMPLETED
    assert result.providers["fake"].plugins["latency"].request_count == 4
    assert result.providers["fake"].plugins["burst"].status == RunStatus.DISABLED
    assert result.providers["fake"].scorecard["verdict"] == "PASS"
    assert fake.closed
    assert events[0]["type"] == "run.started"
    assert events[-1]["type"] == "run.completed"

    run_dir = tmp_path / "test-run"
    assert (run_dir / "report.html").is_file()
    assert (run_dir / "report.md").is_file()
    assert (run_dir / "scores.json").is_file()
    assert (run_dir / "benchmarks/latency/requests.jsonl").is_file()
    assert len((run_dir / "benchmarks/latency/requests.jsonl").read_text().splitlines()) == 4
    saved = json.loads((run_dir / "run.json").read_text())
    assert saved["config"]["provider"]["api_key"] == "**********"
    assert "secret" not in (run_dir / "run.json").read_text()


async def test_only_and_skip_selection(tmp_path: Path) -> None:
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
                "compatibility": {"enabled": True},
                "latency": {"enabled": True, "warmup": 0, "repetitions": 1},
            },
        }
    )
    result = await BenchmarkEngine(provider_factory=lambda _: FakeProvider()).run(
        config, only={"latency"}, run_id="selected-run"
    )
    plugins = result.providers["fake"].plugins
    assert plugins["compatibility"].status == RunStatus.DISABLED
    assert plugins["latency"].status == RunStatus.COMPLETED


async def test_plugin_failure_does_not_stop_later_plugins(tmp_path: Path) -> None:
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
                "quality": {"enabled": True, "datasets": [str(tmp_path / "missing.yaml")]},
                "latency": {"enabled": True, "warmup": 0, "repetitions": 1},
            },
        }
    )
    result = await BenchmarkEngine(provider_factory=lambda _: FakeProvider()).run(
        config, run_id="isolated-failure"
    )
    plugins = result.providers["fake"].plugins
    assert result.status == RunStatus.FAILED
    assert plugins["quality"].status == RunStatus.FAILED
    assert plugins["latency"].status == RunStatus.COMPLETED
    assert (tmp_path / "isolated-failure/benchmarks/quality/metrics.json").is_file()
