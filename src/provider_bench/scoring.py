from __future__ import annotations

import operator
from typing import Any

from provider_bench.models import HardGate, PluginResult, RunStatus, ScoringConfig

_OPERATORS = {
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
    "==": operator.eq,
}

# Component → dimension mapping. "gateway" covers protocol compliance, parameter
# validation, tool_choice semantics, routing consistency, cache and billing; "model"
# covers the model's own capability; "performance" covers speed, stability and cost.
_DIMENSIONS: dict[str, list[str]] = {
    "gateway": [
        "compatibility",
        "protocol",
        "features_param",
        "tool_choice",
        "cache",
        "model_identity",
        "billing",
    ],
    "model": ["quality", "structured_output", "tool_calling", "features_thinking"],
    "performance": ["latency", "throughput", "concurrency", "reliability", "cost"],
}

DIMENSION_LABELS: dict[str, str] = {
    "gateway": "网关合规",
    "model": "模型能力",
    "performance": "性能",
}


def _bounded(value: float) -> float:
    return max(0.0, min(100.0, value))


def _get_path(plugins: dict[str, PluginResult], path: str) -> Any:
    parts = path.split(".")
    if not parts or parts[0] not in plugins:
        return None
    value: Any = plugins[parts[0]].metrics
    for part in parts[1:]:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _rate(metric: dict[str, Any], passed_key: str, total_key: str) -> float:
    total = metric.get(total_key) or 0
    return float(metric.get(passed_key, 0)) / total if total else 0.0


def _component_scores(
    plugins: dict[str, PluginResult], config: ScoringConfig
) -> dict[str, float]:
    scores: dict[str, float] = {}

    compatibility = plugins.get("compatibility")
    if compatibility and compatibility.status == RunStatus.COMPLETED:
        scores["compatibility"] = _bounded(
            float(compatibility.metrics.get("success_rate", 0)) * 100
        )

    protocol = plugins.get("protocol")
    if protocol and protocol.status == RunStatus.COMPLETED:
        scores["protocol"] = _bounded(float(protocol.metrics.get("success_rate", 0)) * 100)

    quality = plugins.get("quality")
    if quality and quality.status == RunStatus.COMPLETED:
        scores["quality"] = _bounded(float(quality.metrics.get("score", 0)) * 100)

    structured = plugins.get("structured_output")
    if structured and structured.status == RunStatus.COMPLETED:
        scores["structured_output"] = _bounded(
            float(structured.metrics.get("success_rate", 0)) * 100
        )

    tool_calling = plugins.get("tool_calling")
    if tool_calling and tool_calling.status == RunStatus.COMPLETED:
        scores["tool_calling"] = _bounded(
            float(tool_calling.metrics.get("success_rate", 0)) * 100
        )
        branches_total = tool_calling.metrics.get("branches_total") or 0
        if branches_total:
            branch_rate = float(tool_calling.metrics.get("branches_passed", 0)) / branches_total
            scores["tool_choice"] = _bounded(branch_rate * 100)

    features = plugins.get("features")
    if features and features.status == RunStatus.COMPLETED:
        thinking = features.metrics.get("thinking") or {}
        reasoning = features.metrics.get("reasoning_effort") or {}
        passed = int(thinking.get("passed", 0)) + int(reasoning.get("passed", 0))
        total = int(thinking.get("total", 0)) + int(reasoning.get("total", 0))
        scores["features_thinking"] = _bounded(passed / total * 100) if total else 0.0
        params = features.metrics.get("param_constraints") or {}
        param_passed = int(params.get("passed", 0))
        param_total = int(params.get("total", 0))
        scores["features_param"] = _bounded(param_passed / param_total * 100) if param_total else 0.0

    cache = plugins.get("cache")
    if cache and cache.status == RunStatus.COMPLETED:
        scores["cache"] = _bounded(float(cache.metrics.get("hit_rate", 0)) * 100)

    identity = plugins.get("model_identity")
    if identity and identity.status == RunStatus.COMPLETED:
        scores["model_identity"] = _bounded(
            float(identity.metrics.get("identity_score", 0)) * 100
        )

    billing = plugins.get("billing")
    if billing and billing.status == RunStatus.COMPLETED:
        scores["billing"] = _bounded(
            float(billing.metrics.get("within_tolerance_rate", 0)) * 100
        )
        if billing.metrics.get("cost_score") is not None:
            scores["cost"] = _bounded(float(billing.metrics["cost_score"]))

    latency = plugins.get("latency")
    if latency and latency.status == RunStatus.COMPLETED:
        success_rate = float(latency.metrics.get("success_rate", 0))
        p95 = (latency.metrics.get("ttft_ms") or {}).get("p95")
        if p95 is None:
            latency_factor = 0.0
        elif p95 <= config.latency_ttft_good_ms:
            latency_factor = 1.0
        elif p95 >= config.latency_ttft_fail_ms:
            latency_factor = 0.0
        else:
            latency_factor = (config.latency_ttft_fail_ms - float(p95)) / (
                config.latency_ttft_fail_ms - config.latency_ttft_good_ms
            )
        scores["latency"] = _bounded(success_rate * latency_factor * 100)
        tps = (latency.metrics.get("output_tps") or {}).get("p50")
        if tps is not None:
            scores["throughput"] = _bounded(float(tps) / config.output_tps_target * 100)

    concurrency = plugins.get("concurrency")
    if concurrency and concurrency.status == RunStatus.COMPLETED:
        levels = concurrency.metrics.get("levels") or []
        highest = max((item.get("concurrency", 0) for item in levels), default=0)
        stable = concurrency.metrics.get("max_stable_concurrency", 0)
        scores["concurrency"] = _bounded(stable / highest * 100 if highest else 0)

    reliability_rates: list[float] = []
    for result in plugins.values():
        if result.status != RunStatus.COMPLETED:
            continue
        if "success_rate" in result.metrics:
            reliability_rates.append(float(result.metrics["success_rate"]))
        for group_name in ("levels", "batches"):
            group = result.metrics.get(group_name)
            if group:
                reliability_rates.extend(float(item.get("success_rate", 0)) for item in group)
    if reliability_rates:
        scores["reliability"] = _bounded(sum(reliability_rates) / len(reliability_rates) * 100)
    return scores


