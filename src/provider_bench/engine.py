from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from provider_bench.models import (
    AppConfig,
    PluginResult,
    ProviderConfig,
    ProviderRunResult,
    RunResult,
    RunStatus,
)
from provider_bench.comparison import build_provider_comparisons
from provider_bench.plugins.base import EventHandler, PluginContext
from provider_bench.plugins.registry import PluginRegistry, registry
from provider_bench.providers.base import Provider
from provider_bench.providers.openai import OpenAICompatibleProvider
from provider_bench.report import write_reports
from provider_bench.scoring import build_scorecard
from provider_bench.storage import safe_name, write_json

ProviderFactory = Callable[[ProviderConfig], Provider]


async def _ignore_event(event: dict[str, Any]) -> None:
    return None


def _platform_name(base_url: str) -> str:
    host = urlparse(base_url).netloc
    if ":" in host:
        host = host.rsplit(":", 1)[0]
    return safe_name(host) or "unknown"


def new_run_id(config: AppConfig | None = None) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8]
    if config is None or not config.selected_providers:
        return f"{stamp}-{suffix}"
    hosts = sorted({_platform_name(provider.base_url) for provider in config.selected_providers})
    models = sorted({safe_name(provider.model) for provider in config.selected_providers})
    return f"{stamp}-{'-'.join(hosts)}-{'-'.join(models)}-{suffix}"


