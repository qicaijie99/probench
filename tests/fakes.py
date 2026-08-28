from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from provider_bench.models import RequestRecord, Usage
from provider_bench.providers.base import Provider


def record(
    provider: str,
    case_id: str,
    *,
    content: str = "OK",
    tool_calls: list[dict[str, Any]] | None = None,
    ttfr_ms: float | None = None,
    ttfc_ms: float | None = None,
    reasoning_content: str | None = None,
) -> RequestRecord:
    started = datetime.now(UTC)
    return RequestRecord(
        request_id=f"request-{case_id}",
        provider=provider,
        case_id=case_id,
        start_time=started,
        first_token_time=started + timedelta(milliseconds=10),
        end_time=started + timedelta(milliseconds=30),
        ttft_ms=10,
        ttfr_ms=ttfr_ms,
        ttfc_ms=ttfc_ms,
        tpot_ms=2,
        itl_ms=[2, 2],
        e2e_ms=30,
        tps=100,
        tokens=3,
        usage=Usage(prompt_tokens=4, completion_tokens=3, total_tokens=7),
        status="success",
        status_code=200,
        request={"body": {"model": "fake-model"}},
        response={
            "content": content,
            "reasoning_content": reasoning_content or "",
            "finish_reason": "stop",
            "tool_calls": tool_calls or [],
            "model": "fake-model",
            "system_fingerprint": "fp_fake",
        },
    )


class FakeProvider(Provider):
    def __init__(self, name: str = "fake", model: str = "fake-model") -> None:
        self.name = name
        self.model = model
        self.closed = False

    async def list_models(self, case_id: str = "models") -> RequestRecord:
        result = record(self.name, case_id)
        result.response = {"data": [{"id": self.model}]}
        return result

    async def chat(
        self,
        *,
        case_id: str,
        messages: list[dict[str, Any]],
        stream: bool = True,
        max_tokens: int = 128,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | list[str] | None = None,
        response_format: dict[str, Any] | None = None,
        seed: int | None = None,
        extra: dict[str, Any] | None = None,
        omit_temperature: bool = False,
    ) -> RequestRecord:
        content = "OK"
        tool_calls = None
        if case_id == "quality.math-arithmetic-01":
            content = "42"
        elif case_id == "quality.math-arithmetic-02":
            content = "100"
        elif case_id == "quality.math-fraction-01":
            content = "0.5"
        elif case_id == "quality.math-percent-01":
            content = "30"
        elif case_id == "quality.reasoning-logic-01":
            content = "YES"
        elif case_id == "quality.reasoning-arithmetic-word-01":
            content = "4"
        elif case_id == "quality.reasoning-schedule-01":
            content = "9"
        elif case_id == "quality.reasoning-mix-01":
            content = "12"
        elif case_id == "quality.chinese-knowledge-01":
            content = "王勃"
        elif case_id == "quality.chinese-knowledge-02":
            content = "千里共婵娟"
        elif case_id == "quality.chinese-knowledge-03":
            content = "毕昇"
        elif case_id == "quality.chinese-knowledge-04":
            content = "罗贯中"
        elif case_id == "quality.code-python-01":
            content = "def add(a, b):\n    return a + b"
        elif case_id == "quality.code-python-02":
            content = "def is_even(n):\n    return n % 2 == 0"
        elif case_id == "quality.code-python-03":
            content = "def max_of_three(a, b, c):\n    return max(a, b, c)"
        elif case_id == "quality.code-python-04":
            content = "def reverse_string(s):\n    return s[::-1]"
        elif case_id == "quality.instruction-following-01":
            content = "BLUE"
        elif case_id == "quality.instruction-following-02":
            content = "红 绿 蓝"
        elif case_id == "quality.instruction-judge-01":
            content = "记录超时有助于故障诊断。"
        elif case_id == "quality.instruction-judge-02":
            content = "缓存命中可显著降低成本。"
        elif case_id == "quality.json-basic-01":
            content = '{"name":"Ada","age":36}'
        elif case_id == "quality.json-basic-02":
            content = '{"ok":true,"count":3}'
        elif case_id == "quality.json-nested-01":
            content = '{"order_id":"ORD-1","items":[{"sku":"A1","qty":2}]}'
        elif case_id == "quality.json-list-01":
            content = "[1, 2, 3]"
        elif case_id.startswith("quality.judge."):
            content = '{"passed":true,"score":0.95,"reason":"meets rubric"}'
        elif case_id == "tool_calling.selection-weather":
            tool_calls = [{
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city":"Taipei","unit":"celsius"}',
                }
            }]
        elif case_id == "tool_calling.arguments-numeric":
            tool_calls = [{
                "function": {"name": "multiply", "arguments": '{"a":17,"b":6}'}
            }]
        elif case_id == "tool_calling.nested-schema":
            tool_calls = [{
                "function": {
                    "name": "search_catalog",
                    "arguments": '{"query":"keyboard","filter":{"in_stock":true,"max_price":100}}',
                }
            }]
        elif case_id == "structured_output.json-object":
            content = '{"ok":true,"count":3}'
        elif case_id == "structured_output.json-schema":
            content = '{"name":"Ada","age":36,"tags":["pioneer","mathematician"]}'
        elif case_id == "structured_output.nested-structure":
            content = '{"order_id":"ORD-1","items":[{"sku":"KB-1","quantity":2,"price":49.5}]}'
        elif "system_message" in case_id:
            content = "SYSTEM_OK"
        elif "multi_turn" in case_id:
            content = "7319"
        elif "json_output" in case_id:
            content = '{"ok": true}'
        elif "tool_calling" in case_id:
            tool_calls = [{"function": {"name": "get_weather", "arguments": '{"city":"Taipei"}'}}]
        elif "non_streaming" in case_id:
            content = "pong"
        result = record(self.name, case_id, content=content, tool_calls=tool_calls)
        result.response["model"] = self.model
        if extra and extra.get("enable_thinking"):
            result.ttfr_ms = 20
            result.ttfc_ms = 60
            result.response["reasoning_content"] = "Let me reason step by step."
        return result

    async def close(self) -> None:
        self.closed = True