def _dimension_scores(
    component_scores: dict[str, float], config: ScoringConfig
) -> dict[str, dict[str, Any]]:
    dimensions: dict[str, dict[str, Any]] = {}
    for dimension, names in _DIMENSIONS.items():
        present = {name: component_scores[name] for name in names if name in component_scores}
        if not present:
            continue
        total_weight = sum(config.weights.get(name, 0) for name in present)
        score = (
            sum(present[name] * config.weights.get(name, 0) for name in present) / total_weight
            if total_weight
            else 0.0
        )
        dimensions[dimension] = {
            "score": round(score, 2),
            "components": {name: round(value, 2) for name, value in present.items()},
        }
    return dimensions


def _evaluate_gate(gate: HardGate, plugins: dict[str, PluginResult]) -> dict[str, Any]:
    actual = _get_path(plugins, gate.metric)
    passed = False
    if isinstance(actual, (int, float)):
        passed = _OPERATORS[gate.operator](float(actual), gate.value)
    return {
        "metric": gate.metric,
        "operator": gate.operator,
        "expected": gate.value,
        "actual": actual,
        "severity": gate.severity,
        "passed": passed,
    }


def build_scorecard(
    plugins: dict[str, PluginResult], config: ScoringConfig
) -> dict[str, Any]:
    component_scores = _component_scores(plugins, config)
    applicable_weight = sum(config.weights.get(name, 0) for name in component_scores)
    weighted_score = (
        sum(component_scores[name] * config.weights.get(name, 0) for name in component_scores)
        / applicable_weight
        if applicable_weight
        else 0.0
    )
    gates = [_evaluate_gate(gate, plugins) for gate in config.gates]
    failed_hard_gate = any(not gate["passed"] and gate["severity"] == "fail" for gate in gates)
    failed_warn_gate = any(not gate["passed"] and gate["severity"] == "warn" for gate in gates)
    if failed_hard_gate or weighted_score < config.fail_score_below:
        verdict = "FAIL"
    elif failed_warn_gate or weighted_score < config.warn_score_below:
        verdict = "WARN"
    else:
        verdict = "PASS"
    return {
        "score": round(weighted_score, 2),
        "verdict": verdict,
        "components": {name: round(value, 2) for name, value in component_scores.items()},
        "dimensions": _dimension_scores(component_scores, config),
        "applied_weight": applicable_weight,
        "gates": gates,
    }