class BenchmarkEngine:
    def __init__(
        self,
        *,
        plugin_registry: PluginRegistry = registry,
        provider_factory: ProviderFactory = OpenAICompatibleProvider,
    ) -> None:
        self.registry = plugin_registry
        self.provider_factory = provider_factory

    def selected_plugins(
        self,
        config: AppConfig,
        *,
        only: set[str] | None = None,
        skip: set[str] | None = None,
    ) -> list[str]:
        skip = skip or set()
        configured = set(config.benchmarks)
        if only:
            missing = only - configured
            if missing:
                raise ValueError(f"--only plugins are not configured: {', '.join(sorted(missing))}")
            configured &= only
        configured -= skip
        unknown = {
            name
            for name in configured
            if config.benchmarks[name].get("enabled", True) and name not in self.registry.names()
        }
        if unknown:
            raise ValueError(f"unknown enabled plugins: {', '.join(sorted(unknown))}")
        return [name for name in config.benchmarks if name in configured]

    async def run(
        self,
        config: AppConfig,
        *,
        only: set[str] | None = None,
        skip: set[str] | None = None,
        event_handler: EventHandler | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        emit = event_handler or _ignore_event
        run_id = run_id or new_run_id(config)
        started = datetime.now(UTC)
        run_dir = config.output_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        selected = self.selected_plugins(config, only=only, skip=skip)
        write_json(run_dir / "config.json", config.safe_dict())
        write_json(
            run_dir / "state.json",
            {"run_id": run_id, "status": RunStatus.RUNNING, "started_at": started},
        )
        total_plugins = len(config.selected_providers) * sum(
            name in selected and settings.get("enabled", True)
            for name, settings in config.benchmarks.items()
        )
        await emit(
            {
                "type": "run.started",
                "run_id": run_id,
                "status": RunStatus.RUNNING,
                "started_at": started,
                "total_plugins": total_plugins,
                "progress": 0.0,
            }
        )
        providers: dict[str, ProviderRunResult] = {}
        multiple_providers = len(config.selected_providers) > 1
        completed_plugins = 0

        for provider_config in config.selected_providers:
            provider = self.provider_factory(provider_config)
            judge_provider = (
                self.provider_factory(config.judge_provider)
                if config.judge_provider is not None
                else provider
            )
            provider_plugins: dict[str, PluginResult] = {}
            base_dir = (
                run_dir / "providers" / safe_name(provider_config.name)
                if multiple_providers
                else run_dir
            )
            await emit(
                {
                    "type": "provider.started",
                    "run_id": run_id,
                    "provider": provider.name,
                    "model": provider.model,
                }
            )
            try:
                for name, raw_settings in config.benchmarks.items():
                    is_selected = name in selected and raw_settings.get("enabled", True)
                    if not is_selected:
                        provider_plugins[name] = PluginResult.disabled(name)
                        continue
                    plugin_started = datetime.now(UTC)
                    context = PluginContext(
                        run_id=run_id,
                        provider=provider,
                        judge_provider=judge_provider,
                        output_dir=base_dir / "benchmarks" / name,
                        emit=emit,
                    )
                    plugin_class = self.registry.get(name)
                    plugin = None
                    error: str | None = None
                    metrics: dict[str, Any] = {}
                    status = RunStatus.RUNNING
                    await emit(
                        {
                            "type": "plugin.started",
                            "run_id": run_id,
                            "provider": provider.name,
                            "plugin": name,
                            "status": status,
                        }
                    )
                    try:
                        plugin = plugin_class(raw_settings, context)
                        await plugin.prepare()
                        raw_result = await plugin.run()
                        metrics = plugin.aggregate(raw_result)
                        status = RunStatus.COMPLETED
                    except Exception as exc:
                        status = RunStatus.FAILED
                        error = f"{type(exc).__name__}: {exc}"
                    finally:
                        if plugin is not None:
                            try:
                                await plugin.cleanup()
                            except Exception as exc:
                                status = RunStatus.FAILED
                                error = error or f"cleanup {type(exc).__name__}: {exc}"
                    plugin_result = PluginResult(
                        name=name,
                        status=status,
                        started_at=plugin_started,
                        ended_at=datetime.now(UTC),
                        metrics=metrics,
                        error=error,
                        request_count=len(context.records),
                    )
                    provider_plugins[name] = plugin_result
                    completed_plugins += 1
                    write_json(context.output_dir / "metrics.json", plugin_result)
                    await emit(
                        {
                            "type": "plugin.completed",
                            "run_id": run_id,
                            "provider": provider.name,
                            "plugin": name,
                            "status": status,
                            "metrics": metrics,
                            "error": error,
                            "completed_plugins": completed_plugins,
                            "total_plugins": total_plugins,
                            "progress": completed_plugins / total_plugins if total_plugins else 1.0,
                        }
                    )
            finally:
                await provider.close()
                if judge_provider is not provider:
                    await judge_provider.close()

            provider_status = (
                RunStatus.FAILED
                if any(plugin.status == RunStatus.FAILED for plugin in provider_plugins.values())
                else RunStatus.COMPLETED
            )
            providers[provider.name] = ProviderRunResult(
                provider=provider.name,
                model=provider.model,
                status=provider_status,
                plugins=provider_plugins,
                scorecard=build_scorecard(provider_plugins, config.scoring),
            )
            await emit(
                {
                    "type": "provider.completed",
                    "run_id": run_id,
                    "provider": provider.name,
                    "status": provider_status,
                }
            )

        status = (
            RunStatus.FAILED
            if any(provider.status == RunStatus.FAILED for provider in providers.values())
            else RunStatus.COMPLETED
        )
        comparisons = build_provider_comparisons(providers, config.reference_provider)
        result = RunResult(
            run_id=run_id,
            status=status,
            started_at=started,
            ended_at=datetime.now(UTC),
            providers=providers,
            config=config.safe_dict(),
            comparisons=comparisons,
        )
        write_json(run_dir / "run.json", result)
        write_json(
            run_dir / "state.json",
            {
                "run_id": run_id,
                "status": status,
                "started_at": started,
                "ended_at": result.ended_at,
                "providers": {
                    name: {
                        "model": provider.model,
                        "score": provider.scorecard.get("score"),
                        "verdict": provider.scorecard.get("verdict"),
                    }
                    for name, provider in providers.items()
                },
            },
        )
        write_reports(run_dir, result)
        await emit(
            {
                "type": "run.completed",
                "run_id": run_id,
                "status": status,
                "providers": {
                    name: {
                        "model": provider.model,
                        "score": provider.scorecard.get("score"),
                        "verdict": provider.scorecard.get("verdict"),
                    }
                    for name, provider in providers.items()
                },
                "progress": 1.0,
            }
        )
        return result
