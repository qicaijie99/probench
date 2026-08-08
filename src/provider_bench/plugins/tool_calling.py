from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin
from provider_bench.validation import validate_json_schema, validate_schema_definition


class ToolCallingCase(BaseModel):
    id: str
    prompt: str
    tools: list[dict[str, Any]]
    expected_tool: str | None = None
    expected_arguments: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


def _default_cases() -> list[ToolCallingCase]:
    weather = {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["city", "unit"],
                "additionalProperties": False,
            },
        },
    }
    calculator = {
        "type": "function",
        "function": {
            "name": "multiply",
            "description": "Multiply two numbers",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
                "additionalProperties": False,
            },
        },
    }
    search = {
        "type": "function",
        "function": {
            "name": "search_catalog",
            "description": "Search a product catalog with nested filters",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "filter": {
                        "type": "object",
                        "properties": {
                            "in_stock": {"type": "boolean"},
                            "max_price": {"type": "number"},
                        },
                        "required": ["in_stock", "max_price"],
                        "additionalProperties": False,
                    },
                },
                "required": ["query", "filter"],
                "additionalProperties": False,
            },
        },
    }
    return [
        ToolCallingCase(
            id="selection-weather",
            prompt="What is the weather in Taipei? Use the appropriate tool with celsius.",
            tools=[weather, calculator],
            expected_tool="get_weather",
            expected_arguments={"city": "Taipei", "unit": "celsius"},
        ),
        ToolCallingCase(
            id="arguments-numeric",
            prompt="Use a tool to multiply 17 by 6.",
            tools=[weather, calculator],
            expected_tool="multiply",
            expected_arguments={"a": 17, "b": 6},
        ),
        ToolCallingCase(
            id="nested-schema",
            prompt="Search the catalog for keyboard items in stock costing at most 100.",
            tools=[search],
            expected_tool="search_catalog",
            expected_arguments={
                "query": "keyboard",
                "filter": {"in_stock": True, "max_price": 100},
            },
        ),
    ]


class ToolCallingSettings(PluginSettings):
    cases: list[ToolCallingCase] = Field(default_factory=_default_cases)
    concurrency: int = Field(default=3, gt=0, le=32)
    max_tokens: int = Field(default=128, gt=0)


def _expected_subset(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _expected_subset(value, actual[key]) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(
            _expected_subset(left, right) for left, right in zip(expected, actual, strict=True)
        )
    return expected == actual


@register_plugin
class ToolCallingPlugin(BenchmarkPlugin[ToolCallingSettings]):
    name = "tool_calling"
    description = "Tool selection, argument JSON validity and function-schema compliance"
    settings_model = ToolCallingSettings

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> ToolCallingSettings:
        settings = ToolCallingSettings.model_validate(config)
        if not settings.cases:
            raise ValueError("tool_calling requires at least one case")
        for case in settings.cases:
            for tool in case.tools:
                schema = tool.get("function", {}).get("parameters")
                if isinstance(schema, dict):
                    validate_schema_definition(schema)
        return settings

    async def _run_case(
        self, case: ToolCallingCase, semaphore: asyncio.Semaphore
    ) -> dict[str, Any]:
        async with semaphore:
            record = await self.context.provider.chat(
                case_id=f"tool_calling.{case.id}",
                messages=[{"role": "user", "content": case.prompt}],
                stream=False,
                max_tokens=self.settings.max_tokens,
                temperature=0,
                tools=case.tools,
                tool_choice="auto",
            )
            await self.context.record(record)
        calls = record.response.get("tool_calls") or []
        selected_name = None
        arguments: Any = None
        json_valid = False
        if calls:
            function = calls[0].get("function") or {}
            selected_name = function.get("name")
            raw_arguments = function.get("arguments")
            if isinstance(raw_arguments, dict):
                arguments, json_valid = raw_arguments, True
            elif isinstance(raw_arguments, str):
                try:
                    arguments, json_valid = json.loads(raw_arguments), True
                except json.JSONDecodeError:
                    pass
        selection_ok = selected_name == case.expected_tool
        function_schema: dict[str, Any] = next(
            (
                tool.get("function", {}).get("parameters", {})
                for tool in case.tools
                if tool.get("function", {}).get("name") == selected_name
            ),
            {},
        )
        schema_errors = (
            validate_json_schema(arguments, function_schema)
            if json_valid and function_schema
            else (["arguments are not valid JSON"] if not json_valid else [])
        )
        arguments_ok = json_valid and _expected_subset(case.expected_arguments, arguments)
        passed = record.status == "success" and selection_ok and arguments_ok and not schema_errors
        return {
            "case_id": case.id,
            "request_id": record.request_id,
            "passed": passed,
            "selection_ok": selection_ok,
            "arguments_ok": arguments_ok,
            "json_valid": json_valid,
            "schema_compliant": not schema_errors,
            "selected_tool": selected_name,
            "arguments": arguments,
            "schema_errors": schema_errors,
            "error": record.error,
        }

    async def run(self) -> list[dict[str, Any]]:
        if not self.settings.cases:
            raise ValueError("tool_calling requires at least one case")
        semaphore = asyncio.Semaphore(self.settings.concurrency)
        return await asyncio.gather(
            *(self._run_case(case, semaphore) for case in self.settings.cases)
        )

    def aggregate(self, raw_result: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(raw_result)

        def rate(key: str) -> float:
            return sum(bool(result[key]) for result in raw_result) / total if total else 0.0

        return {
            "cases": total,
            "passed": sum(result["passed"] for result in raw_result),
            "success_rate": rate("passed"),
            "tool_selection_rate": rate("selection_ok"),
            "arguments_accuracy": rate("arguments_ok"),
            "json_valid_rate": rate("json_valid"),
            "schema_compliance_rate": rate("schema_compliant"),
            "results": raw_result,
        }
