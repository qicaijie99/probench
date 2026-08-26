from __future__ import annotations

import json

import httpx

from provider_bench.models import ProviderConfig
from provider_bench.providers.openai import OpenAICompatibleProvider


async def test_openai_provider_parses_stream_and_usage() -> None:
    events = [
        {"model": "model-a", "system_fingerprint": "fp_123", "choices": [{"delta": {"content": "hello "}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}},
    ]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    provider = OpenAICompatibleProvider(
        ProviderConfig(
            name="mock",
            base_url="https://mock.test/v1",
            api_key="secret",
            model="model-a",
        ),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.chat(
        case_id="stream", messages=[{"role": "user", "content": "hi"}], stream=True
    )
    await provider.close()

    assert result.status == "success"
    assert result.response["content"] == "hello world"
    assert result.tokens == 2
    assert result.usage and result.usage.total_tokens == 5
    assert result.ttft_ms is not None
    assert result.response["model"] == "model-a"
    assert result.response["system_fingerprint"] == "fp_123"


async def test_openai_provider_classifies_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "slow down secret"})

    provider = OpenAICompatibleProvider(
        ProviderConfig(
            name="mock",
            base_url="https://mock.test/v1",
            api_key="secret",
            model="model-a",
        ),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.chat(case_id="limited", messages=[], stream=False)
    await provider.close()

    assert result.status == "rate_limited"
    assert result.status_code == 429
    assert "secret" not in (result.error or "")
    assert "**********" in (result.error or "")


async def test_openai_provider_stream_error_surfaces_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"error": {"code": "model_not_found", "message": "no channel for secret"}},
            headers={"content-type": "text/event-stream"},
        )

    provider = OpenAICompatibleProvider(
        ProviderConfig(
            name="mock",
            base_url="https://mock.test/v1",
            api_key="secret",
            model="model-a",
        ),
        transport=httpx.MockTransport(handler),
    )
    result = await provider.chat(
        case_id="stream-error", messages=[{"role": "user", "content": "hi"}], stream=True
    )
    await provider.close()

    assert result.status == "server_error"
    assert result.status_code == 503
    assert "model_not_found" in (result.error or "")
    assert "ResponseNotRead" not in (result.error or "")


async def test_openai_provider_default_temperature_applies_and_explicit_wins() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )

    provider = OpenAICompatibleProvider(
        ProviderConfig(
            name="mock",
            base_url="https://mock.test/v1",
            api_key="secret",
            model="model-a",
            default_temperature=1,
        ),
        transport=httpx.MockTransport(handler),
    )
    await provider.chat(case_id="default", messages=[], stream=False)
    await provider.chat(case_id="explicit", messages=[], stream=False, temperature=0.5)
    await provider.chat(case_id="omitted", messages=[], stream=False, omit_temperature=True)
    await provider.close()

    assert seen[0]["temperature"] == 1
    assert seen[1]["temperature"] == 0.5
    assert "temperature" not in seen[2]
