from __future__ import annotations

from typing import Any

from provider_bench.models import Usage


def extract_cache_usage(usage: dict[str, Any] | Usage | None) -> dict[str, Any]:
    """Extract prompt-cache read/write tokens from a provider usage payload.

    Supports the field shapes emitted by OpenAI, Anthropic, and several
    OpenAI-compatible gateways (including providers that report ``cached_tokens``
    at the top level or under ``prompt_tokens_details``).
    """
    if usage is None:
        return _empty()
    if isinstance(usage, Usage):
        usage = usage.details or usage.model_dump()
    if not isinstance(usage, dict):
        return _empty()

    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = usage.get("completion_tokens_details") or {}

    def first(*paths: str) -> int | None:
        for path in paths:
            value: Any = usage
            for part in path.split("."):
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(part)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        return None

    cache_read = first(
        "prompt_tokens_details.cached_tokens",
        "cached_tokens",
        "cache_read_input_tokens",
        "prompt_cache_hit_tokens",
        "cache_read",
    )
    cache_write = first(
        "cache_creation_input_tokens",
        "cache_write_input_tokens",
        "prompt_cache_write_tokens",
        "cache_write",
    )
    prompt_tokens = first("prompt_tokens", "input_tokens")
    completion_tokens = first("completion_tokens", "output_tokens")
    reasoning_tokens = first("completion_tokens_details.reasoning_tokens", "reasoning_tokens")
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_read_tokens": cache_read or 0,
        "cache_write_tokens": cache_write,
        "reasoning_tokens": reasoning_tokens,
        "field_present": _cache_field_present(usage),
    }


def _cache_field_present(usage: dict[str, Any]) -> bool:
    for key, value in usage.items():
        if key in {"cached_tokens", "cache_read_input_tokens", "prompt_cache_hit_tokens"}:
            return value is not None
        if key == "prompt_tokens_details" and isinstance(value, dict):
            if "cached_tokens" in value:
                return True
    return False


def _empty() -> dict[str, Any]:
    return {
        "prompt_tokens": None,
        "completion_tokens": None,
        "cache_read_tokens": 0,
        "cache_write_tokens": None,
        "reasoning_tokens": None,
        "field_present": False,
    }
