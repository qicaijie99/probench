from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from provider_bench.models import ProviderRunResult, RunStatus


def _path(value: Any, path: str) -> Any:
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


_COMPARISON_METRICS = [
    ("compatibility", "success_rate", "Compatibility", "higher"),
    ("quality", "score", "Quality score", "higher"),
    ("latency", "ttft_ms.p95", "TTFT P95 (ms)", "lower"),
    ("latency", "e2e_ms.p95", "E2E P95 (ms)", "lower"),
    ("latency", "output_tps.p50", "Output TPS P50", "higher"),
    ("concurrency", "max_stable_concurrency", "Max stable concurrency", "higher"),
    ("tool_calling", "success_rate", "Tool calling", "higher"),
    ("structured_output", "success_rate", "Structured output", "higher"),
    ("billing", "within_tolerance_rate", "Billing accuracy", "higher"),
    ("billing", "estimated_cost_per_request_usd", "Cost / request (USD)", "lower"),
]


def _identity_comparison(
    reference: ProviderRunResult, candidate: ProviderRunResult
) -> dict[str, Any] | None:
    left = reference.plugins.get("model_identity")
    right = candidate.plugins.get("model_identity")
    if not left or not right or left.status != RunStatus.COMPLETED or right.status != RunStatus.COMPLETED:
        return None
    left_results = left.metrics.get("results") or []
    right_results = right.metrics.get("results") or []
    left_by_case = {
        (item.get("probe_id"), item.get("repetition")): str(item.get("response") or "")
        for item in left_results
    }
    similarities = []
    for item in right_results:
        key = (item.get("probe_id"), item.get("repetition"))
        if key in left_by_case:
            similarities.append(
                SequenceMatcher(
                    None,
                    " ".join(left_by_case[key].casefold().split()),
                    " ".join(str(item.get("response") or "").casefold().split()),
                ).ratio()
            )
    similarity = sum(similarities) / len(similarities) if similarities else None
    threshold = float(right.metrics.get("similarity_threshold", 0.75))
    reference_models = left.metrics.get("reported_models") or []
    candidate_models = right.metrics.get("reported_models") or []
    return {
        "behavior_similarity": similarity,
        "reported_model_equal": reference_models == candidate_models,
        "reference_models": reference_models,
        "candidate_models": candidate_models,
        "similarity_threshold": threshold,
        "possible_substitution": similarity is not None and similarity < threshold,
    }


def build_provider_comparisons(
    providers: dict[str, ProviderRunResult], reference_name: str | None = None
) -> dict[str, Any]:
    if len(providers) < 2:
        return {}
    reference_name = reference_name or next(iter(providers))
    if reference_name not in providers:
        raise ValueError(f"reference provider {reference_name!r} does not exist")
    reference = providers[reference_name]
    candidates: dict[str, Any] = {}
    for name, candidate in providers.items():
        if name == reference_name:
            continue
        metrics = []
        for plugin_name, metric_path, label, better in _COMPARISON_METRICS:
            left_plugin = reference.plugins.get(plugin_name)
            right_plugin = candidate.plugins.get(plugin_name)
            if not left_plugin or not right_plugin:
                continue
            left = _path(left_plugin.metrics, metric_path)
            right = _path(right_plugin.metrics, metric_path)
            if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                continue
            delta = float(right) - float(left)
            metrics.append(
                {
                    "plugin": plugin_name,
                    "metric": metric_path,
                    "label": label,
                    "better": better,
                    "reference": left,
                    "candidate": right,
                    "delta": delta,
                    "relative_delta": delta / abs(float(left)) if left else None,
                    "candidate_better": delta > 0 if better == "higher" else delta < 0,
                }
            )
        candidates[name] = {
            "score": {
                "reference": reference.scorecard.get("score"),
                "candidate": candidate.scorecard.get("score"),
                "delta": round(
                    float(candidate.scorecard.get("score", 0))
                    - float(reference.scorecard.get("score", 0)),
                    2,
                ),
            },
            "metrics": metrics,
            "identity": _identity_comparison(reference, candidate),
        }
    return {"reference": reference_name, "candidates": candidates}
