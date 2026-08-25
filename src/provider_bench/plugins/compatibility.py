from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from provider_bench.models import RequestRecord
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin


class CompatibilitySettings(PluginSettings):
    checks: list[str] = Field(
        default_factory=lambda: [
            "models",
            "non_streaming",
            "streaming",
            "system_message",
            "multi_turn",
            "usage",
            "finish_reason",
            "tool_calling",
            "json_output",
        ]
    )
    max_tokens: int = Field(default=512, gt=0)


@register_plugin
class CompatibilityPlugin(BenchmarkPlugin[CompatibilitySettings]):
    name = "compatibility"
    description = "OpenAI API surface and response-field compatibility checks"
    settings_model = CompatibilitySettings

    async def _chat(
        self,
        case_id: str,
        messages: list[dict[str, Any]],
        *,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> RequestRecord:
        record = await self.context.provider.chat(
            case_id=case_id,
            messages=messages,
            stream=stream,
            max_tokens=self.settings.max_tokens,
            tools=tools,
            response_format=response_format,
        )
        await self.context.record(record)
        return record

    async def run(self) -> dict[str, dict[str, Any]]:
        enabled = set(self.settings.checks)
        checks: dict[str, dict[str, Any]] = {}

        if "models" in enabled:
            record = await self.context.provider.list_models("compatibility.models")
            await self.context.record(record)
            model_ids = [item.get("id") for item in record.response.get("data", [])]
            checks["models"] = self._check(record, self.context.provider.model in model_ids)

        if "non_streaming" in enabled or {"usage", "finish_reason"} & enabled:
            record = await self._chat(
                "compatibility.non_streaming",
                [{"role": "user", "content": "Reply with exactly: pong"}],
            )
            if "non_streaming" in enabled:
                checks["non_streaming"] = self._check(record, bool(record.response.get("content")))
            if "usage" in enabled:
                checks["usage"] = self._check(record, record.usage is not None)
            if "finish_reason" in enabled:
                checks["finish_reason"] = self._check(
                    record, bool(record.response.get("finish_reason"))
                )

        if "streaming" in enabled:
            record = await self._chat(
                "compatibility.streaming",
                [{"role": "user", "content": "Count from one to three."}],
                stream=True,
            )
            checks["streaming"] = self._check(
                record, record.first_token_time is not None and bool(record.response.get("content"))
            )

        if "system_message" in enabled:
            record = await self._chat(
                "compatibility.system_message",
                [
                    {"role": "system", "content": "Answer every question with SYSTEM_OK."},
                    {"role": "user", "content": "Are you ready?"},
                ],
            )
            checks["system_message"] = self._check(
                record, "SYSTEM_OK" in record.response.get("content", "")
            )

        if "multi_turn" in enabled:
            record = await self._chat(
                "compatibility.multi_turn",
                [
                    {"role": "user", "content": "Remember the code 7319."},
                    {"role": "assistant", "content": "I will remember it."},
                    {"role": "user", "content": "What was the code? Reply with digits only."},
                ],
            )
            checks["multi_turn"] = self._check(
                record, "7319" in record.response.get("content", "")
            )

        if "tool_calling" in enabled:
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "description": "Get weather for a city",
                        "parameters": {
                            "type": "object",
                            "properties": {"city": {"type": "string"}},
                            "required": ["city"],
                        },
                    },
                }
            ]
            record = await self._chat(
                "compatibility.tool_calling",
                [{"role": "user", "content": "Use the tool to get weather for Taipei."}],
                tools=tools,
            )
            checks["tool_calling"] = self._check(
                record, bool(record.response.get("tool_calls"))
            )

        if "json_output" in enabled:
            record = await self._chat(
                "compatibility.json_output",
                [{"role": "user", "content": 'Return JSON only: {"ok": true}'}],
                response_format={"type": "json_object"},
            )
            valid_json = False
            try:
                valid_json = isinstance(json.loads(record.response.get("content", "")), dict)
            except (json.JSONDecodeError, TypeError):
                pass
            checks["json_output"] = self._check(record, valid_json)
        return checks

    @staticmethod
    def _check(record: RequestRecord, assertion: bool) -> dict[str, Any]:
        passed = record.status == "success" and assertion
        return {
            "passed": passed,
            "request_id": record.request_id,
            "error": record.error if record.error else (None if assertion else "assertion failed"),
        }

    def aggregate(self, raw_result: dict[str, dict[str, Any]]) -> dict[str, Any]:
        passed = sum(check["passed"] for check in raw_result.values())
        total = len(raw_result)
        return {
            "checks": raw_result,
            "passed": passed,
            "total": total,
            "success_rate": passed / total if total else 0.0,
        }
