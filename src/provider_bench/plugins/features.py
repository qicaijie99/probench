from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from provider_bench.models import RequestRecord
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin


class ParamCase(BaseModel):
    id: str
    param: str
    value: Any = None
    expect: Literal["accept", "reject"] = "accept"

    model_config = ConfigDict(extra="forbid")


def _default_param_cases() -> list[ParamCase]:
    fixed: list[tuple[str, str, Any, Literal["accept", "reject"]]] = [
        ("omit_sampling", "__omit__", None, "accept"),
        ("fixed_temperature", "temperature", 1.0, "accept"),
        ("fixed_top_p", "top_p", 0.95, "accept"),
        ("fixed_n", "n", 1, "accept"),
        ("fixed_presence_penalty", "presence_penalty", 0.0, "accept"),
        ("fixed_frequency_penalty", "frequency_penalty", 0.0, "accept"),
    ]
    reject: list[tuple[str, str, Any]] = [
        ("reject_temperature_1_1", "temperature", 1.1),
        ("reject_temperature_2_0", "temperature", 2.0),
        ("reject_temperature_neg", "temperature", -0.1),
        ("reject_temperature_0_6", "temperature", 0.6),
        ("reject_top_p_0_8", "top_p", 0.8),
        ("reject_presence_penalty_0_5", "presence_penalty", 0.5),
        ("reject_frequency_penalty_0_5", "frequency_penalty", 0.5),
    ]
    cases = [
        ParamCase(id=case_id, param=param, value=value, expect=expect)
        for case_id, param, value, expect in fixed
    ]
    cases += [
        ParamCase(id=case_id, param=param, value=value, expect="reject")
        for case_id, param, value in reject
    ]
    return cases


_THINKING_VARIANTS: list[tuple[str, dict[str, Any]]] = [
    ("enable_thinking_true", {"enable_thinking": True}),
    ("enable_thinking_false", {"enable_thinking": False}),
    ("thinking_type_enabled", {"thinking": {"type": "enabled"}}),
    ("thinking_type_disabled", {"thinking": {"type": "disabled"}}),
    ("chat_template_kwargs_enable_thinking", {"chat_template_kwargs": {"enable_thinking": True}}),
    ("chat_template_kwargs_thinking", {"chat_template_kwargs": {"thinking": True}}),
    ("chat_template_kwargs_thinking_off", {"chat_template_kwargs": {"thinking": False}}),
]


class FeaturesSettings(PluginSettings):
    reasoning_effort_levels: list[str] = Field(default_factory=lambda: ["low", "high", "max"])
    reasoning_prompt: str = "用两步说明 1+1 为什么等于 2。"
    expect_reasoning_by_default: bool = True
    thinking_variants: list[str] = Field(
        default_factory=lambda: [key for key, _ in _THINKING_VARIANTS]
    )
    param_cases: list[ParamCase] = Field(default_factory=_default_param_cases)
    param_prompt: str = "Reply with exactly: ok"
    max_tokens: int = Field(default=64, gt=0)

    @model_validator(mode="after")
    def validate_levels(self) -> FeaturesSettings:
        if not self.reasoning_effort_levels:
            raise ValueError("reasoning_effort_levels must not be empty")
        return self


