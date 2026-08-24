from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from provider_bench.models import RequestRecord


def percentile(values: Iterable[float | int | None], quantile: float) -> float | None:
    samples = sorted(float(value) for value in values if value is not None)
    if not samples:
        return None
    if len(samples) == 1:
        return samples[0]
    position = (len(samples) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return samples[lower]
    return samples[lower] + (samples[upper] - samples[lower]) * (position - lower)


def distribution(values: Iterable[float | int | None]) -> dict[str, float | None]:
    samples = [float(value) for value in values if value is not None]
    return {
        "p50": percentile(samples, 0.50),
        "p75": percentile(samples, 0.75),
        "p90": percentile(samples, 0.90),
        "p95": percentile(samples, 0.95),
        "p99": percentile(samples, 0.99),
        "mean": sum(samples) / len(samples) if samples else None,
    }


def status_counts(records: list[RequestRecord]) -> dict[str, Any]:
    total = len(records)
    successes = sum(record.status == "success" for record in records)
    return {
        "requests": total,
        "successes": successes,
        "success_rate": successes / total if total else 0.0,
        "rate_limited": sum(record.status == "rate_limited" for record in records),
        "server_errors": sum(record.status == "server_error" for record in records),
        "timeouts": sum(record.status == "timeout" for record in records),
        "ssl_errors": sum(record.status == "ssl_error" for record in records),
        "other_errors": sum(
            record.status
            not in {"success", "rate_limited", "server_error", "timeout", "ssl_error"}
            for record in records
        ),
    }

