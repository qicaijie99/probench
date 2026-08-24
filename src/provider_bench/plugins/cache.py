from __future__ import annotations

import random
from typing import Any

from pydantic import Field

from provider_bench.cache import extract_cache_usage
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin

_FILLER_SENTENCE = (
    "The quick brown fox jumps over the lazy dog while the patient observer watches quietly. "
)


def _filler(length: int) -> str:
    return (_FILLER_SENTENCE * (length // len(_FILLER_SENTENCE) + 1))[:length]


class CacheSettings(PluginSettings):
    prefix_chars: int = Field(default=4096, gt=0)
    rounds: int = Field(default=2, gt=0)
    warmup: bool = True
    max_tokens: int = Field(default=64, gt=0)
    suffix_template: str = "简洁一点，随机数={rand}"


@register_plugin
class CachePlugin(BenchmarkPlugin[CacheSettings]):
    name = "cache"
    description = "Prefix-cache hit-rate measurement across warmup and repeated rounds"
    settings_model = CacheSettings

    def _prefix(self) -> str:
        header = "[cache-hit-test] You are a helpful assistant. Context document follows. "
        return header + _filler(self.settings.prefix_chars)

    def _messages(self, prefix: str) -> list[dict[str, Any]]:
        suffix = self.settings.suffix_template.format(rand=random.randint(100000000, 999999999))
        return [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prefix},
                    {"type": "text", "text": suffix},
                ],
            }
        ]

    async def run(self) -> list[dict[str, Any]]:
        prefix = self._prefix()
        rounds: list[dict[str, Any]] = []
        if self.settings.warmup:
            record = await self.context.provider.chat(
                case_id="cache.round_0",
                messages=self._messages(prefix),
                stream=True,
                max_tokens=self.settings.max_tokens,
                extra={"stream_options": {"include_usage": True}},
            )
            await self.context.record(record)
            rounds.append({"warmup": True, "record": record})
        for index in range(self.settings.rounds):
            record = await self.context.provider.chat(
                case_id=f"cache.round_{index + 1}",
                messages=self._messages(prefix),
                stream=True,
                max_tokens=self.settings.max_tokens,
                extra={"stream_options": {"include_usage": True}},
            )
            await self.context.record(record)
            rounds.append({"warmup": False, "record": record})
        return rounds

    @staticmethod
    def _round(item: dict[str, Any]) -> dict[str, Any]:
        record = item["record"]
        usage = extract_cache_usage(record.response.get("usage"))
        cache_read = usage["cache_read_tokens"] or 0
        total_prompt = usage["prompt_tokens"] or 0
        hit = cache_read > 0
        return {
            "warmup": item["warmup"],
            "hit": hit,
            "http_status": record.status_code,
            "usage": {
                "input": total_prompt,
                "output": usage["completion_tokens"],
                "cache_read": cache_read,
                "cache_write": usage["cache_write_tokens"],
                "total_prompt": total_prompt,
                "field_present": usage["field_present"],
            },
            "e2e_ms": record.e2e_ms,
            "ttfb_ms": record.ttfb_ms,
            "ttft_ms": record.ttft_ms,
            "error": record.error,
        }

    def aggregate(self, raw_result: list[dict[str, Any]]) -> dict[str, Any]:
        rounds = [self._round(item) for item in raw_result]
        measured = [round_ for round_ in rounds if not round_["warmup"]]
        hit_count = sum(round_["hit"] for round_ in measured)
        total_cache_read = sum(round_["usage"]["cache_read"] for round_ in measured)
        total_prompt = sum(round_["usage"]["total_prompt"] for round_ in measured)
        hit_rate = hit_count / len(measured) if measured else 0.0
        coverage = total_cache_read / total_prompt if total_prompt else 0.0
        return {
            "rounds": rounds,
            "measured": len(measured),
            "hit_count": hit_count,
            "hit_rate": hit_rate,
            "coverage": coverage,
            "saved_tokens": total_cache_read,
        }
