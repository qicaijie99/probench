from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from provider_bench.models import RequestRecord


class Provider(ABC):
    name: str
    model: str

    @abstractmethod
    async def list_models(self, case_id: str = "models") -> RequestRecord:
        raise NotImplementedError

    @abstractmethod
    async def chat(
        self,
        *,
        case_id: str,
        messages: list[dict[str, Any]],
        stream: bool = True,
        max_tokens: int = 128,
        temperature: float = 0,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | list[str] | None = None,
        response_format: dict[str, Any] | None = None,
        seed: int | None = None,
        extra: dict[str, Any] | None = None,
        omit_temperature: bool = False,
    ) -> RequestRecord:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
