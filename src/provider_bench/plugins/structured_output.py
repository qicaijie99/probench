from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin
from provider_bench.validation import validate_json_schema, validate_schema_definition


class StructuredCase(BaseModel):
    id: str
    prompt: str
    mode: str = Field(pattern=r"^(json_object|json_schema)$")
    output_schema: dict[str, Any] = Field(alias="schema")
    expected: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _default_cases() -> list[StructuredCase]:
    return [
        StructuredCase(
            id="json-object",
            prompt="Return a JSON object with ok=true and count=3.",
            mode="json_object",
            schema={
                "type": "object",
                "properties": {"ok": {"type": "boolean"}, "count": {"type": "integer"}},
                "required": ["ok", "count"],
                "additionalProperties": False,
            },
            expected={"ok": True, "count": 3},
        ),
        StructuredCase(
            id="json-schema",
            prompt="Return a person named Ada, age 36, with tags pioneer and mathematician.",
            mode="json_schema",
            schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "age": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "age", "tags"],
                "additionalProperties": False,
            },
        ),
        StructuredCase(
            id="nested-structure",
            prompt="Return an order ORD-1 containing one item: sku KB-1, quantity 2, price 49.5.",
            mode="json_schema",
            schema={
                "type": "object",
                "properties": {
                    "order_id": {"type": "string"},
                    "items": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "sku": {"type": "string"},
                                "quantity": {"type": "integer"},
                                "price": {"type": "number"},
                            },
                            "required": ["sku", "quantity", "price"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["order_id", "items"],
                "additionalProperties": False,
            },
        ),
    ]


class StructuredOutputSettings(PluginSettings):
    cases: list[StructuredCase] = Field(default_factory=_default_cases)
    concurrency: int = Field(default=3, gt=0, le=32)
    max_tokens: int = Field(default=256, gt=0)
    strict: bool = True


@register_plugin
class StructuredOutputPlugin(BenchmarkPlugin[StructuredOutputSettings]):
    name = "structured_output"
    description = "JSON Object, JSON Schema and nested structured-output compliance"
    settings_model = StructuredOutputSettings

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> StructuredOutputSettings:
        settings = StructuredOutputSettings.model_validate(config)
        if not settings.cases:
            raise ValueError("structured_output requires at least one case")
        for case in settings.cases:
            validate_schema_definition(case.output_schema)
        return settings

    async def _run_case(
        self, case: StructuredCase, semaphore: asyncio.Semaphore
    ) -> dict[str, Any]:
        if case.mode == "json_object":
            response_format: dict[str, Any] = {"type": "json_object"}
        else:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": case.id.replace("-", "_"),
                    "strict": self.settings.strict,
                    "schema": case.output_schema,
                },
            }
        async with semaphore:
            record = await self.context.provider.chat(
                case_id=f"structured_output.{case.id}",
                messages=[{"role": "user", "content": case.prompt}],
                stream=False,
                max_tokens=self.settings.max_tokens,
                response_format=response_format,
            )
            await self.context.record(record)
        parsed = None
        parse_error = None
        try:
            parsed = json.loads(record.response.get("content", ""))
        except (json.JSONDecodeError, TypeError) as exc:
            parse_error = str(exc)
        schema_errors = (
            validate_json_schema(parsed, case.output_schema) if parse_error is None else []
        )
        expected_ok = case.expected is None or parsed == case.expected
        passed = (
            record.status == "success"
            and parse_error is None
            and not schema_errors
            and expected_ok
        )
        return {
            "case_id": case.id,
            "mode": case.mode,
            "request_id": record.request_id,
            "passed": passed,
            "json_valid": parse_error is None,
            "schema_compliant": parse_error is None and not schema_errors,
            "expected_match": expected_ok,
            "parsed": parsed,
            "parse_error": parse_error,
            "schema_errors": schema_errors,
            "error": record.error,
        }

    async def run(self) -> list[dict[str, Any]]:
        if not self.settings.cases:
            raise ValueError("structured_output requires at least one case")
        semaphore = asyncio.Semaphore(self.settings.concurrency)
        return await asyncio.gather(
            *(self._run_case(case, semaphore) for case in self.settings.cases)
        )

    def aggregate(self, raw_result: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(raw_result)

        def rate(key: str) -> float:
            return sum(bool(item[key]) for item in raw_result) / total if total else 0.0

        modes = {
            mode: {
                "cases": len(items),
                "success_rate": sum(item["passed"] for item in items) / len(items),
            }
            for mode in {item["mode"] for item in raw_result}
            if (items := [item for item in raw_result if item["mode"] == mode])
        }
        return {
            "cases": total,
            "passed": sum(item["passed"] for item in raw_result),
            "success_rate": rate("passed"),
            "json_valid_rate": rate("json_valid"),
            "schema_compliance_rate": rate("schema_compliant"),
            "modes": modes,
            "results": raw_result,
        }
