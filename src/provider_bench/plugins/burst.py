from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic import Field, model_validator

from provider_bench.models import RequestRecord
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin
from provider_bench.plugins.stats import percentile, status_counts


class BurstSettings(PluginSettings):
    sizes: list[int] = Field(default_factory=lambda: [10, 25, 50, 100])
    prompt: str = "Reply with OK."
    max_tokens: int = Field(default=16, gt=0)

    @model_validator(mode="after")
    def validate_sizes(self) -> BurstSettings:
        if not self.sizes or any(size <= 0 for size in self.sizes):
            raise ValueError("burst sizes must be positive")
        return self


@register_plugin
class BurstPlugin(BenchmarkPlugin[BurstSettings]):
    name = "burst"
    description = "Simultaneous-request burst behavior at configurable batch sizes"
    settings_model = BurstSettings

    async def _one(self, size: int, index: int) -> RequestRecord:
        record = await self.context.provider.chat(
            case_id=f"burst.{size}.{index + 1}",
            messages=[{"role": "user", "content": self.settings.prompt}],
            stream=True,
            max_tokens=self.settings.max_tokens,
        )
        await self.context.record(record)
        return record

    async def run(self) -> list[dict[str, Any]]:
        batches = []
        for size in self.settings.sizes:
            started = time.perf_counter()
            records = await asyncio.gather(*(self._one(size, index) for index in range(size)))
            batches.append(
                {"size": size, "elapsed": time.perf_counter() - started, "records": records}
            )
        return batches

    def aggregate(self, raw_result: list[dict[str, Any]]) -> dict[str, Any]:
        batches = []
        for result in raw_result:
            records: list[RequestRecord] = result["records"]
            successful = [record for record in records if record.status == "success"]
            batches.append(
                {
                    "burst_size": result["size"],
                    **status_counts(records),
                    "ttft_p95_ms": percentile(
                        (record.ttft_ms for record in successful), 0.95
                    ),
                    "e2e_p95_ms": percentile(
                        (record.e2e_ms for record in successful), 0.95
                    ),
                    "elapsed_seconds": result["elapsed"],
                }
            )
        return {"batches": batches}
