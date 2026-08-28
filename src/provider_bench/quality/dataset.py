from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class QualityCase(BaseModel):
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    evaluator: str = Field(
        pattern=r"^(exact_match|contains|numeric|json_validator|code_test|llm_judge)$"
    )
    expected: Any = None
    tolerance: float = Field(default=0.0, ge=0)
    json_schema: dict[str, Any] | None = None
    code_tests: list[str] = Field(default_factory=list)
    code_timeout_seconds: float = Field(default=10, gt=0, le=30)
    code_memory_mb: int = Field(default=256, ge=64, le=2048)
    rubric: str | None = None
    pass_threshold: float = Field(default=0.8, ge=0, le=1)
    system_prompt: str | None = None
    max_tokens: int = Field(default=256, gt=0)
    tags: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def evaluator_requirements(self) -> QualityCase:
        if self.evaluator in {"exact_match", "numeric"} and self.expected is None:
            raise ValueError(f"{self.evaluator} requires expected")
        if self.evaluator == "code_test" and not self.code_tests:
            raise ValueError("code_test requires code_tests")
        if self.evaluator == "llm_judge" and not self.rubric:
            raise ValueError("llm_judge requires rubric")
        return self


def _read_dataset(reference: str) -> list[dict[str, Any]]:
    if reference.startswith("builtin:"):
        name = reference.split(":", 1)[1]
        content = files("provider_bench.datasets").joinpath(f"{name}.yaml").read_text(
            encoding="utf-8"
        )
    else:
        content = Path(reference).read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(content) or []
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in quality dataset {reference!r}: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"quality dataset {reference!r} must contain a list")
    return raw


def load_quality_cases(
    references: list[str], inline_cases: list[QualityCase] | None = None
) -> list[QualityCase]:
    cases = [
        QualityCase.model_validate(item)
        for reference in references
        for item in _read_dataset(reference)
    ]
    cases.extend(inline_cases or [])
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("quality case IDs must be unique")
    return cases
