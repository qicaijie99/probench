from __future__ import annotations

from typing import Any

from pydantic import Field

from provider_bench.models import RequestRecord
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin
from provider_bench.plugins.stats import distribution, status_counts


class LatencySettings(PluginSettings):
    warmup: int = Field(default=1, ge=0)
    repetitions: int = Field(default=10, gt=0)
    prompt: str = "Explain why low latency matters for interactive APIs."
    system_prompt: str | None = None
    max_tokens: int = Field(default=128, gt=0)
    temperature: float = Field(default=0, ge=0, le=2)


@register_plugin
class LatencyPlugin(BenchmarkPlugin[LatencySettings]):
    name = "latency"
    description = "Streaming TTFT, TPOT, ITL, E2E and output throughput distributions"
    settings_model = LatencySettings

    def _messages(self) -> list[dict[str, str]]:
        messages = []
        if self.settings.system_prompt:
            messages.append({"role": "system", "content": self.settings.system_prompt})
        messages.append({"role": "user", "content": self.settings.prompt})
        return messages

    async def run(self) -> list[RequestRecord]:
        for index in range(self.settings.warmup):
            record = await self.context.provider.chat(
                case_id=f"latency.warmup.{index + 1}",
                messages=self._messages(),
                stream=True,
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
            )
            await self.context.record(record)

        measured = []
        for index in range(self.settings.repetitions):
            record = await self.context.provider.chat(
                case_id=f"latency.measure.{index + 1}",
                messages=self._messages(),
                stream=True,
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
            )
            await self.context.record(record)
            measured.append(record)
        return measured

    def aggregate(self, raw_result: list[RequestRecord]) -> dict[str, Any]:
        successful = [record for record in raw_result if record.status == "success"]
        itl_values = [value for record in successful for value in record.itl_ms]
        details = [
            {
                "case_id": record.case_id,
                "ttft_ms": record.ttft_ms,
                "ttfb_ms": record.ttfb_ms,
                "e2e_ms": record.e2e_ms,
            }
            for record in successful
            if record.ttft_ms is not None
        ]
        return {
            **status_counts(raw_result),
            "ttfb_ms": distribution(record.ttfb_ms for record in successful),
            "ttft_ms": distribution(record.ttft_ms for record in successful),
            "tpot_ms": distribution(record.tpot_ms for record in successful),
            "itl_ms": distribution(itl_values),
            "e2e_ms": distribution(record.e2e_ms for record in successful),
            "output_tps": distribution(record.tps for record in successful),
            "details": details,
        }
