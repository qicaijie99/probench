from __future__ import annotations

import statistics
from typing import Any

import tiktoken
from pydantic import BaseModel, Field

from provider_bench.models import RequestRecord
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin


class TokenPricing(BaseModel):
    input_per_million: float = Field(ge=0)
    output_per_million: float = Field(ge=0)


class BillingSettings(PluginSettings):
    prompts: list[str] = Field(
        default_factory=lambda: [
            "Reply with exactly five English words about reliable APIs.",
            "用一句中文说明记录 token usage 的用途。",
            "Return the first eight prime numbers separated by commas.",
        ]
    )
    max_tokens: int = Field(default=128, gt=0)
    tokenizer_model: str | None = None
    tokenizer_encoding: str = "cl100k_base"
    allowed_deviation: float = Field(default=0.05, ge=0, le=1)
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    provider_prices: dict[str, TokenPricing] = Field(default_factory=dict)
    target_cost_per_request_usd: float | None = Field(default=None, gt=0)


@register_plugin
class BillingPlugin(BenchmarkPlugin[BillingSettings]):
    name = "billing"
    description = "Reported usage, local tokenizer deviation and estimated request cost"
    settings_model = BillingSettings

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> BillingSettings:
        settings = BillingSettings.model_validate(config)
        if not settings.prompts:
            raise ValueError("billing requires at least one prompt")
        try:
            if settings.tokenizer_model:
                tiktoken.encoding_for_model(settings.tokenizer_model)
            else:
                tiktoken.get_encoding(settings.tokenizer_encoding)
        except KeyError as exc:
            raise ValueError(f"unknown tokenizer model or encoding: {exc}") from exc
        return settings

    async def prepare(self) -> None:
        await super().prepare()
        try:
            self.encoding = (
                tiktoken.encoding_for_model(self.settings.tokenizer_model)
                if self.settings.tokenizer_model
                else tiktoken.get_encoding(self.settings.tokenizer_encoding)
            )
        except KeyError as exc:
            raise ValueError(f"unknown tokenizer model or encoding: {exc}") from exc

    def _prompt_tokens(self, messages: list[dict[str, str]]) -> int:
        count = 3
        for message in messages:
            count += 3
            count += sum(len(self.encoding.encode(str(value))) for value in message.values())
        return count

    async def run(self) -> list[dict[str, Any]]:
        results = []
        for index, prompt in enumerate(self.settings.prompts):
            messages = [{"role": "user", "content": prompt}]
            record = await self.context.provider.chat(
                case_id=f"billing.{index + 1}",
                messages=messages,
                stream=False,
                max_tokens=self.settings.max_tokens,
                temperature=0,
            )
            await self.context.record(record)
            results.append(self._measure(record, messages))
        if not results:
            raise ValueError("billing requires at least one prompt")
        return results

    def _measure(
        self, record: RequestRecord, messages: list[dict[str, str]]
    ) -> dict[str, Any]:
        local_prompt = self._prompt_tokens(messages)
        local_completion = len(self.encoding.encode(str(record.response.get("content") or "")))
        reported_prompt = record.usage.prompt_tokens if record.usage else None
        reported_completion = record.usage.completion_tokens if record.usage else None
        reasoning_tokens = record.usage.reasoning_tokens if record.usage else None
        reported_completion_visible = (
            reported_completion - reasoning_tokens
            if reported_completion is not None and reasoning_tokens is not None
            else reported_completion
        )

        def deviation(reported: int | None, local: int) -> float | None:
            return (reported - local) / local if reported is not None and local else None

        prompt_deviation = deviation(reported_prompt, local_prompt)
        completion_deviation = deviation(reported_completion_visible, local_completion)
        within = (
            prompt_deviation is not None
            and completion_deviation is not None
            and abs(prompt_deviation) <= self.settings.allowed_deviation
            and abs(completion_deviation) <= self.settings.allowed_deviation
        )
        billed_prompt = reported_prompt if reported_prompt is not None else local_prompt
        billed_completion = (
            reported_completion if reported_completion is not None else local_completion
        )
        configured_pricing = self.settings.provider_prices.get(self.context.provider.name)
        input_price = (
            configured_pricing.input_per_million
            if configured_pricing
            else self.settings.input_price_per_million
        )
        output_price = (
            configured_pricing.output_per_million
            if configured_pricing
            else self.settings.output_price_per_million
        )
        cost = None
        if input_price is not None and output_price is not None:
            cost = (
                billed_prompt * input_price
                + billed_completion * output_price
            ) / 1_000_000
        return {
            "request_id": record.request_id,
            "status": record.status,
            "usage_present": record.usage is not None,
            "reported_prompt_tokens": reported_prompt,
            "local_prompt_tokens": local_prompt,
            "prompt_deviation": prompt_deviation,
            "reported_completion_tokens": reported_completion,
            "local_completion_tokens": local_completion,
            "reasoning_tokens": reasoning_tokens,
            "completion_deviation": completion_deviation,
            "within_tolerance": within,
            "estimated_cost_usd": cost,
            "error": record.error,
        }

    def aggregate(self, raw_result: list[dict[str, Any]]) -> dict[str, Any]:
        prompt_deviations = [
            abs(item["prompt_deviation"])
            for item in raw_result
            if item["prompt_deviation"] is not None
        ]
        completion_deviations = [
            abs(item["completion_deviation"])
            for item in raw_result
            if item["completion_deviation"] is not None
        ]
        costs = [
            item["estimated_cost_usd"]
            for item in raw_result
            if item["estimated_cost_usd"] is not None
        ]
        average_cost = statistics.fmean(costs) if costs else None
        cost_score = None
        if average_cost is not None and self.settings.target_cost_per_request_usd is not None:
            cost_score = min(
                100.0, self.settings.target_cost_per_request_usd / max(average_cost, 1e-12) * 100
            )
        total = len(raw_result)
        within_count = sum(item["within_tolerance"] for item in raw_result)
        return {
            "requests": total,
            "usage_present_rate": sum(item["usage_present"] for item in raw_result) / total,
            "within_tolerance_rate": within_count / total,
            "success_rate": within_count / total,
            "mean_absolute_prompt_deviation": (
                statistics.fmean(prompt_deviations) if prompt_deviations else None
            ),
            "mean_absolute_completion_deviation": (
                statistics.fmean(completion_deviations) if completion_deviations else None
            ),
            "estimated_total_cost_usd": sum(costs) if costs else None,
            "estimated_cost_per_request_usd": average_cost,
            "cost_score": cost_score,
            "results": raw_result,
        }
