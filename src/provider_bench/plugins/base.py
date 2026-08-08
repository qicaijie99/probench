from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar, cast

from pydantic import BaseModel, ConfigDict

from provider_bench.models import RequestRecord
from provider_bench.providers.base import Provider
from provider_bench.storage import append_jsonl

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class PluginSettings(BaseModel):
    enabled: bool = True

    model_config = ConfigDict(extra="forbid")


SettingsT = TypeVar("SettingsT", bound=PluginSettings)


@dataclass
class PluginContext:
    run_id: str
    provider: Provider
    judge_provider: Provider | None
    output_dir: Path
    emit: EventHandler
    records: list[RequestRecord] = field(default_factory=list)

    async def record(self, record: RequestRecord) -> None:
        self.records.append(record)
        append_jsonl(self.output_dir / "requests.jsonl", record)
        await self.emit(
            {
                "type": "request.completed",
                "run_id": self.run_id,
                "provider": self.provider.name,
                "plugin": self.output_dir.name,
                "case_id": record.case_id,
                "status": record.status,
                "e2e_ms": record.e2e_ms,
                "ttft_ms": record.ttft_ms,
            }
        )


class BenchmarkPlugin(ABC, Generic[SettingsT]):
    name: ClassVar[str]
    description: ClassVar[str] = ""
    settings_model: ClassVar[type[PluginSettings]] = PluginSettings

    def __init__(self, config: dict[str, Any], context: PluginContext) -> None:
        self.context = context
        self.settings = cast(SettingsT, self.validate_config(config))

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> PluginSettings:
        return cls.settings_model.model_validate(config)

    async def prepare(self) -> None:
        self.context.output_dir.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    async def run(self) -> Any:
        raise NotImplementedError

    @abstractmethod
    def aggregate(self, raw_result: Any) -> dict[str, Any]:
        raise NotImplementedError

    async def cleanup(self) -> None:
        return None