@register_plugin
class FeaturesPlugin(BenchmarkPlugin[FeaturesSettings]):
    name = "features"
    description = "Thinking switch, reasoning_effort levels and sampling-parameter constraints"
    settings_model = FeaturesSettings

    async def _reasoning_effort(self) -> list[dict[str, Any]]:
        results = []
        for level in self.settings.reasoning_effort_levels:
            record = await self.context.provider.chat(
                case_id=f"features.reasoning_effort.{level}",
                messages=[{"role": "user", "content": self.settings.reasoning_prompt}],
                stream=False,
                max_tokens=self.settings.max_tokens,
                temperature=0,
                extra={"reasoning_effort": level},
            )
            await self.context.record(record)
            reasoning_tokens = record.usage.reasoning_tokens if record.usage else None
            results.append(
                {
                    "case_id": f"reasoning_effort_{level}",
                    "request_id": record.request_id,
                    "level": level,
                    "passed": record.status == "success",
                    "http_status": record.status_code,
                    "reasoning_tokens": reasoning_tokens,
                    "has_reasoning": bool(record.response.get("reasoning_content")) or bool(reasoning_tokens),
                    "e2e_ms": record.e2e_ms,
                    "error": record.error,
                }
            )
        return results

    async def _thinking(self) -> list[dict[str, Any]]:
        results = []
        default = await self.context.provider.chat(
            case_id="features.thinking_default",
            messages=[{"role": "user", "content": self.settings.reasoning_prompt}],
            stream=False,
            max_tokens=self.settings.max_tokens,
            temperature=0,
        )
        await self.context.record(default)
        default_reasoning = bool(default.response.get("reasoning_content")) or bool(
            default.usage.reasoning_tokens if default.usage else None
        )
        default_passed = (
            default.status == "success"
            and default_reasoning == self.settings.expect_reasoning_by_default
        )
        results.append(
            {
                "case_id": "thinking_default",
                "request_id": default.request_id,
                "variant": "default",
                "passed": default_passed,
                "http_status": default.status_code,
                "has_reasoning": default_reasoning,
                "reasoning_tokens": default.usage.reasoning_tokens if default.usage else None,
                "e2e_ms": default.e2e_ms,
                "error": default.error,
            }
        )
        variants = {key: body for key, body in _THINKING_VARIANTS}
        for key in self.settings.thinking_variants:
            body = variants.get(key)
            if body is None:
                results.append(
                    {
                        "case_id": f"thinking_{key}",
                        "request_id": None,
                        "variant": key,
                        "passed": False,
                        "http_status": None,
                        "has_reasoning": False,
                        "reasoning_tokens": None,
                        "e2e_ms": None,
                        "error": f"unknown thinking variant: {key}",
                    }
                )
                continue
            record = await self.context.provider.chat(
                case_id=f"features.thinking_{key}",
                messages=[{"role": "user", "content": self.settings.reasoning_prompt}],
                stream=False,
                max_tokens=self.settings.max_tokens,
                temperature=0,
                extra=body,
            )
            await self.context.record(record)
            has_reasoning = bool(record.response.get("reasoning_content")) or bool(
                record.usage.reasoning_tokens if record.usage else None
            )
            results.append(
                {
                    "case_id": f"thinking_{key}",
                    "request_id": record.request_id,
                    "variant": key,
                    "passed": record.status == "success",
                    "http_status": record.status_code,
                    "has_reasoning": has_reasoning,
                    "reasoning_tokens": record.usage.reasoning_tokens if record.usage else None,
                    "e2e_ms": record.e2e_ms,
                    "error": record.error,
                }
            )
        return results

    async def _param_constraints(self) -> list[dict[str, Any]]:
        results = []
        for case in self.settings.param_cases:
            extra: dict[str, Any] | None = None
            omit_temperature = case.param == "__omit__"
            if case.param != "__omit__":
                extra = {case.param: case.value}
            record = await self.context.provider.chat(
                case_id=f"features.param.{case.id}",
                messages=[{"role": "user", "content": self.settings.param_prompt}],
                stream=False,
                max_tokens=32,
                extra=extra,
                omit_temperature=omit_temperature,
            )
            await self.context.record(record)
            if case.expect == "reject":
                passed = record.status != "success"
                note = (
                    "参数被拒绝"
                    if passed
                    else f"应拒绝但请求成功 · {case.param}={case.value}"
                )
            else:
                passed = record.status == "success"
                note = "参数被接受" if passed else f"应接受但请求失败 · {case.param}={case.value}"
            results.append(
                {
                    "case_id": f"param_{case.id}",
                    "request_id": record.request_id,
                    "param": case.param,
                    "value": case.value,
                    "expect": case.expect,
                    "passed": passed,
                    "http_status": record.status_code,
                    "e2e_ms": record.e2e_ms,
                    "note": note,
                    "error": record.error,
                }
            )
        return results

    async def run(self) -> dict[str, Any]:
        return {
            "reasoning_effort": await self._reasoning_effort(),
            "thinking": await self._thinking(),
            "param_constraints": await self._param_constraints(),
        }

    @staticmethod
    def _summary(results: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(results)
        passed = sum(bool(item["passed"]) for item in results)
        return {
            "total": total,
            "passed": passed,
            "success_rate": passed / total if total else 0.0,
        }

    def aggregate(self, raw_result: dict[str, Any]) -> dict[str, Any]:
        reasoning = raw_result["reasoning_effort"]
        thinking = raw_result["thinking"]
        params = raw_result["param_constraints"]
        reasoning_tokens = [item["reasoning_tokens"] for item in reasoning if item["reasoning_tokens"]]
        distinguishable = (
            len(reasoning) >= 2
            and reasoning_tokens
            and max(reasoning_tokens) > min(reasoning_tokens)
        )
        return {
            "reasoning_effort": self._summary(reasoning),
            "reasoning_effort_results": reasoning,
            "reasoning_effort_distinguishable": distinguishable,
            "thinking": self._summary(thinking),
            "thinking_results": thinking,
            "param_constraints": self._summary(params),
            "param_constraint_results": params,
        }
