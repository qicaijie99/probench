from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import ssl

import httpx
import tiktoken

from provider_bench.models import ProviderConfig, RequestRecord, Usage
from provider_bench.providers.base import Provider


def _utc_from_monotonic(start_wall: datetime, start_clock: float, point: float) -> datetime:
    return datetime.fromtimestamp(start_wall.timestamp() + point - start_clock, tz=UTC)


def _usage(data: dict[str, Any] | None) -> Usage | None:
    if not data:
        return None
    prompt_details = data.get("prompt_tokens_details") or {}
    completion_details = data.get("completion_tokens_details") or {}
    cached_tokens = (
        prompt_details.get("cached_tokens")
        if prompt_details.get("cached_tokens") is not None
        else data.get("cached_tokens")
    )
    reasoning_tokens = (
        completion_details.get("reasoning_tokens")
        if completion_details.get("reasoning_tokens") is not None
        else data.get("reasoning_tokens")
    )
    return Usage(
        prompt_tokens=data.get("prompt_tokens"),
        completion_tokens=data.get("completion_tokens"),
        total_tokens=data.get("total_tokens"),
        cached_tokens=cached_tokens,
        reasoning_tokens=reasoning_tokens,
        details=data,
    )


def _is_ssl_error(exc: Exception) -> bool:
    """Detect TLS/SSL failures across the httpx exception cause chain."""
    current: BaseException | None = exc
    for _ in range(6):
        if current is None:
            return False
        if isinstance(current, ssl.SSLError):
            return True
        if isinstance(current, httpx.RequestError) and not isinstance(
            current, httpx.HTTPStatusError
        ):
            text = str(current)
            if "SSL" in text or "TLS" in text or "certificate" in text.lower():
                return True
        current = current.__cause__
    return False


def _error_kind(exc: Exception) -> tuple[str, int | None]:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout", None
    if _is_ssl_error(exc):
        return "ssl_error", None
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return "rate_limited", code
        if code >= 500:
            return "server_error", code
        return "http_error", code
    return "error", None


