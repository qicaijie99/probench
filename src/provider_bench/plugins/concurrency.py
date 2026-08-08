from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import Field, model_validator

from provider_bench.models import RequestRecord
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin
from provider_bench.plugins.stats import percentile, status_counts


class ConcurrencySettings(PluginSettings):
    levels: list[int] = Field(default_factory=lambda: [1, 2, 4, 8, 16, 32, 64, 128])
    requests_per_level: int = Field(default=8, gt=0)
    prompt: str = "Give one practical API reliability tip."
    max_tokens: int = Field(default=64, gt=0)
    stable_success_rate: float = Field(default=0.995, ge=0, le=1)
    stable_ttft_p95_ms: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_levels(self) -> ConcurrencySettings:
        if not self.levels or any(level <= 0 for level in self.levels):
            raise ValueError("concurrency levels must be positive")
        return self


@register_plugin
class ConcurrencyPlugin(BenchmarkPlugin[ConcurrencySettings]):
    name = "concurrency"
    description = "Stepped concurrent-load capacity and stability benchmark"
    settings_model = ConcurrencySettings

    async def _one(self, level: int, index: int, semaphore: asyncio.Semaphore) -> RequestRecord:
        async with semaphore:
            record = await self.context.provider.chat(
                case_id=f"concurrency.{level}.{index + 1}",
                messages=[{"role": "user", "content": self.settings.prompt}],
                stream=True,
                max_tokens=self.settings.max_tokens,
            )
            await self.context.record(record)
            return record

    async def run(self) -> list[dict[str, Any]]:
        levels = []
        for level in self.settings.levels:
            count = max(level, self.settings.requests_per_level)
            semaphore = asyncio.Semaphore(level)
            started = time.perf_counter()
            records = await asyncio.gather(
                *(self._one(level, index, semaphore) for index in range(count))
            )
            elapsed = time.perf_counter() - started
            levels.append({"level": level, "elapsed": elapsed, "records": records})
        return levels

    def aggregate(self, raw_result: list[dict[str, Any]]) -> dict[str, Any]:
        summaries = []
        max_stable = 0
        for result in raw_result:
            records: list[RequestRecord] = result["records"]
            counts = status_counts(records)
            successful = [record for record in records if record.status == "success"]
            output_tokens = sum(record.tokens or 0 for record in successful)
            ttft_p95 = percentile((record.ttft_ms for record in successful), 0.95)
            stable = counts["success_rate"] >= self.settings.stable_success_rate
            if self.settings.stable_ttft_p95_ms is not None:
                stable = stable and ttft_p95 is not None and ttft_p95 < self.settings.stable_ttft_p95_ms
            if stable:
                max_stable = max(max_stable, result["level"])
            summaries.append(
                {
                    "concurrency": result["level"],
                    **counts,
                    "ttft_p95_ms": ttft_p95,
                    "output_tps": output_tokens / result["elapsed"] if result["elapsed"] else None,
                    "elapsed_seconds": result["elapsed"],
                    "stable": stable,
                }
            )
        return {"levels": summaries, "max_stable_concurrency": max_stable}
