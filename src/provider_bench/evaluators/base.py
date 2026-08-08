from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from provider_bench.plugins.base import PluginContext


class EvaluationResult(BaseModel):
    passed: bool
    score: float = Field(ge=0, le=1)
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


@dataclass
class EvaluatorContext:
    plugin: PluginContext

