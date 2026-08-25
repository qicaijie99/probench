from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from provider_bench.models import AppConfig

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        raise ValueError(f"environment variable {name!r} is not set")

    return _ENV_PATTERN.sub(replace, value)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as stream:
        try:
            raw = yaml.safe_load(stream) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"invalid YAML in {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    expanded = _expand_env(raw)
    quality = (expanded.get("benchmarks") or {}).get("quality")
    if isinstance(quality, dict) and isinstance(quality.get("datasets"), list):
        quality["datasets"] = [
            reference
            if not isinstance(reference, str)
            or reference.startswith("builtin:")
            or Path(reference).is_absolute()
            else str((config_path.parent / reference).resolve())
            for reference in quality["datasets"]
        ]
    return AppConfig.model_validate(expanded)


EXAMPLE_CONFIG = """# API keys are expanded from the environment and never written to reports.
provider:
  name: candidate
  base_url: https://api.example.com/v1
  api_key: ${PROVIDER_API_KEY}
  model: your-model-name

benchmarks:
  compatibility:
    enabled: true
  latency:
    enabled: true
    warmup: 1
    repetitions: 5
    prompt: "用两句话解释什么是 API。"
    max_tokens: 512  # reasoning 模型需足够输出预算，避免思考占满导致 content 为空
  concurrency:
    enabled: false
    levels: [1, 2, 4, 8, 16, 32, 64, 128]
    requests_per_level: 8
  burst:
    enabled: false
    sizes: [10, 25, 50, 100]
  quality:
    enabled: false
    datasets: [builtin:core]
    concurrency: 4
  tool_calling:
    enabled: false
    branches: [default, auto, required, none, function, allowed_tools]
  structured_output:
    enabled: false
  features:
    enabled: false
    reasoning_effort_levels: [low, high, max]
  protocol:
    enabled: false
    checks: [ping, stream_integrity, usage_stream, image_base64, video_base64]
  cache:
    enabled: false
    prefix_chars: 4096
    rounds: 2
  benchmark:
    enabled: false
    sessions: 4
    turns: 3
    init_tokens: 32000
    output_tokens: 346
    max_inflight: 1
    arrival_start: 0.08
    arrival_end: 0.2
    ramp_seconds: 15.0
    baseline_rps_min: 0.6
    baseline_ttft_p50_max_ms: 15000
    baseline_tpot_p50_max_ms: 35
    baseline_cache_hit_rate_min: 0.6
    scenario_input_tokens_min: 4000
    scenario_input_tokens_max: 80000
  model_identity:
    enabled: false
    repetitions: 2
  billing:
    enabled: false
    tokenizer_encoding: cl100k_base
    allowed_deviation: 0.05
    max_tokens: 512

scoring:
  latency_ttft_good_ms: 1000
  latency_ttft_fail_ms: 10000
  output_tps_target: 20
  gates:
    - metric: compatibility.success_rate
      operator: ">="
      value: 0.95
      severity: fail
    - metric: latency.ttft_ms.p95
      operator: "<"
      value: 3000
      severity: warn
"""
