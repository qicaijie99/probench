from __future__ import annotations

import asyncio
import hashlib
import re
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin


class IdentityProbe(BaseModel):
    id: str
    prompt: str
    max_tokens: int = Field(default=512, gt=0)

    model_config = ConfigDict(extra="forbid")


def _default_probes() -> list[IdentityProbe]:
    return [
        IdentityProbe(id="arithmetic", prompt="Only output the result of 37 * 19."),
        IdentityProbe(id="logic", prompt="Only answer YES or NO: If all dax are wugs and no wugs are red, can any dax be red?"),
        IdentityProbe(id="chinese", prompt="用恰好四个汉字描述春天。"),
        IdentityProbe(id="format", prompt="Output exactly three comma-separated prime numbers, with no other text."),
        IdentityProbe(id="code", prompt="Return only a Python expression that reverses the string variable s."),
    ]


class ModelIdentitySettings(PluginSettings):
    probes: list[IdentityProbe] = Field(default_factory=_default_probes)
    repetitions: int = Field(default=2, gt=0, le=10)
    concurrency: int = Field(default=4, gt=0, le=32)
    seed: int | None = 7319
    expected_model_patterns: list[str] = Field(default_factory=list)
    expected_system_fingerprints: list[str] = Field(default_factory=list)
    baseline_responses: dict[str, str] = Field(default_factory=dict)
    similarity_threshold: float = Field(default=0.75, ge=0, le=1)
    consistency_threshold: float = Field(default=0.85, ge=0, le=1)

    @model_validator(mode="after")
    def validate_probes(self) -> ModelIdentitySettings:
        if not self.probes:
            raise ValueError("model_identity requires at least one probe")
        for pattern in self.expected_model_patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid expected model regex {pattern!r}: {exc}") from exc
        return self


def _normalize(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        body = [line for line in lines[1:-1] if line and not line.strip().startswith("```")]
        text = "\n".join(body).strip()
    return " ".join(text.casefold().split())


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalize(left), _normalize(right)).ratio()


@register_plugin
class ModelIdentityPlugin(BenchmarkPlugin[ModelIdentitySettings]):
    name = "model_identity"
    description = "Reported-model, fingerprint, deterministic probe and behavior-drift detection"
    settings_model = ModelIdentitySettings

    async def _one(
        self, probe: IdentityProbe, repetition: int, semaphore: asyncio.Semaphore
    ) -> dict[str, Any]:
        async with semaphore:
            record = await self.context.provider.chat(
                case_id=f"model_identity.{probe.id}.{repetition + 1}",
                messages=[{"role": "user", "content": probe.prompt}],
                stream=False,
                max_tokens=probe.max_tokens,
                seed=self.settings.seed,
            )
            await self.context.record(record)
        content = str(record.response.get("content") or "")
        return {
            "probe_id": probe.id,
            "repetition": repetition + 1,
            "request_id": record.request_id,
            "status": record.status,
            "reported_model": record.response.get("model"),
            "system_fingerprint": record.response.get("system_fingerprint"),
            "response": content,
            "response_hash": hashlib.sha256(_normalize(content).encode()).hexdigest(),
            "baseline_similarity": (
                _similarity(content, self.settings.baseline_responses[probe.id])
                if probe.id in self.settings.baseline_responses
                else None
            ),
            "error": record.error,
        }

    async def run(self) -> list[dict[str, Any]]:
        if not self.settings.probes:
            raise ValueError("model_identity requires at least one probe")
        semaphore = asyncio.Semaphore(self.settings.concurrency)
        return await asyncio.gather(
            *(
                self._one(probe, repetition, semaphore)
                for probe in self.settings.probes
                for repetition in range(self.settings.repetitions)
            )
        )

    def aggregate(self, raw_result: list[dict[str, Any]]) -> dict[str, Any]:
        successful = [item for item in raw_result if item["status"] == "success"]
        models = sorted({item["reported_model"] for item in successful if item["reported_model"]})
        fingerprints = sorted(
            {item["system_fingerprint"] for item in successful if item["system_fingerprint"]}
        )
        model_match = (
            bool(models)
            and all(
                any(re.search(pattern, model) for pattern in self.settings.expected_model_patterns)
                for model in models
            )
            if self.settings.expected_model_patterns
            else None
        )
        fingerprint_match = (
            bool(fingerprints)
            and all(value in self.settings.expected_system_fingerprints for value in fingerprints)
            if self.settings.expected_system_fingerprints
            else None
        )
        baseline_values = [
            item["baseline_similarity"]
            for item in successful
            if item["baseline_similarity"] is not None
        ]
        baseline_similarity = (
            sum(baseline_values) / len(baseline_values) if baseline_values else None
        )
        consistency_values: list[float] = []
        for probe in self.settings.probes:
            responses = [
                item["response"] for item in successful if item["probe_id"] == probe.id
            ]
            consistency_values.extend(_similarity(a, b) for a, b in combinations(responses, 2))
        consistency = (
            sum(consistency_values) / len(consistency_values) if consistency_values else 1.0
        )
        reasons = []
        if model_match is False:
            reasons.append("reported model does not match expected patterns")
        if fingerprint_match is False:
            reasons.append("system fingerprint is outside the expected set")
        if baseline_similarity is not None and baseline_similarity < self.settings.similarity_threshold:
            reasons.append("probe behavior differs from baseline")
        if consistency < self.settings.consistency_threshold:
            reasons.append("deterministic probe responses are inconsistent")
        if len(successful) != len(raw_result):
            reasons.append("one or more identity probes failed")
        identity_score = (
            (len(successful) / len(raw_result) if raw_result else 0.0)
            * consistency
            * (baseline_similarity if baseline_similarity is not None else 1.0)
        )
        if model_match is False or fingerprint_match is False:
            identity_score = 0.0
        identity_status = (
            "FAIL"
            if model_match is False or fingerprint_match is False
            else ("WARN" if reasons else "PASS")
        )
        return {
            "requests": len(raw_result),
            "success_rate": len(successful) / len(raw_result) if raw_result else 0.0,
            "reported_models": models,
            "system_fingerprints": fingerprints,
            "model_match": model_match,
            "fingerprint_match": fingerprint_match,
            "baseline_similarity": baseline_similarity,
            "behavior_consistency": consistency,
            "identity_score": identity_score,
            "similarity_threshold": self.settings.similarity_threshold,
            "identity_status": identity_status,
            "reasons": reasons,
            "results": raw_result,
        }