class OpenAICompatibleProvider(Provider):
    def __init__(
        self,
        config: ProviderConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = config.name
        self.model = config.model
        self._stream_include_usage = config.stream_include_usage
        try:
            self._encoding = tiktoken.encoding_for_model(config.model)
        except KeyError:
            self._encoding = tiktoken.get_encoding("cl100k_base")
        self._secrets = [
            config.api_key.get_secret_value(),
            *(value.get_secret_value() for value in config.headers.values()),
        ]
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/") + "/",
            headers={
                "Authorization": f"Bearer {config.api_key.get_secret_value()}",
                "Content-Type": "application/json",
                **{name: value.get_secret_value() for name, value in config.headers.items()},
            },
            timeout=config.timeout_seconds,
            limits=httpx.Limits(
                max_connections=config.max_connections,
                max_keepalive_connections=config.max_keepalive_connections,
            ),
            transport=transport,
        )

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc)
        if isinstance(exc, httpx.HTTPStatusError):
            body = exc.response.text[:2000]
            if body:
                message = f"{message}; response={body}"
        for secret in self._secrets:
            if secret:
                message = message.replace(secret, "**********")
        return message

    async def list_models(self, case_id: str = "models") -> RequestRecord:
        request_id = str(uuid.uuid4())
        started_wall = datetime.now(UTC)
        started = time.perf_counter()
        response_data: dict[str, Any] = {}
        status, status_code, error = "success", None, None
        try:
            response = await self._client.get("models")
            status_code = response.status_code
            response.raise_for_status()
            response_data = response.json()
        except Exception as exc:
            status, status_code = _error_kind(exc)
            error = self._safe_error(exc)
        ended = time.perf_counter()
        return RequestRecord(
            request_id=request_id,
            provider=self.name,
            case_id=case_id,
            start_time=started_wall,
            end_time=_utc_from_monotonic(started_wall, started, ended),
            e2e_ms=(ended - started) * 1000,
            status=status,
            status_code=status_code,
            error=error,
            request={"method": "GET", "path": "/models"},
            response=response_data,
        )

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
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "max_tokens": max_tokens,
        }
        if not omit_temperature:
            payload["temperature"] = temperature
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if response_format is not None:
            payload["response_format"] = response_format
        if seed is not None:
            payload["seed"] = seed
        if extra:
            payload.update(extra)
        if stream and self._stream_include_usage:
            payload["stream_options"] = {"include_usage": True}

        request_id = str(uuid.uuid4())
        started_wall = datetime.now(UTC)
        started = time.perf_counter()
        first_token: float | None = None
        first_byte: float | None = None
        chunk_count = 0
        saw_done = False
        token_events: list[float] = []
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[Any] = []
        finish_reason: str | None = None
        usage: Usage | None = None
        raw_usage: dict[str, Any] | None = None
        response_data: dict[str, Any] = {}
        response_model: str | None = None
        system_fingerprint: str | None = None
        status, status_code, error = "success", None, None

        try:
            if stream:
                async with self._client.stream("POST", "chat/completions", json=payload) as response:
                    status_code = response.status_code
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError:
                        await response.aread()
                        raise
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        if first_byte is None:
                            first_byte = time.perf_counter()
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        if raw == "[DONE]":
                            saw_done = True
                            continue
                        chunk_count += 1
                        event = json.loads(raw)
                        response_model = event.get("model") or response_model
                        system_fingerprint = event.get("system_fingerprint") or system_fingerprint
                        if event.get("usage"):
                            raw_usage = event["usage"]
                            usage = _usage(raw_usage) or usage
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta") or {}
                        chunk = delta.get("content")
                        reasoning_chunk = delta.get("reasoning_content")
                        if (chunk or reasoning_chunk) and first_token is None:
                            first_token = time.perf_counter()
                        if chunk:
                            now = time.perf_counter()
                            token_events.append(now)
                            content_parts.append(chunk)
                        if reasoning_chunk:
                            reasoning_parts.append(reasoning_chunk)
                        if delta.get("tool_calls"):
                            tool_calls.extend(delta["tool_calls"])
                        finish_reason = choice.get("finish_reason") or finish_reason
                response_data = {
                    "content": "".join(content_parts),
                    "reasoning_content": "".join(reasoning_parts),
                    "finish_reason": finish_reason,
                    "tool_calls": tool_calls,
                    "model": response_model,
                    "system_fingerprint": system_fingerprint,
                    "chunk_count": chunk_count,
                    "saw_done": saw_done,
                }
            else:
                response = await self._client.post("chat/completions", json=payload)
                status_code = response.status_code
                response.raise_for_status()
                body = response.json()
                response_model = body.get("model")
                system_fingerprint = body.get("system_fingerprint")
                raw_usage = body.get("usage")
                usage = _usage(raw_usage)
                choice = (body.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                response_data = {
                    "content": message.get("content") or "",
                    "reasoning_content": message.get("reasoning_content") or "",
                    "finish_reason": choice.get("finish_reason"),
                    "tool_calls": message.get("tool_calls") or [],
                    "model": response_model,
                    "system_fingerprint": system_fingerprint,
                }
        except Exception as exc:
            status, status_code = _error_kind(exc)
            error = self._safe_error(exc)

        ended = time.perf_counter()
        if raw_usage is not None:
            response_data["usage"] = raw_usage
        completion_tokens = usage.completion_tokens if usage else None
        response_content = str(response_data.get("content") or "")
        local_tokens = len(self._encoding.encode(response_content)) if response_content else 0
        estimated_tokens = completion_tokens or local_tokens or len(token_events) or None
        ttfb = (first_byte - started) * 1000 if first_byte is not None else None
        ttft = (first_token - started) * 1000 if first_token is not None else None
        generation_seconds = ended - first_token if first_token is not None else None
        tpot = None
        if generation_seconds is not None and estimated_tokens and estimated_tokens > 1:
            tpot = generation_seconds * 1000 / (estimated_tokens - 1)
        itl = [
            (current - previous) * 1000
            for previous, current in zip(token_events, token_events[1:], strict=False)
        ]
        tps = estimated_tokens / (ended - started) if estimated_tokens and ended > started else None
        return RequestRecord(
            request_id=request_id,
            provider=self.name,
            case_id=case_id,
            start_time=started_wall,
            first_token_time=(
                _utc_from_monotonic(started_wall, started, first_token)
                if first_token is not None
                else None
            ),
            end_time=_utc_from_monotonic(started_wall, started, ended),
            ttfb_ms=ttfb,
            ttft_ms=ttft,
            tpot_ms=tpot,
            itl_ms=itl,
            e2e_ms=(ended - started) * 1000,
            tps=tps,
            tokens=estimated_tokens,
            usage=usage,
            status=status,
            status_code=status_code,
            error=error,
            request={"method": "POST", "path": "/chat/completions", "body": payload},
            response=response_data,
        )

    async def close(self) -> None:
        await self._client.aclose()
