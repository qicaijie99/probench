from __future__ import annotations

from typing import Any

from pydantic import Field

from provider_bench.assets import image_content, video_content
from provider_bench.models import RequestRecord
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin


class ProtocolSettings(PluginSettings):
    checks: list[str] = Field(
        default_factory=lambda: [
            "ping",
            "stream_integrity",
            "usage_stream",
            "image_base64",
            "video_base64",
        ]
    )
    max_tokens: int = Field(default=64, gt=0)
    image_prompt: str = "Describe this image in one sentence."
    video_prompt: str = "Describe this video frame in one sentence."


@register_plugin
class ProtocolPlugin(BenchmarkPlugin[ProtocolSettings]):
    name = "protocol"
    description = "Liveness, stream integrity, stream usage and multimodal (image/video) checks"
    settings_model = ProtocolSettings

    async def _chat(
        self,
        case_id: str,
        messages: list[dict[str, Any]],
        *,
        stream: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> RequestRecord:
        record = await self.context.provider.chat(
            case_id=case_id,
            messages=messages,
            stream=stream,
            max_tokens=self.settings.max_tokens,
            extra=extra,
        )
        await self.context.record(record)
        return record

    async def run(self) -> dict[str, dict[str, Any]]:
        enabled = set(self.settings.checks)
        checks: dict[str, dict[str, Any]] = {}

        if "ping" in enabled:
            record = await self._chat(
                "protocol.ping",
                [{"role": "user", "content": "Reply with exactly: pong"}],
            )
            checks["ping"] = self._result(record, record.status == "success")

        if "stream_integrity" in enabled:
            record = await self._chat(
                "protocol.stream_integrity",
                [{"role": "user", "content": "Count from one to five, one number per line."}],
                stream=True,
            )
            chunk_count = record.response.get("chunk_count", 0)
            saw_done = bool(record.response.get("saw_done"))
            content = record.response.get("content", "")
            reasoning = record.response.get("reasoning_content", "")
            passed = (
                record.status == "success"
                and chunk_count >= 2
                and saw_done
                and bool(content or reasoning)
            )
            checks["stream_integrity"] = self._result(
                record,
                passed,
                evidence={
                    "chunk_count": chunk_count,
                    "saw_done": saw_done,
                    "content_length": len(content),
                    "reasoning_length": len(reasoning),
                },
            )

        if "usage_stream" in enabled:
            record = await self._chat(
                "protocol.usage_stream",
                [{"role": "user", "content": "Count from one to three."}],
                stream=True,
                extra={"stream_options": {"include_usage": True}},
            )
            raw_usage = record.response.get("usage")
            passed = record.status == "success" and isinstance(raw_usage, dict) and bool(raw_usage)
            checks["usage_stream"] = self._result(
                record,
                passed,
                evidence={"usage": raw_usage},
            )

        if "image_base64" in enabled:
            record = await self._chat(
                "protocol.image_base64",
                [{"role": "user", "content": image_content(self.settings.image_prompt)}],
            )
            checks["image_base64"] = self._result(
                record, record.status == "success" and bool(record.response.get("content"))
            )

        if "video_base64" in enabled:
            record = await self._chat(
                "protocol.video_base64",
                [{"role": "user", "content": video_content(self.settings.video_prompt)}],
            )
            checks["video_base64"] = self._result(
                record, record.status == "success" and bool(record.response.get("content"))
            )

        return checks

    @staticmethod
    def _result(
        record: RequestRecord,
        passed: bool,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "passed": passed,
            "request_id": record.request_id,
            "http_status": record.status_code,
            "e2e_ms": record.e2e_ms,
            "ttfb_ms": record.ttfb_ms,
            "ttft_ms": record.ttft_ms,
            "error": record.error,
            "evidence": evidence or {},
        }

    def aggregate(self, raw_result: dict[str, dict[str, Any]]) -> dict[str, Any]:
        passed = sum(check["passed"] for check in raw_result.values())
        total = len(raw_result)
        return {
            "checks": raw_result,
            "passed": passed,
            "total": total,
            "success_rate": passed / total if total else 0.0,
        }
