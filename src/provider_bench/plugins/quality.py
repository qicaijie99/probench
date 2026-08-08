from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from pydantic import Field, model_validator

from provider_bench.evaluators import EvaluatorContext, evaluate_case, evaluator_names
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin
from provider_bench.quality import QualityCase, load_quality_cases
from provider_bench.validation import validate_schema_definition


class QualitySettings(PluginSettings):
    datasets: list[str] = Field(default_factory=lambda: ["builtin:core"])
    cases: list[QualityCase] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)
    evaluators: list[str] = Field(default_factory=list)
    max_cases: int | None = Field(default=None, gt=0)
    concurrency: int = Field(default=4, gt=0, le=64)
    temperature: float = Field(default=0, ge=0, le=2)

    @model_validator(mode="after")
    def validate_evaluators(self) -> QualitySettings:
        unknown = set(self.evaluators) - set(evaluator_names())
        if unknown:
            raise ValueError(f"unknown evaluators: {', '.join(sorted(unknown))}")
        return self


@register_plugin
class QualityPlugin(BenchmarkPlugin[QualitySettings]):
    name = "quality"
    description = "Dataset-driven math, reasoning, Chinese, code, instruction and JSON quality"
    settings_model = QualitySettings

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> QualitySettings:
        settings = QualitySettings.model_validate(config)
        cases = load_quality_cases(settings.datasets, settings.cases)
        if settings.categories:
            cases = [case for case in cases if case.category in settings.categories]
        if settings.evaluators:
            cases = [case for case in cases if case.evaluator in settings.evaluators]
        if not cases:
            raise ValueError("quality selection produced no cases")
        for case in cases:
            if case.json_schema:
                validate_schema_definition(case.json_schema)
        return settings

    async def prepare(self) -> None:
        await super().prepare()
        cases = load_quality_cases(self.settings.datasets, self.settings.cases)
        if self.settings.categories:
            cases = [case for case in cases if case.category in self.settings.categories]
        if self.settings.evaluators:
            cases = [case for case in cases if case.evaluator in self.settings.evaluators]
        self.cases = cases[: self.settings.max_cases] if self.settings.max_cases else cases
        if not self.cases:
            raise ValueError("quality selection produced no cases")

    async def _run_case(
        self, case: QualityCase, semaphore: asyncio.Semaphore
    ) -> dict[str, Any]:
        async with semaphore:
            messages = []
            if case.system_prompt:
                messages.append({"role": "system", "content": case.system_prompt})
            messages.append({"role": "user", "content": case.prompt})
            response_format = {"type": "json_object"} if case.evaluator == "json_validator" else None
            record = await self.context.provider.chat(
                case_id=f"quality.{case.id}",
                messages=messages,
                stream=False,
                max_tokens=case.max_tokens,
                temperature=self.settings.temperature,
                response_format=response_format,
            )
            await self.context.record(record)
            if record.status != "success":
                evaluation = {
                    "passed": False,
                    "score": 0.0,
                    "reason": record.error or record.status,
                    "details": {},
                }
            else:
                evaluated = await evaluate_case(
                    case,
                    str(record.response.get("content") or ""),
                    EvaluatorContext(plugin=self.context),
                )
                evaluation = evaluated.model_dump(mode="json")
            return {
                "case_id": case.id,
                "category": case.category,
                "evaluator": case.evaluator,
                "request_id": record.request_id,
                **evaluation,
            }

    async def run(self) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(self.settings.concurrency)
        regular_cases = [case for case in self.cases if case.evaluator != "code_test"]
        code_cases = [case for case in self.cases if case.evaluator == "code_test"]
        results = list(
            await asyncio.gather(*(self._run_case(case, semaphore) for case in regular_cases))
        )
        # Child-process evaluators run serially to avoid platform child-watcher races.
        for case in code_cases:
            results.append(await self._run_case(case, semaphore))
        order = {case.id: index for index, case in enumerate(self.cases)}
        return sorted(results, key=lambda result: order[result["case_id"]])

    def aggregate(self, raw_result: list[dict[str, Any]]) -> dict[str, Any]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        evaluator_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for result in raw_result:
            groups[result["category"]].append(result)
            evaluator_groups[result["evaluator"]].append(result)

        def summary(items: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "cases": len(items),
                "passed": sum(item["passed"] for item in items),
                "pass_rate": sum(item["passed"] for item in items) / len(items),
                "score": sum(float(item["score"]) for item in items) / len(items),
            }

        overall = summary(raw_result)
        return {
            **overall,
            "success_rate": overall["pass_rate"],
            "categories": {name: summary(items) for name, items in groups.items()},
            "evaluators": {name: summary(items) for name, items in evaluator_groups.items()},
            "results": raw_result,
        }
