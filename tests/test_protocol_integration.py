from __future__ import annotations

import json
from pathlib import Path

import httpx

from provider_bench.engine import BenchmarkEngine
from provider_bench.models import AppConfig, RunStatus
from provider_bench.providers.openai import OpenAICompatibleProvider


def _openai_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path.endswith("/models"):
        return httpx.Response(200, json={"object": "list", "data": [{"id": "model-a"}]})
    payload = json.loads(request.content)
    messages = payload.get("messages") or []
    content = "pong"
    tool_calls = []
    if payload.get("tools"):
        content = ""
        tool_calls = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Taipei"}'},
            }
        ]
    elif payload.get("response_format"):
        content = '{"ok":true}'
    elif any(message.get("role") == "system" for message in messages):
        content = "SYSTEM_OK"
    elif len(messages) > 2:
        content = "7319"
    elif payload.get("stream"):
        content = "one two three"
    if payload.get("stream"):
        events = [
            {
                "model": "model-a",
                "system_fingerprint": "fp_protocol",
                "choices": [{"delta": {"content": content}, "finish_reason": "stop"}],
            },
            {
                "choices": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        ]
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, text=body + "data: [DONE]\n\n")
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl_mock",
            "model": "model-a",
            "system_fingerprint": "fp_protocol",
            "choices": [
                {
                    "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        },
    )


async def test_engine_to_openai_protocol_to_report(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "provider": {
                "name": "protocol",
                "base_url": "https://protocol.test/v1",
                "api_key": "protocol-secret",
                "model": "model-a",
            },
            "output_dir": str(tmp_path),
            "benchmarks": {
                "compatibility": {"enabled": True},
                "latency": {"enabled": True, "warmup": 0, "repetitions": 2},
            },
        }
    )
    transport = httpx.MockTransport(_openai_handler)
    result = await BenchmarkEngine(
        provider_factory=lambda provider: OpenAICompatibleProvider(
            provider, transport=transport
        )
    ).run(config, run_id="protocol-run")

    assert result.status == RunStatus.COMPLETED
    compatibility = result.providers["protocol"].plugins["compatibility"]
    assert compatibility.metrics["success_rate"] == 1
    assert result.providers["protocol"].plugins["latency"].request_count == 2
    assert "protocol-secret" not in (tmp_path / "protocol-run/run.json").read_text()
    assert (tmp_path / "protocol-run/report.html").is_file()

