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


def _component_scores(
    plugins: dict[str, PluginResult], config: ScoringConfig
) -> dict[str, float]:
    scores: dict[str, float] = {}
    compatibility = plugins.get("compatibility")
    if compatibility and compatibility.status == RunStatus.COMPLETED:
        scores["compatibility"] = _bounded(
            float(compatibility.metrics.get("success_rate", 0)) * 100
        )

    quality = plugins.get("quality")
    if quality and quality.status == RunStatus.COMPLETED:
        scores["quality"] = _bounded(float(quality.metrics.get("score", 0)) * 100)

    tool_calling = plugins.get("tool_calling")
    if tool_calling and tool_calling.status == RunStatus.COMPLETED:
        scores["tool_calling"] = _bounded(
            float(tool_calling.metrics.get("success_rate", 0)) * 100
        )

    structured = plugins.get("structured_output")
    if structured and structured.status == RunStatus.COMPLETED:
        structured_score = _bounded(float(structured.metrics.get("success_rate", 0)) * 100)
        scores["compatibility"] = (
            (scores["compatibility"] + structured_score) / 2
            if "compatibility" in scores
            else structured_score
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
        reliability_score = sum(reliability_rates) / len(reliability_rates) * 100
        identity = plugins.get("model_identity")
        if identity and identity.status == RunStatus.COMPLETED:
            identity_score = float(identity.metrics.get("identity_score", 0)) * 100
            reliability_score = (reliability_score + identity_score) / 2
        scores["reliability"] = _bounded(reliability_score)
    return scores


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
        "applied_weight": applicable_weight,
        "gates": gates,
    }
