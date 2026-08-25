from __future__ import annotations

from typing import Any

from pydantic import Field

from provider_bench.models import RequestRecord
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin
from provider_bench.plugins.stats import distribution, status_counts


_DEFAULT_THINKING_PROMPT = (
    "逐步推理并解答：某工厂生产两种零件，A 每个重 3 克、B 每个重 5 克。"
    "现有 41 个零件总重 167 克，请问 A 零件有几个？请先给出完整推理过程，再给出最终答案。"
)


class LatencySettings(PluginSettings):
    warmup: int = Field(default=1, ge=0)
    repetitions: int = Field(default=10, gt=0)
    prompt: str = "Explain why low latency matters for interactive APIs."
    system_prompt: str | None = None
    max_tokens: int = Field(default=128, gt=0)
    temperature: float = Field(default=0, ge=0, le=2)
    extra: dict[str, Any] | None = None
    thinking: bool = False
    thinking_prompt: str | None = None


@register_plugin
class LatencyPlugin(BenchmarkPlugin[LatencySettings]):
    name = "latency"
    description = "Streaming TTFT, TPOT, ITL, E2E and output throughput distributions"
    settings_model = LatencySettings

    def _messages(self) -> list[dict[str, str]]:
        messages = []
        if self.settings.system_prompt:
            messages.append({"role": "system", "content": self.settings.system_prompt})
        if self.settings.thinking:
            prompt = self.settings.thinking_prompt or _DEFAULT_THINKING_PROMPT
        else:
            prompt = self.settings.prompt
        messages.append({"role": "user", "content": prompt})
        return messages

    def _extra(self) -> dict[str, Any] | None:
        extra = dict(self.settings.extra or {})
        if self.settings.thinking and "enable_thinking" not in extra:
            extra["enable_thinking"] = True
        return extra or None

    async def run(self) -> list[RequestRecord]:
        for index in range(self.settings.warmup):
            record = await self.context.provider.chat(
                case_id=f"latency.warmup.{index + 1}",
                messages=self._messages(),
                stream=True,
                max_tokens=self.settings.max_tokens,
                temperature=self.settings.temperature,
                extra=self._extra(),
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
                extra=self._extra(),
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
        metrics: dict[str, Any] = {
            **status_counts(raw_result),
            "ttfb_ms": distribution(record.ttfb_ms for record in successful),
            "ttft_ms": distribution(record.ttft_ms for record in successful),
            "tpot_ms": distribution(record.tpot_ms for record in successful),
            "itl_ms": distribution(itl_values),
            "e2e_ms": distribution(record.e2e_ms for record in successful),
            "output_tps": distribution(record.tps for record in successful),
            "details": details,
        }
        if self.settings.thinking:
            metrics["ttfr_ms"] = distribution(record.ttfr_ms for record in successful)
            metrics["ttfc_ms"] = distribution(record.ttfc_ms for record in successful)
            metrics["thinking_overhead_ms"] = distribution(
                record.ttfc_ms - record.ttfr_ms
                for record in successful
                if record.ttfr_ms is not None and record.ttfc_ms is not None
            )
            by_case = {record.case_id: record for record in successful}
            for detail in details:
                record = by_case.get(str(detail["case_id"]))
                if record is not None:
                    detail["ttfr_ms"] = record.ttfr_ms
                    detail["ttfc_ms"] = record.ttfc_ms
        return metrics
