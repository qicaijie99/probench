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
    max_tokens: int = Field(default=512, gt=0)
    branches: list[str] = Field(
        default_factory=lambda: ["default", "auto", "required", "none", "function", "allowed_tools"]
    )


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

    async def _branch_tools(self) -> list[dict[str, Any]]:
        def function_tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            }

        city = {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
            "additionalProperties": False,
        }
        return [
            function_tool("get_weather", "查询城市天气", city),
            function_tool("get_time", "查询城市时间", city),
        ]

    async def _run_branch(self, branch: str) -> dict[str, Any]:
        tools = await self._branch_tools()
        tool_choice: str | dict[str, Any] | list[str] | None = {
            "default": None,
            "auto": "auto",
            "required": "required",
            "none": "none",
            "function": {"type": "function", "function": {"name": "get_weather"}},
            "allowed_tools": ["get_weather"],
        }.get(branch)
        record = await self.context.provider.chat(
            case_id=f"tool_calling.branch.{branch}",
            messages=[{"role": "user", "content": "北京今天天气怎么样？请按需使用工具。"}],
            stream=False,
            max_tokens=self.settings.max_tokens,
            temperature=0,
            tools=tools,
            tool_choice=tool_choice,
        )
        await self.context.record(record)
        calls = record.response.get("tool_calls") or []
        names = [
            (call_.get("function") or {}).get("name")
            for call_ in calls
            if isinstance(call_, dict)
        ]
        names = [name for name in names if name]
        http_ok = record.status == "success"
        has_tool_calls = bool(names)
        allowed = {"get_weather"}
        branch_ok = {
            "default": http_ok,
            "auto": http_ok,
            "required": http_ok and has_tool_calls,
            "none": http_ok and not has_tool_calls,
            "function": http_ok and names[:1] == ["get_weather"],
            "allowed_tools": http_ok and (not has_tool_calls or all(name in allowed for name in names)),
        }.get(branch, http_ok)
        return {
            "branch": branch,
            "request_id": record.request_id,
            "passed": bool(branch_ok),
            "http_ok": http_ok,
            "has_tool_calls": has_tool_calls,
            "tool_call_names": names,
            "http_status": record.status_code,
            "e2e_ms": record.e2e_ms,
            "error": record.error,
        }

    async def run(self) -> dict[str, Any]:
        if not self.settings.cases:
            raise ValueError("tool_calling requires at least one case")
        semaphore = asyncio.Semaphore(self.settings.concurrency)
        case_results = await asyncio.gather(
            *(self._run_case(case, semaphore) for case in self.settings.cases)
        )
        branch_results = [await self._run_branch(branch) for branch in self.settings.branches]
        return {"cases": case_results, "branches": branch_results}

    def aggregate(self, raw_result: dict[str, Any]) -> dict[str, Any]:
        case_results = raw_result["cases"]
        branch_results = raw_result["branches"]
        total = len(case_results)

        def rate(key: str) -> float:
            return sum(bool(result[key]) for result in case_results) / total if total else 0.0

        branch_summary = {}
        for branch in branch_results:
            branch_summary[branch["branch"]] = {
                "passed": branch["passed"],
                "http_ok": branch["http_ok"],
                "has_tool_calls": branch["has_tool_calls"],
                "tool_call_names": branch["tool_call_names"],
            }
        branches_passed = sum(branch["passed"] for branch in branch_results)
        branches_total = len(branch_results)
        return {
            "cases": total,
            "passed": sum(result["passed"] for result in case_results),
            "success_rate": rate("passed"),
            "tool_selection_rate": rate("selection_ok"),
            "arguments_accuracy": rate("arguments_ok"),
            "json_valid_rate": rate("json_valid"),
            "schema_compliance_rate": rate("schema_compliant"),
            "branches": branch_summary,
            "branches_passed": branches_passed,
            "branches_total": branches_total,
            "branch_results": branch_results,
            "results": case_results,
        }
