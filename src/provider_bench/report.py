from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment, select_autoescape

from provider_bench.models import RunResult
from provider_bench.storage import read_json, write_json


def _format_value(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, (dict, list)):
        return f"`{json.dumps(value, ensure_ascii=False)}`"
    return str(value)


def render_markdown(result: RunResult) -> str:
    lines = [
        f"# Benchmark Report: {result.run_id}",
        "",
        f"- Status: **{result.status.value}**",
        f"- Started: {result.started_at.isoformat()}",
        f"- Ended: {result.ended_at.isoformat()}",
        "",
        "## Scorecard",
        "",
        "| Provider | Model | Score | Verdict |",
        "| --- | --- | ---: | --- |",
    ]
    for provider in result.providers.values():
        score = provider.scorecard.get("score", 0)
        verdict = provider.scorecard.get("verdict", "FAIL")
        lines.append(f"| {provider.provider} | {provider.model} | {score:.2f} | **{verdict}** |")
    if result.comparisons:
        reference = result.comparisons.get("reference")
        lines.extend(["", "## Provider Comparison", "", f"Reference: **{reference}**", ""])
        for candidate_name, comparison in result.comparisons.get("candidates", {}).items():
            lines.extend(
                [
                    f"### {candidate_name} vs {reference}",
                    "",
                    "| Metric | Reference | Candidate | Delta | Better |",
                    "| --- | ---: | ---: | ---: | --- |",
                ]
            )
            for metric in comparison.get("metrics", []):
                better = "Candidate" if metric["candidate_better"] else "Reference"
                lines.append(
                    f"| {metric['label']} | {_format_value(metric['reference'])} | "
                    f"{_format_value(metric['candidate'])} | {_format_value(metric['delta'])} | {better} |"
                )
            identity = comparison.get("identity")
            if identity:
                lines.extend(
                    [
                        "",
                        f"Identity behavior similarity: **{_format_value(identity.get('behavior_similarity'))}**; "
                        f"possible substitution: **{identity.get('possible_substitution')}**.",
                    ]
                )
    for provider in result.providers.values():
        lines.extend(["", f"## {provider.provider}", ""])
        for plugin in provider.plugins.values():
            lines.extend(
                [
                    f"### {plugin.name}",
                    "",
                    f"Status: **{plugin.status.value}**",
                    "",
                ]
            )
            if plugin.error:
                lines.extend([f"Error: `{plugin.error}`", ""])
            if plugin.metrics:
                lines.extend(["| Metric | Value |", "| --- | --- |"])
                for key, value in plugin.metrics.items():
                    lines.append(f"| {key} | {_format_value(value)} |")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _collect_errors(run_dir: Path) -> list[dict[str, Any]]:
    errors = []
    for path in run_dir.glob("**/requests.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("status") != "success":
                errors.append(record)
    return errors


# --------------------------------------------------------------------------- #
# View-model builder: normalizes plugin metrics + raw records into the rich    #
# structure consumed by the file-1-style HTML template.                        #
# --------------------------------------------------------------------------- #

SCORING_ITEM_WEIGHTS = {
    "http_ok": 4,
    "ping_ok": 4,
    "model_match": 2,
    "usage_present": 4,
    "cache_hit": 8,
    "stream_integrity": 10,
    "usage_stream": 8,
    "image_base64": 10,
    "video_base64": 10,
    "tool_choice": 12,
    "structured_output": 10,
    "reasoning_effort": 10,
    "thinking_switch": 8,
}


def _load_records(run_dir: Path) -> dict[str, list[dict[str, Any]]]:
    by_provider: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in run_dir.glob("**/requests.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            by_provider[record.get("provider", "unknown")].append(record)
    return dict(by_provider)


def _short(value: Any, limit: int = 400) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…<{len(value)} chars>"
    if isinstance(value, dict):
        return {key: _short(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [_short(item, limit) for item in value]
    return value


def _truncate_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == "body" and isinstance(item, dict):
                result[key] = _short(item)
            else:
                result[key] = _truncate_payload(item)
        return result
    return value


def _records_by_request_id(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {record["request_id"]: record for record in records if record.get("request_id")}


def _build_dimensions_view(dimensions: dict[str, Any]) -> list[dict[str, Any]]:
    labels = {"gateway": "网关合规", "model": "模型能力", "performance": "性能"}
    return [
        {"key": key, "label": labels.get(key, key), **value}
        for key, value in dimensions.items()
    ]


def _build_scoring_items(plugins: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = {name: p.metrics for name, p in plugins.items() if p.status.value in ("COMPLETED",)}
    items: list[dict[str, Any]] = []

    def add(item_id: str, name: str, passed: bool | None, note: str = "") -> None:
        items.append(
            {
                "id": item_id,
                "name": name,
                "weight": SCORING_ITEM_WEIGHTS.get(item_id, 0),
                "passed": passed,
                "note": note,
            }
        )

    compat = metrics.get("compatibility", {}).get("checks", {})
    add("http_ok", "请求成功", _passed(compat.get("non_streaming")), _note(compat.get("non_streaming")))

    proto = metrics.get("protocol", {}).get("checks", {})
    add("ping_ok", "探活", _passed(proto.get("ping")), _note(proto.get("ping")))
    add("stream_integrity", "Stream 完整性", _passed(proto.get("stream_integrity")), _note(proto.get("stream_integrity")))
    add("usage_stream", "Usage 流式", _passed(proto.get("usage_stream")), _note(proto.get("usage_stream")))
    add("image_base64", "Image_base64", _passed(proto.get("image_base64")), _note(proto.get("image_base64")))
    add("video_base64", "Video_base64", _passed(proto.get("video_base64")), _note(proto.get("video_base64")))

    identity = metrics.get("model_identity", {})
    models = identity.get("reported_models") or []
    add("model_match", "模型回显", bool(models), f"{len(models)} 种模型标识" if models else "无模型标识")

    billing = metrics.get("billing", {})
    usage_present = billing.get("usage_present_rate")
    usage_passed = usage_present is not None and float(usage_present) > 0
    add("usage_present", "usage 回传", usage_passed if usage_present is not None else None, "")

    cache = metrics.get("cache", {})
    hit_rate = cache.get("hit_rate")
    add("cache_hit", "Prefix Cache 命中率", None if hit_rate is None else hit_rate > 0, f"hit_rate={_pct(hit_rate)}")

    tc = metrics.get("tool_calling", {})
    branches = tc.get("branches", {})
    if branches:
        passed = all(branch.get("passed") for branch in branches.values())
        failed = [name for name, branch in branches.items() if not branch.get("passed")]
        add("tool_choice", "tool_choice 分支", passed, f"失败分支: {failed}" if failed else "全部分支通过")

    structured = metrics.get("structured_output", {})
    if structured:
        add(
            "structured_output",
            "结构化输出",
            structured.get("success_rate") == 1,
            f"json_object + json_schema",
        )

    features = metrics.get("features", {})
    reasoning = features.get("reasoning_effort", {})
    if reasoning:
        add(
            "reasoning_effort",
            "reasoning_effort",
            reasoning.get("success_rate") == 1,
            "三档均返回 reasoning_tokens" if reasoning.get("success_rate") == 1 else "",
        )
    thinking = features.get("thinking", {})
    if thinking:
        add("thinking_switch", "思考开关", thinking.get("success_rate") == 1, "")

    return items


def _passed(check: dict[str, Any] | None) -> bool | None:
    if check is None:
        return None
    return bool(check.get("passed"))


def _note(check: dict[str, Any] | None) -> str:
    if check is None:
        return ""
    return check.get("note") or (check.get("error") or "")


def _pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.2f}%"


def _ms(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value):.0f}"


def _result_label(passed: bool | None) -> str:
    if passed is None:
        return "—"
    return "PASS" if passed else "FAIL"


def _build_functional_cases(plugins: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def push(case_id: str, name: str, suite: str, level: str, passed: bool | None, **extra: Any) -> None:
        cases.append(
            {
                "id": case_id,
                "name": name,
                "suite": suite,
                "level": level,
                "passed": passed,
                "warn": extra.get("warn", False),
                "http": extra.get("http"),
                "e2e_ms": extra.get("e2e_ms"),
                "ttfb_ms": extra.get("ttfb_ms"),
                "ttft_ms": extra.get("ttft_ms"),
                "note": extra.get("note", ""),
                "check": extra.get("check", ""),
                "request_id": extra.get("request_id"),
            }
        )

    for name, plugin in plugins.items():
        if plugin.status.value != "COMPLETED":
            continue
        metrics = plugin.metrics
        if name == "compatibility":
            for key, check in metrics.get("checks", {}).items():
                push(
                    f"must.{key}",
                    f"协议 · {key}",
                    "协议",
                    "must",
                    _passed(check),
                    http=None,
                    note=_note(check),
                    check=key,
                    request_id=check.get("request_id"),
                )
        elif name == "protocol":
            for key, check in metrics.get("checks", {}).items():
                push(
                    f"must.{key}",
                    _protocol_name(key),
                    "协议",
                    "must",
                    _passed(check),
                    http=check.get("http_status"),
                    e2e_ms=check.get("e2e_ms"),
                    ttfb_ms=check.get("ttfb_ms"),
                    ttft_ms=check.get("ttft_ms"),
                    note=_note(check),
                    check=key,
                    request_id=check.get("request_id"),
                )
        elif name == "tool_calling":
            for result in metrics.get("results", []):
                push(
                    f"must.tool_call.{result['case_id']}",
                    f"工具调用 · {result['case_id']}",
                    "工具调用",
                    "must",
                    result["passed"],
                    http=None,
                    note="工具选择/参数/JSON 校验" if result["passed"] else result.get("error", ""),
                    check="Tool Call 能力",
                    request_id=result.get("request_id"),
                )
            for result in metrics.get("branch_results", []):
                push(
                    f"must.tool_choice_{result['branch']}",
                    f"工具调用 · tool_choice={result['branch']}",
                    "工具调用",
                    "must",
                    result["passed"],
                    http=result.get("http_status"),
                    e2e_ms=result.get("e2e_ms"),
                    note="已发起 tool_calls" if result.get("has_tool_calls") else "",
                    check=f"tool_choice={result['branch']}",
                    request_id=result.get("request_id"),
                )
        elif name == "structured_output":
            for result in metrics.get("results", []):
                push(
                    f"must.{result['mode']}.{result['case_id']}",
                    f"结构化输出 · {result['case_id']}",
                    "结构化输出",
                    "must",
                    result["passed"],
                    http=None,
                    note="合法 JSON" if result.get("json_valid") else result.get("parse_error", ""),
                    check=result["mode"],
                    request_id=result.get("request_id"),
                )
        elif name == "features":
            for result in metrics.get("reasoning_effort_results", []):
                push(
                    f"must.reasoning_effort_{result['level']}",
                    f"reasoning_effort · {result['level']}",
                    "思考/参数",
                    "must",
                    result["passed"],
                    http=result.get("http_status"),
                    e2e_ms=result.get("e2e_ms"),
                    note=f"reasoning_tokens={result.get('reasoning_tokens')}",
                    check="reasoning_effort",
                    request_id=result.get("request_id"),
                )
            for result in metrics.get("thinking_results", []):
                push(
                    f"must.thinking_{result['variant']}",
                    f"思考 · {result['variant']}",
                    "思考/参数",
                    "must",
                    result["passed"],
                    http=result.get("http_status"),
                    e2e_ms=result.get("e2e_ms"),
                    note=f"reasoning_tokens={result.get('reasoning_tokens')}",
                    check="思考行为",
                    request_id=result.get("request_id"),
                )
            toggle = metrics.get("thinking_toggle")
            if toggle:
                push(
                    "must.thinking_toggle",
                    "思考开关 · enable_thinking 开/关对比",
                    "思考/参数",
                    "must",
                    None if toggle.get("warn") else toggle.get("passed"),
                    warn=toggle.get("warn"),
                    http=toggle.get("http_status"),
                    e2e_ms=toggle.get("e2e_ms"),
                    note=toggle.get("note", ""),
                    check="思考开关",
                    request_id=toggle.get("request_id_off") or toggle.get("request_id_on"),
                )
            for result in metrics.get("param_constraint_results", []):
                warn = result.get("warn", False)
                push(
                    f"must.param_{result['case_id']}",
                    f"参数约束 · {result['param']}={result['value']}",
                    "参数约束",
                    "must",
                    None if warn else result["passed"],
                    warn=warn,
                    http=result.get("http_status"),
                    e2e_ms=result.get("e2e_ms"),
                    note=result.get("note", ""),
                    check="参数约束",
                    request_id=result.get("request_id"),
                )
    return cases


def _protocol_name(key: str) -> str:
    names = {
        "ping": "探活",
        "stream_integrity": "Stream 完整性",
        "usage_stream": "Usage 流式",
        "image_base64": "多模态图像 · Image_base64",
        "video_base64": "多模态视频 · Video_base64",
    }
    return names.get(key, key)


def _build_failures(
    cases: list[dict[str, Any]], records_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    failures = []
    for case in cases:
        if case["passed"] is not False:
            continue
        record = records_by_id.get(case["request_id"], {})
        failures.append(
            {
                "id": case["id"],
                "name": case["name"],
                "suite": case["suite"],
                "level": case["level"],
                "reason": case["note"] or record.get("error") or "",
                "http": case["http"],
                "evidence": _short(record.get("response")),
                "request": _short(_truncate_payload(record.get("request"))),
                "response": _short(record.get("response")),
            }
        )
    return failures


def _build_cache_view(plugins: dict[str, Any]) -> dict[str, Any] | None:
    plugin = plugins.get("cache")
    if plugin is None or plugin.status.value != "COMPLETED":
        return None
    metrics = plugin.metrics
    rounds = metrics.get("rounds", [])
    bars: list[dict[str, Any]] = []
    max_total = 0
    measured_index = 0
    for round_ in rounds:
        usage = round_.get("usage", {})
        total = usage.get("total_prompt") or 0
        read = usage.get("cache_read") or 0
        write = usage.get("cache_write") or 0
        uncached = max(total - read - write, 0)
        max_total = max(max_total, total)
        if round_.get("warmup"):
            label = "预热"
        else:
            measured_index += 1
            label = f"#{measured_index}"
        bars.append(
            {
                "label": label,
                "read": read,
                "write": write,
                "uncached": uncached,
                "total": total,
            }
        )
    for bar in bars:
        total = max(bar["total"], max_total)
        for key in ("read", "write", "uncached"):
            bar[f"{key}_pct"] = bar[key] / total * 100 if total else 0.0
    return {
        "hit_rate": metrics.get("hit_rate"),
        "coverage": metrics.get("coverage"),
        "saved_tokens": metrics.get("saved_tokens"),
        "measured": metrics.get("measured"),
        "rounds": rounds,
        "bars": bars,
    }


def _build_latency_view(plugins: dict[str, Any]) -> dict[str, Any] | None:
    plugin = plugins.get("latency")
    if plugin is None or plugin.status.value != "COMPLETED":
        return None
    metrics = plugin.metrics
    thinking = "ttfr_ms" in metrics
    return {
        "ttfb": metrics.get("ttfb_ms"),
        "ttft": metrics.get("ttft_ms"),
        "ttfr": metrics.get("ttfr_ms") if thinking else None,
        "ttfc": metrics.get("ttfc_ms") if thinking else None,
        "overhead": metrics.get("thinking_overhead_ms") if thinking else None,
        "tpot": metrics.get("tpot_ms"),
        "itl": metrics.get("itl_ms"),
        "e2e": metrics.get("e2e_ms"),
        "thinking": thinking,
        "success_rate": metrics.get("success_rate"),
        "details": metrics.get("details", []),
    }


def _build_concurrency_view(plugins: dict[str, Any]) -> dict[str, Any] | None:
    plugin = plugins.get("concurrency")
    if plugin is None or plugin.status.value != "COMPLETED":
        return None
    metrics = plugin.metrics
    return {
        "max_stable_concurrency": metrics.get("max_stable_concurrency"),
        "levels": metrics.get("levels", []),
    }


def _build_burst_view(plugins: dict[str, Any]) -> dict[str, Any] | None:
    plugin = plugins.get("burst")
    if plugin is None or plugin.status.value != "COMPLETED":
        return None
    metrics = plugin.metrics
    return {"batches": metrics.get("batches", [])}


def _build_provider_view(
    name: str,
    provider: Any,
    plugins: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    scoring_items = _build_scoring_items(plugins)
    passed_items = sum(1 for item in scoring_items if item["passed"] is True)
    total_items = sum(1 for item in scoring_items if item["passed"] is not None)
    weighted_score = sum(
        item["weight"] for item in scoring_items if item["passed"] is True
    )
    weighted_total = sum(item["weight"] for item in scoring_items if item["passed"] is not None)
    cases = _build_functional_cases(plugins)
    records_by_id = _records_by_request_id(records)
    cases_by_suite: list[tuple[str, list[dict[str, Any]]]] = []
    seen: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        seen.setdefault(case["suite"], []).append(case)
    cases_by_suite = list(seen.items())
    latency = _build_latency_view(plugins)
    benchmark_plugin = plugins.get("benchmark")
    benchmark = (
        benchmark_plugin.metrics
        if benchmark_plugin is not None and benchmark_plugin.status.value == "COMPLETED"
        else None
    )
    identity = (
        plugins["model_identity"].metrics
        if "model_identity" in plugins and plugins["model_identity"].status.value == "COMPLETED"
        else {}
    )
    return {
        "name": name,
        "model": provider.model,
        "score": provider.scorecard.get("score"),
        "verdict": provider.scorecard.get("verdict"),
        "components": provider.scorecard.get("components", {}),
        "dimensions": _build_dimensions_view(provider.scorecard.get("dimensions", {})),
        "scoring_items": scoring_items,
        "functional_passed": passed_items,
        "functional_total": total_items,
        "functional_score": round(weighted_score / weighted_total * 100, 1) if weighted_total else None,
        "cases": cases,
        "cases_by_suite": cases_by_suite,
        "failures": _build_failures(cases, records_by_id),
        "cache": _build_cache_view(plugins),
        "latency": latency,
        "benchmark": benchmark,
        "concurrency": _build_concurrency_view(plugins),
        "burst": _build_burst_view(plugins),
        "reported_models": identity.get("reported_models", []),
        "identity_status": identity.get("identity_status"),
        "reasons": identity.get("reasons", []),
        "ttft_p50": (latency or {}).get("ttft", {}).get("p50") if latency else None,
        "e2e_p50": (latency or {}).get("e2e", {}).get("p50") if latency else None,
    }


def _build_view(run_dir: Path, result: RunResult) -> dict[str, Any]:
    records_by_provider = _load_records(run_dir)
    providers = []
    for name, provider in result.providers.items():
        providers.append(
            _build_provider_view(name, provider, provider.plugins, records_by_provider.get(name, []))
        )
    comparisons = []
    reference = result.comparisons.get("reference")
    for candidate_name, comparison in result.comparisons.get("candidates", {}).items():
        comparisons.append(
            {
                "reference": reference,
                "candidate": candidate_name,
                "metrics": comparison.get("metrics", []),
                "identity": comparison.get("identity"),
            }
        )
    return {
        "run_id": result.run_id,
        "started_at": result.started_at.isoformat(),
        "ended_at": result.ended_at.isoformat(),
        "providers": providers,
        "comparisons": comparisons,
    }


# --------------------------------------------------------------------------- #
# HTML template (UI modelled after the reference functional test report).     #
# --------------------------------------------------------------------------- #

_HTML_TEMPLATE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>API Provider Benchmark · {{ view.run_id }}</title>
<style>
body{font-family:ui-sans-serif,system-ui,sans-serif;margin:24px;background:#f7f5f2;color:#1b1b1b}
h1{margin:0 0 8px}.meta{color:#555;line-height:1.6}
.card{background:#fff;border:1px solid #e6e1da;border-radius:12px;padding:16px;margin:12px 0}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{border-bottom:1px solid #eee;padding:8px;text-align:left;vertical-align:top}
th{background:#faf8f5}.ok{color:#067d3c}.bad{color:#b42318}.err{color:#b42318}.warn-text{color:#b54708;font-weight:700}
code{font-size:12px}
.fail-box{background:#fff8f7;border:1px solid #f0d0cb;border-radius:10px;padding:12px;margin:12px 0}
.fail-case{border-top:1px solid #f0d0cb;padding-top:10px;margin-top:10px}
.fail-col ul{margin:6px 0 0;padding-left:18px;max-height:220px;overflow:auto}
.fail-col li{margin:2px 0;font-size:12px;line-height:1.4}
.warn{background:#fff8e8;border:1px solid #f0d9a8;border-radius:8px;padding:10px 12px;color:#8a5a00;line-height:1.5}
pre.io{background:#1b1b1b;color:#f5f5f5;padding:12px;border-radius:8px;overflow:auto;font-size:12px;line-height:1.45;max-height:420px}
pre.mtb{background:#1b1b1b;color:#f5f5f5;padding:14px;border-radius:8px;overflow:auto;font-size:12px;line-height:1.35}
.cache-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:8px 0 14px}
.cache-title{font-size:16px;font-weight:650}
.cache-pill{display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:650}
.cache-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:0 0 16px}
.cache-stat{background:#faf8f5;border:1px solid #eee;border-radius:12px;padding:12px 14px}
.cache-stat .l{font-size:11px;color:#6b7280}.cache-stat .v{font-size:22px;font-weight:700;margin-top:6px}
.cache-stat .s{font-size:11px;color:#6b7280;margin-top:6px}
.cache-chart{display:flex;align-items:flex-end;gap:10px;height:200px;padding:8px 4px 0;border-bottom:1px solid #e5e7eb}
.cache-bar-col{flex:1;min-width:36px;max-width:80px;height:100%;display:flex;flex-direction:column;align-items:center}
.cache-bar{width:70%;flex:1;display:flex;flex-direction:column-reverse;justify-content:flex-start;background:#f3f4f6;border-radius:6px 6px 0 0;overflow:hidden}
.cache-bar .seg{width:100%}.cache-bar .seg.read{background:#5a7d4e}.cache-bar .seg.write{background:#d97706}.cache-bar .seg.uncached{background:#9ca3af}
.cache-bar-label{font-size:11px;color:#6b7280;margin-top:6px}
.cache-chart-wrap{margin-top:18px;padding-top:8px;border-top:1px solid #eee}
.cache-chart-title{font-size:14px;font-weight:650;margin-bottom:8px}
.cache-legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:#555;margin-bottom:10px}
.cache-legend i.lg{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:middle}
.cache-legend i.read{background:#5a7d4e}.cache-legend i.write{background:#d97706}.cache-legend i.uncached{background:#9ca3af}
.ssl-warn{background:#fff8f7;border:1px solid #f0d0cb;border-radius:8px;padding:10px 12px;color:#b42318;line-height:1.5}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px}
.chart-card{background:#fff;border:1px solid #e6e1da;border-radius:12px;padding:12px 14px}
.chart-card h4{margin:0 0 8px;font-size:14px;font-weight:600}
.chart-box{width:100%;height:280px}
.hint{font-size:12px;color:#777;margin:0 0 10px}
</style></head><body>
<h1>API Provider Benchmark 测试报告</h1>
<div class="meta">
  Run ID: {{ view.run_id }}<br/>
  生成时间: {{ view.started_at }}
</div>

{% set ssl_errors = errors | selectattr('status', 'equalto', 'ssl_error') | list %}
{% if ssl_errors %}
<section class="card">
  <h2 class="bad">TLS / SSL 稳定性诊断</h2>
  <div class="ssl-warn">检测到 {{ ssl_errors|length }} 次 TLS/SSL 连接异常（如 <code>SSL: UNEXPECTED_EOF_WHILE_READING</code>）。请排查网关 TLS 连接、连接复用与主动断连策略；此类异常不代表模型能力本身失败。</div>
  <table><thead><tr><th>Provider</th><th>Case</th><th>错误</th></tr></thead><tbody>
  {% for error in ssl_errors %}<tr><td>{{ error.provider }}</td><td>{{ error.case_id }}</td><td class="err">{{ error.error }}</td></tr>{% endfor %}
  </tbody></table>
</section>
{% endif %}

{% for provider in view.providers %}
<section class="card">
  <h2 class="{{ 'bad' if provider.verdict == 'FAIL' else 'ok' }}">总体结论：{{ provider.verdict }}{% if provider.verdict == 'FAIL' %}（未通过）{% elif provider.verdict == 'WARN' %}（存疑）{% else %}（通过）{% endif %}</h2>
  <table>
    <thead><tr><th>模型</th><th>判定</th><th>得分</th><th>子项</th><th>TTFT p50</th><th>E2E p50</th><th>摘要</th></tr></thead>
    <tbody><tr>
      <td><code>{{ provider.model }}</code></td>
      <td style="font-weight:700" class="{{ provider.verdict }}">{{ provider.verdict }}</td>
      <td>{{ provider.score|num('%.1f') }}/100</td>
      <td>{{ provider.functional_passed }}/{{ provider.functional_total }}</td>
      <td>{{ provider.ttft_p50|round(0)|int if provider.ttft_p50 is not none else '—' }}</td>
      <td>{{ provider.e2e_p50|round(0)|int if provider.e2e_p50 is not none else '—' }}</td>
      <td>{{ provider.functional_passed }}/{{ provider.functional_total }} 项通过 · 失败 {{ provider.failures|length }} 项</td>
    </tr></tbody>
  </table>
  {% if provider.dimensions %}
  <div class="grid" style="margin:14px 0 0">
  {% for dim in provider.dimensions %}
    <article class="card" style="box-shadow:none;margin:0">
      <div class="muted">{{ dim.label }}</div>
      <div class="score" style="font-size:26px">{{ dim.score|num('%.1f') }}</div>
      <div class="muted" style="font-size:12px">
        {% for name, value in dim.components.items() %}{{ name }} {{ value|num('%.0f') }} · {% endfor %}
      </div>
    </article>
  {% endfor %}
  </div>
  {% endif %}
</section>

{% if provider.failures %}
<section class="card">
  <h2 class="bad">错误用例汇总（{{ provider.failures|length }}）</h2>
  <table>
    <thead><tr><th>套件</th><th>级别</th><th>Case</th><th>名称</th><th>原因</th></tr></thead>
    <tbody>
    {% for failure in provider.failures %}
      <tr><td>{{ failure.suite }}</td><td>{{ failure.level }}</td><td><code>{{ failure.id }}</code></td><td>{{ failure.name }}</td><td class="err">{{ failure.reason }}</td></tr>
    {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}

<section class="card">
  <h3>{{ provider.model }} · {{ provider.verdict }} · {{ provider.score }}</h3>
  <h4>评分子项</h4>
  <table><thead><tr><th>ID</th><th>名称</th><th>结果</th><th>权重</th><th>说明</th></tr></thead>
  <tbody>
  {% for item in provider.scoring_items %}
    <tr>
      <td><code>{{ item.id }}</code></td><td>{{ item.name }}</td>
      <td class="{{ 'ok' if item.passed is true else ('bad' if item.passed is false else '') }}">{{ 'PASS' if item.passed is true else ('FAIL' if item.passed is false else '—') }}</td>
      <td>{{ item.weight }}</td><td>{{ item.note }}</td>
    </tr>
  {% endfor %}
  </tbody></table>

  {% for suite, cases in provider.cases_by_suite %}
  <h4>{{ suite }}</h4>
  <table><thead><tr><th>Case</th><th>级别</th><th>名称</th><th>结果</th><th>HTTP</th><th>E2E ms</th><th>TTFB ms</th><th>TTFT ms</th><th>说明</th></tr></thead>
  <tbody>
  {% for case in cases %}
    <tr>
      <td><code>{{ case.id }}</code></td><td>{{ case.level }}</td><td>{{ case.name }}</td>
      <td class="{{ 'ok' if case.passed is true else ('bad' if case.passed is false else ('warn-text' if case.warn else '')) }}" style="font-weight:700">{{ 'PASS' if case.passed is true else ('FAIL' if case.passed is false else ('WARN' if case.warn else '—')) }}</td>
      <td>{{ case.http or '—' }}</td><td>{{ case.e2e_ms|round(0)|int if case.e2e_ms is not none else '—' }}</td><td>{{ case.ttfb_ms|round(0)|int if case.ttfb_ms is not none else '—' }}</td><td>{{ case.ttft_ms|round(0)|int if case.ttft_ms is not none else '—' }}</td>
      <td>{{ case.note }}</td>
    </tr>
  {% endfor %}
  </tbody></table>
  {% endfor %}

  {% if provider.cache %}
  <h4>缓存命中率 · 长文固定 + 随机数变化</h4>
  <div class="cache-head">
    <span class="cache-title">OpenAI Chat Completions</span>
    {% if provider.cache.hit_rate and provider.cache.hit_rate > 0 %}
    <span class="cache-pill" style="background:#e7f4ec;color:#067d3c">命中 {{ (provider.cache.hit_rate * 100)|num('%.1f') }}%</span>
    {% else %}
    <span class="cache-pill" style="background:#fdeceb;color:#b42318">全部未命中</span>
    {% endif %}
  </div>
  <div class="cache-stats">
    <div class="cache-stat"><div class="l">请求级命中率</div><div class="v">{{ (provider.cache.hit_rate * 100)|num('%.1f') }}%</div><div class="s">{{ provider.cache.measured }} 轮</div></div>
    <div class="cache-stat"><div class="l">Token 覆盖率</div><div class="v">{{ (provider.cache.coverage * 100)|num('%.1f') }}%</div></div>
    <div class="cache-stat"><div class="l">节省 Token</div><div class="v">{{ provider.cache.saved_tokens }}</div></div>
  </div>
  <table><thead><tr><th>轮次</th><th>判定</th><th>总输入</th><th>缓存读</th><th>未缓存</th><th>耗时</th></tr></thead>
  <tbody>
  {% for round in provider.cache.rounds %}
    <tr>
      <td>{{ '预热' if round.warmup else '#' ~ loop.index0 }}</td>
      <td>{% if round.warmup %}预热{% elif round.hit %}<span class="cache-pill" style="background:#e7f4ec;color:#067d3c">命中</span>{% else %}<span class="cache-pill" style="background:#fff4e5;color:#b54708">未命中</span>{% endif %}</td>
      <td>{{ round.usage.input }}</td><td>{{ round.usage.cache_read }}</td><td>{{ round.usage.total_prompt - round.usage.cache_read }}</td><td>{{ round.e2e_ms|round(0)|int if round.e2e_ms is not none else '-' }} ms</td>
    </tr>
  {% endfor %}
  </tbody></table>
  {% if provider.cache.bars %}
  <div class="cache-chart-wrap">
    <div class="cache-chart-title">每轮输入 Token 构成</div>
    <div class="cache-legend">
      <span><i class="lg read"></i>缓存读取</span>
      <span><i class="lg write"></i>缓存写入</span>
      <span><i class="lg uncached"></i>未缓存输入</span>
    </div>
    <div class="cache-chart">
      {% for bar in provider.cache.bars %}
      <div class="cache-bar-col">
        <div class="cache-bar" title="读{{ bar.read }} / 写{{ bar.write }} / 未缓存{{ bar.uncached }}">
          <div class="seg read" style="height:{{ bar.read_pct|num('%.2f') }}%"></div>
          <div class="seg write" style="height:{{ bar.write_pct|num('%.2f') }}%"></div>
          <div class="seg uncached" style="height:{{ bar.uncached_pct|num('%.2f') }}%"></div>
        </div>
        <div class="cache-bar-label">{{ bar.label }}</div>
      </div>
      {% endfor %}
    </div>
    <p class="meta" style="margin-top:10px">理想形态：预热轮主要为「缓存写入 / 未缓存输入」，后续轮次绝大部分为「缓存读取」。</p>
  </div>
  {% endif %}
  {% endif %}

  {% if provider.reported_models %}
  <h4>返回模型汇总</h4>
  <p class="meta">响应模型去重数：{{ provider.reported_models|length }}</p>
  <table><thead><tr><th>响应 model</th></tr></thead><tbody>
  {% for model in provider.reported_models %}<tr><td><code>{{ model }}</code></td></tr>{% endfor %}
  </tbody></table>
  {% endif %}

  {% if provider.latency %}
  <h4>延迟 · TTFT / TTFB / TPOT / ITL / E2E{% if provider.latency.thinking %} / 思考模式 TTFR·TTFC{% endif %}</h4>
  <table>
    <thead><tr><th>指标</th><th>avg</th><th>p50</th><th>p75</th><th>p90</th><th>p95</th><th>p99</th></tr></thead>
    <tbody>
      <tr><td>TTFB（首字节）</td>{% for key in ['mean','p50','p75','p90','p95','p99'] %}<td>{{ provider.latency.ttfb[key]|round(0)|int if provider.latency.ttfb and provider.latency.ttfb[key] is not none else '-' }}</td>{% endfor %}</tr>
      <tr><td>TTFT（流式）</td>{% for key in ['mean','p50','p75','p90','p95','p99'] %}<td>{{ provider.latency.ttft[key]|round(0)|int if provider.latency.ttft and provider.latency.ttft[key] is not none else '-' }}</td>{% endfor %}</tr>
      {% if provider.latency.thinking %}
      <tr><td>TTFR（首个推理 token）</td>{% for key in ['mean','p50','p75','p90','p95','p99'] %}<td>{{ provider.latency.ttfr[key]|round(0)|int if provider.latency.ttfr and provider.latency.ttfr[key] is not none else '-' }}</td>{% endfor %}</tr>
      <tr><td>TTFC（首个正文 token）</td>{% for key in ['mean','p50','p75','p90','p95','p99'] %}<td>{{ provider.latency.ttfc[key]|round(0)|int if provider.latency.ttfc and provider.latency.ttfc[key] is not none else '-' }}</td>{% endfor %}</tr>
      <tr><td>思考开销（TTFC−TTFR）</td>{% for key in ['mean','p50','p75','p90','p95','p99'] %}<td>{{ provider.latency.overhead[key]|round(0)|int if provider.latency.overhead and provider.latency.overhead[key] is not none else '-' }}</td>{% endfor %}</tr>
      {% endif %}
      <tr><td>TPOT</td>{% for key in ['mean','p50','p75','p90','p95','p99'] %}<td>{{ provider.latency.tpot[key]|num('%.2f') if provider.latency.tpot and provider.latency.tpot[key] is not none else '-' }}</td>{% endfor %}</tr>
      <tr><td>E2E</td>{% for key in ['mean','p50','p75','p90','p95','p99'] %}<td>{{ provider.latency.e2e[key]|round(0)|int if provider.latency.e2e and provider.latency.e2e[key] is not none else '-' }}</td>{% endfor %}</tr>
    </tbody>
  </table>
  {% if provider.latency.details %}
  <h5>流式用例 TTFT 明细{% if provider.latency.thinking %}（思考模式）{% endif %}</h5>
  <table><thead><tr><th>Case</th><th>TTFT ms</th>{% if provider.latency.thinking %}<th>TTFR ms</th><th>TTFC ms</th>{% endif %}<th>TTFB ms</th><th>E2E ms</th></tr></thead><tbody>
  {% for detail in provider.latency.details %}
    <tr><td><code>{{ detail.case_id }}</code></td><td>{{ detail.ttft_ms|round(0)|int if detail.ttft_ms is not none else '-' }}</td>{% if provider.latency.thinking %}<td>{{ detail.ttfr_ms|round(0)|int if detail.ttfr_ms is not none else '-' }}</td><td>{{ detail.ttfc_ms|round(0)|int if detail.ttfc_ms is not none else '-' }}</td>{% endif %}<td>{{ detail.ttfb_ms|round(0)|int if detail.ttfb_ms is not none else '-' }}</td><td>{{ detail.e2e_ms|round(0)|int if detail.e2e_ms is not none else '-' }}</td></tr>
  {% endfor %}
  </tbody></table>
  {% endif %}
  {% endif %}

  {% if provider.benchmark %}
  <h4>性能压测 · 会话/轮次</h4>
  <pre class="mtb">+------------------------------------------------------------+
| BENCHMARK RESULTS                                          |
+------------------------------------------------------------+
| Total requests.................................{{ provider.benchmark.total_requests }}|
| Duration.................................{{ provider.benchmark.duration_seconds|num('%.3f') }} s|
| Avg prompt length............................{{ provider.benchmark.avg_prompt_tokens|round(0)|int }} tok|
| Avg output length.............................{{ provider.benchmark.avg_output_tokens|round(0)|int }} tok|
| Cache hit rate............................{{ (provider.benchmark.coverage * 100)|num('%.4f') }}%|
| Cache hit rate (steady, turn>=2)..........{{ (provider.benchmark.coverage_steady * 100)|num('%.4f') }}%|
| Stop reason......................{{ provider.benchmark.stop_reason }}|
+------------------------------------------------------------+
| THROUGHPUT                                                 |
+------------------------------------------------------------+
| Request................................{{ provider.benchmark.rps|num('%.4f') }} req/s|
| Input tokens........................{{ provider.benchmark.input_tokens_per_second|num('%.1f') }} tok/s|
| Output tokens........................{{ provider.benchmark.output_tokens_per_second|num('%.4f') }} tok/s|
| Input TPM..............................{{ provider.benchmark.input_tpm|num('%.0f') }}|
| Output TPM..............................{{ provider.benchmark.output_tpm|num('%.0f') }}|
+------------------------------------------------------------+
| TTFT                                                       |
+------------------------------------------------------------+
| avg........................{{ provider.benchmark.ttft_ms.mean|num('%.2f') }} s|
| p50........................{{ provider.benchmark.ttft_ms.p50|num('%.2f') }} s|
| p95........................{{ provider.benchmark.ttft_ms.p95|num('%.2f') }} s|
| p99........................{{ provider.benchmark.ttft_ms.p99|num('%.2f') }} s|
+------------------------------------------------------------+
| LATENCY (end-to-end)                                       |
+------------------------------------------------------------+
| avg........................{{ provider.benchmark.latency_ms.mean|num('%.2f') }} s|
| p50........................{{ provider.benchmark.latency_ms.p50|num('%.2f') }} s|
| p95........................{{ provider.benchmark.latency_ms.p95|num('%.2f') }} s|
| p99........................{{ provider.benchmark.latency_ms.p99|num('%.2f') }} s|
+------------------------------------------------------------+
| TPOT                                                       |
+------------------------------------------------------------+
| avg........................{{ provider.benchmark.tpot_ms.mean|num('%.2f') }} ms|
| p50........................{{ provider.benchmark.tpot_ms.p50|num('%.2f') }} ms|
| p95........................{{ provider.benchmark.tpot_ms.p95|num('%.2f') }} ms|
+------------------------------------------------------------+
| ITL                                                        |
+------------------------------------------------------------+
| avg........................{{ provider.benchmark.itl_ms.mean|num('%.2f') }} ms|
| p50........................{{ provider.benchmark.itl_ms.p50|num('%.2f') }} ms|
| p95........................{{ provider.benchmark.itl_ms.p95|num('%.2f') }} ms|
+------------------------------------------------------------+
| PER-ROUND BREAKDOWN                                        |
+------------------------------------------------------------+
| Round   Reqs   TTFT avg    Lat avg     Cache               |
| ---------------------------------------------------------- |
{% for round in provider.benchmark.per_round %}|     {{ round.turn }}      {{ round.requests }}    {{ ((round.ttft_avg_ms or 0) / 1000)|num('%.2f') }} s    {{ ((round.latency_avg_ms or 0) / 1000)|num('%.2f') }} s   {{ ((round.cache_hit_rate or 0) * 100)|num('%.4f') }}%            |
{% endfor %}+------------------------------------------------------------+</pre>

  {% if provider.benchmark.baseline %}
  {% set bl = provider.benchmark.baseline %}
  <h4>基线对照</h4>
  <h4 style="color:{{ '#067d3c' if bl.scenario_compliant is true else ('#b42318' if bl.scenario_compliant is false else '#6b7280') }}">场景合规：{{ '通过' if bl.scenario_compliant is true else ('未达标' if bl.scenario_compliant is false else '—') }}</h4>
  <p class="meta">输入 P50={{ bl.input_p50|num('%.0f') if bl.input_p50 is not none else '—' }}{% if bl.input_range[0] is not none or bl.input_range[1] is not none %} 在区间 [{{ bl.input_range[0] or '—' }}, {{ bl.input_range[1] or '—' }}] 内{% else %}{% endif %}</p>
  <h4 style="color:{{ '#067d3c' if bl.all_passed is true else ('#b54708' if bl.all_passed is false else '#6b7280') }}">参考基线：{{ '达标' if bl.all_passed is true else ('未达标 / 需排查' if bl.all_passed is false else '未配置') }}</h4>
  <table>
    <thead><tr><th>指标</th><th>实际 / 阈值</th><th>判定</th></tr></thead>
    <tbody>
    {% for check in bl.checks %}
      <tr>
        <th>{{ check.label }}</th>
        <td>实际 {{ check.actual if check.actual is not none else '—' }} · 阈值 {{ check.threshold_label }}</td>
        <td style="font-weight:700" class="{{ 'ok' if check.passed is true else 'bad' if check.passed is false else '' }}">{{ '达标' if check.passed is true else ('未达标' if check.passed is false else '—') }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}

  {% if provider.benchmark.session_details %}
  <h4>分会话明细（turn1 摘要）</h4>
  <table>
    <thead><tr><th>#</th><th>结果</th><th>stream</th><th>prompt</th><th>cached</th><th>TTFT</th><th>E2E</th><th>TPOT</th><th>error</th></tr></thead>
    <tbody>
    {% for row in provider.benchmark.session_details %}
      <tr>
        <td>{{ row.session }}</td>
        <td class="{{ 'ok' if row.ok else 'bad' }}" style="font-weight:700">{{ 'PASS' if row.ok else 'FAIL' }}</td>
        <td>{{ 'Y' if row.stream else 'N' }}</td>
        <td>{{ row.prompt or '—' }}</td>
        <td>{{ row.cached or '—' }}</td>
        <td>{{ row.ttft_ms|num('%.1f') if row.ttft_ms is not none else '—' }}</td>
        <td>{{ row.e2e_ms|num('%.1f') if row.e2e_ms is not none else '—' }}</td>
        <td>{{ row.tpot_ms|num('%.2f') if row.tpot_ms is not none else '—' }}</td>
        <td class="err">{{ row.error or '' }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}

  <div class="charts" id="chart-grid-{{ loop.index0 }}">
    <div class="chart-card"><h4>① TTFT 分布直方图</h4><div class="chart-box" id="c-ttft-{{ loop.index0 }}"></div></div>
    <div class="chart-card"><h4>② TTFT / E2E 随时间散点</h4><div class="chart-box" id="c-scatter-{{ loop.index0 }}"></div></div>
    <div class="chart-card"><h4>③ 实际 RPM &amp; RPS</h4><div class="chart-box" id="c-rate-{{ loop.index0 }}"></div></div>
    <div class="chart-card"><h4>④ 在途并发数</h4><div class="chart-box" id="c-inflight-{{ loop.index0 }}"></div></div>
    <div class="chart-card"><h4>⑤ 成功 / 失败</h4><div class="chart-box" id="c-outcome-{{ loop.index0 }}"></div></div>
    <div class="chart-card"><h4>⑥ Token 吞吐</h4><div class="chart-box" id="c-tokens-{{ loop.index0 }}"></div></div>
    <div class="chart-card"><h4>⑦ 流式 vs 非流式</h4><div class="chart-box" id="c-stream-{{ loop.index0 }}"></div></div>
    <div class="chart-card"><h4>⑧ 分轮次缓存命中 &amp; TTFT</h4><div class="chart-box" id="c-round-{{ loop.index0 }}"></div></div>
  </div>
  {% endif %}

  {% if provider.concurrency %}
  <h4>并发探针 · 阶梯并发 / 最大稳定并发</h4>
  <p class="meta">最大稳定并发量：<b>{{ provider.concurrency.max_stable_concurrency }}</b></p>
  <table>
    <thead><tr><th>并发级别</th><th>请求数</th><th>成功率</th><th>TTFT p95 (ms)</th><th>输出 TPS</th><th>耗时 (s)</th><th>稳定</th></tr></thead>
    <tbody>
    {% for level in provider.concurrency.levels %}
      <tr>
        <td>{{ level.concurrency }}</td>
        <td>{{ level.requests }}</td>
        <td>{{ '%.1f'|format(level.success_rate * 100) }}%</td>
        <td>{{ level.ttft_p95_ms|num('%.0f') }}</td>
        <td>{{ level.output_tps|num('%.1f') }}</td>
        <td>{{ level.elapsed_seconds|num('%.1f') }}</td>
        <td class="{{ 'ok' if level.stable else 'bad' }}" style="font-weight:700">{{ '✓ 稳定' if level.stable else '✗ 不稳定' }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}

  {% if provider.burst %}
  <h4>突发并发 · 同时请求批次</h4>
  <table>
    <thead><tr><th>批次规模</th><th>成功率</th><th>TTFT p95 (ms)</th><th>E2E p95 (ms)</th><th>耗时 (s)</th></tr></thead>
    <tbody>
    {% for batch in provider.burst.batches %}
      <tr>
        <td>{{ batch.burst_size }}</td>
        <td>{{ '%.1f'|format(batch.success_rate * 100) }}%</td>
        <td>{{ batch.ttft_p95_ms|num('%.0f') }}</td>
        <td>{{ batch.e2e_p95_ms|num('%.0f') }}</td>
        <td>{{ batch.elapsed_seconds|num('%.1f') }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
  {% endif %}

  {% if provider.failures %}
  <h4>失败详情</h4>
  <div class="fail-box">
  {% for failure in provider.failures %}
    <div class="fail-case">
      <h5><code>{{ failure.id }}</code> · {{ failure.name }}</h5>
      <p><b>结果</b>：FAIL · HTTP {{ failure.http or '—' }}</p>
      <p><b>原因</b>：<span class="err">{{ failure.reason }}</span></p>
      {% if failure.evidence %}<p><b>证据 evidence</b></p><pre class="io">{{ failure.evidence|tojson_pretty }}</pre>{% endif %}
      {% if failure.request %}<p><b>请求输入 request</b></p><pre class="io">{{ failure.request|tojson_pretty }}</pre>{% endif %}
      {% if failure.response %}<p><b>响应输出 response</b></p><pre class="io">{{ failure.response|tojson_pretty }}</pre>{% endif %}
    </div>
  {% endfor %}
  </div>
  {% endif %}
</section>
{% endfor %}

{% if view.comparisons %}
<section class="card">
  <h2>Provider comparison</h2>
  {% for comparison in view.comparisons %}
  <h3>{{ comparison.candidate }} vs {{ comparison.reference }}</h3>
  <table><thead><tr><th>Metric</th><th>Reference</th><th>Candidate</th><th>Delta</th><th>Better</th></tr></thead><tbody>
  {% for metric in comparison.metrics %}
    <tr><td>{{ metric.label }}</td><td>{{ metric.reference }}</td><td>{{ metric.candidate }}</td><td>{{ metric.delta|num('%+.4g') }}</td><td class="{{ 'ok' if metric.candidate_better else 'bad' }}">{{ comparison.candidate if metric.candidate_better else comparison.reference }}</td></tr>
  {% endfor %}
  </tbody></table>
  {% endfor %}
</section>
{% endif %}

<script>__ECHARTS_INLINE__</script>
<script>
window.__BENCH_CHARTS = {{ charts_json|safe }};
(function () {
  var D = window.__BENCH_CHARTS || {};
  if (typeof echarts === 'undefined') {
    var grid = document.querySelector('.charts');
    if (grid) grid.insertAdjacentHTML('beforebegin', '<p style="color:#b42318">未能加载 ECharts CDN，图表不可用。</p>');
    return;
  }
  D.forEach(function (provider, idx) {
    var hist = provider.ttft_hist || {};
    function mount(id, opt) { var el = document.getElementById(id); if (!el) return; var c = echarts.init(el); c.setOption(opt); }
    function markP() {
      var pcts = (provider.percentiles || []).filter(function (x) { return x.metric === 'ttft'; })[0] || hist || {};
      return [['p50','#067d3c'],['p90','#b54708'],['p95','#b42318']].map(function (x) {
        return { xAxis: pcts[x[0]], name: x[0], label: { formatter: x[0] }, lineStyle: { color: x[1], type: 'dashed' } };
      });
    }
    mount('c-ttft-' + idx, {
      tooltip: { trigger: 'axis' }, grid: { left: 48, right: 16, top: 28, bottom: 36 },
      xAxis: { type: 'category', data: (hist.bins || []).map(function (b) { return Math.round(b); }), name: 'TTFT ms' },
      yAxis: { type: 'value', name: 'count' },
      series: [{ type: 'bar', data: hist.counts || [], itemStyle: { color: '#3d6b9a' }, markLine: { symbol: 'none', data: markP() } }]
    });
    var scOk = [], scBad = [];
    (provider.scatter || []).forEach(function (p) {
      var row = [p.tMs / 1000, p.ttftMs != null ? p.ttftMs : p.e2eMs];
      (p.ok ? scOk : scBad).push(row);
    });
    mount('c-scatter-' + idx, {
      tooltip: { trigger: 'item' }, legend: { data: ['成功','失败'], top: 0 }, grid: { left: 52, right: 16, top: 28, bottom: 36 },
      xAxis: { type: 'value', name: 't(s)' }, yAxis: { type: 'value', name: 'ms', scale: true },
      series: [
        { name: '成功', type: 'scatter', symbolSize: 6, data: scOk, itemStyle: { color: '#067d3c' } },
        { name: '失败', type: 'scatter', symbolSize: 7, data: scBad, itemStyle: { color: '#b42318' } }
      ]
    });
    mount('c-rate-' + idx, {
      tooltip: { trigger: 'axis' }, legend: { data: ['RPS','RPM'], top: 0 }, grid: { left: 48, right: 48, top: 28, bottom: 36 },
      xAxis: { type: 'value', name: 't(s)' }, yAxis: [{ type: 'value', name: 'rps' }, { type: 'value', name: 'rpm' }],
      series: [
        { name: 'RPS', type: 'line', showSymbol: false, data: (provider.rate_series || []).map(function (d) { return [d.tS, d.rps]; }), itemStyle: { color: '#3d6b9a' } },
        { name: 'RPM', type: 'line', yAxisIndex: 1, showSymbol: false, data: (provider.rate_series || []).map(function (d) { return [d.tS, d.rpm]; }), itemStyle: { color: '#b54708' } }
      ]
    });
    mount('c-inflight-' + idx, {
      tooltip: { trigger: 'axis' }, grid: { left: 48, right: 16, top: 16, bottom: 36 },
      xAxis: { type: 'value', name: 't(s)' }, yAxis: { type: 'value', name: 'inflight', minInterval: 1 },
      series: [{ type: 'line', areaStyle: { opacity: 0.15 }, showSymbol: false, data: (provider.inflight_series || []).map(function (d) { return [d.tS, d.v]; }), itemStyle: { color: '#5a7d4e' } }]
    });
    mount('c-outcome-' + idx, {
      tooltip: { trigger: 'axis' }, legend: { data: ['ok','fail'], top: 0 }, grid: { left: 48, right: 16, top: 28, bottom: 36 },
      xAxis: { type: 'value', name: 't(s)' }, yAxis: { type: 'value', minInterval: 1 },
      series: [
        { name: 'ok', type: 'bar', stack: 'o', data: (provider.outcome_series || []).map(function (d) { return [d.tS, d.ok]; }), itemStyle: { color: '#067d3c' } },
        { name: 'fail', type: 'bar', stack: 'o', data: (provider.outcome_series || []).map(function (d) { return [d.tS, d.fail]; }), itemStyle: { color: '#b42318' } }
      ]
    });
    mount('c-tokens-' + idx, {
      tooltip: { trigger: 'axis' }, legend: { data: ['in','out'], top: 0 }, grid: { left: 56, right: 16, top: 28, bottom: 36 },
      xAxis: { type: 'value', name: 't(s)' }, yAxis: { type: 'value', name: 'tok/s 桶' },
      series: [
        { name: 'in', type: 'line', showSymbol: false, data: (provider.tokens_series || []).map(function (d) { return [d.tS, d.in]; }), itemStyle: { color: '#3d6b9a' } },
        { name: 'out', type: 'line', showSymbol: false, data: (provider.tokens_series || []).map(function (d) { return [d.tS, d.out]; }), itemStyle: { color: '#b54708' } }
      ]
    });
    var sv = provider.stream_vs || [];
    mount('c-stream-' + idx, {
      tooltip: { trigger: 'axis' }, legend: { data: ['stream','nonStream'], top: 0 }, grid: { left: 56, right: 16, top: 28, bottom: 48 },
      xAxis: { type: 'category', data: sv.map(function (r) { return r.metric; }), axisLabel: { interval: 0, rotate: 20 } },
      yAxis: { type: 'log', name: 'ms (log)' },
      series: [
        { name: 'stream', type: 'bar', data: sv.map(function (r) { return r.stream; }), itemStyle: { color: '#3d6b9a' } },
        { name: 'nonStream', type: 'bar', data: sv.map(function (r) { return r.nonStream; }), itemStyle: { color: '#8a6a3d' } }
      ]
    });
    var pr = provider.per_round || [];
    mount('c-round-' + idx, {
      tooltip: { trigger: 'axis' }, legend: { data: ['cache%','TTFT avg'], top: 0 }, grid: { left: 48, right: 48, top: 28, bottom: 36 },
      xAxis: { type: 'category', data: pr.map(function (r) { return 'T' + r.turn; }) },
      yAxis: [{ type: 'value', name: 'cache%', max: 100 }, { type: 'value', name: 'TTFT ms' }],
      series: [
        { name: 'cache%', type: 'bar', data: pr.map(function (r) { return r.cache_hit_rate * 100; }), itemStyle: { color: '#5a7d4e' } },
        { name: 'TTFT avg', type: 'line', yAxisIndex: 1, data: pr.map(function (r) { return r.ttft_avg_ms; }), itemStyle: { color: '#b54708' } }
      ]
    });
  });
  window.addEventListener('resize', function () {});
})();
</script>
<footer class="report-disclaimer" style="margin-top:32px;padding-top:16px;border-top:1px solid #e6e1da;color:#555;font-size:13px;line-height:1.7"><p>本报告由 provider-bench 自动生成，仅供能力验收与对外沟通参考，不构成官方认证或合规背书；测试环境、参数与样本可能影响结果，请以实际业务验证为准。</p></footer>
</body></html>"""


def _charts_json(view: dict[str, Any]) -> str:
    charts = []
    for provider in view["providers"]:
        benchmark = provider.get("benchmark") or {}
        charts.append(benchmark.get("charts") or {})
    return json.dumps(charts, ensure_ascii=False, default=str)


def _load_echarts() -> str:
    """Return the bundled ECharts runtime so reports render charts offline."""
    path = Path(__file__).parent / "echarts.min.js"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return ""


def write_reports(run_dir: Path, result: RunResult) -> None:
    (run_dir / "report.md").write_text(render_markdown(result), encoding="utf-8")
    view = _build_view(run_dir, result)
    environment = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html"]))
    environment.filters["tojson_pretty"] = lambda value: json.dumps(
        value, ensure_ascii=False, indent=2, default=str
    )

    def num(value: Any, spec: str = "%.0f") -> str:
        if value is None:
            return "—"
        try:
            return spec % value
        except (TypeError, ValueError):
            return "—"

    environment.filters["num"] = num
    rendered = environment.from_string(_HTML_TEMPLATE).render(
        view=view,
        charts_json=_charts_json(view),
        errors=_collect_errors(run_dir),
    )
    rendered = rendered.replace("__ECHARTS_INLINE__", _load_echarts())
    (run_dir / "report.html").write_text(rendered, encoding="utf-8")
    write_json(
        run_dir / "scores.json",
        {name: provider.scorecard for name, provider in result.providers.items()},
    )


def regenerate_report(run_dir: Path) -> tuple[Path, Path]:
    result = RunResult.model_validate(read_json(run_dir / "run.json"))
    write_reports(run_dir, result)
    return run_dir / "report.md", run_dir / "report.html"


def compare_runs(first_dir: Path, second_dir: Path) -> dict[str, Any]:
    first = RunResult.model_validate(read_json(first_dir / "run.json"))
    second = RunResult.model_validate(read_json(second_dir / "run.json"))
    providers = sorted(set(first.providers) | set(second.providers))
    comparison = []
    for name in providers:
        before = first.providers.get(name)
        after = second.providers.get(name)
        before_score = before.scorecard.get("score") if before else None
        after_score = after.scorecard.get("score") if after else None
        delta = (
            round(float(after_score) - float(before_score), 2)
            if before_score is not None and after_score is not None
            else None
        )
        comparison.append(
            {
                "provider": name,
                "first_score": before_score,
                "second_score": after_score,
                "delta": delta,
                "first_verdict": before.scorecard.get("verdict") if before else None,
                "second_verdict": after.scorecard.get("verdict") if after else None,
                "plugin_deltas": (
                    _compare_plugin_metrics(before.plugins, after.plugins)
                    if before is not None and after is not None
                    else []
                ),
            }
        )
    return {"first_run": first.run_id, "second_run": second.run_id, "providers": comparison}


def _numeric_metrics(value: dict[str, Any], prefix: str = "") -> dict[str, float]:
    metrics: dict[str, float] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            metrics[path] = float(item)
        elif isinstance(item, dict):
            metrics.update(_numeric_metrics(item, path))
    return metrics


def _compare_plugin_metrics(first: dict[str, Any], second: dict[str, Any]) -> list[dict[str, Any]]:
    deltas = []
    for plugin_name in sorted(set(first) & set(second)):
        before = _numeric_metrics(first[plugin_name].metrics)
        after = _numeric_metrics(second[plugin_name].metrics)
        for metric in sorted(set(before) & set(after)):
            deltas.append(
                {
                    "plugin": plugin_name,
                    "metric": metric,
                    "first": before[metric],
                    "second": after[metric],
                    "delta": after[metric] - before[metric],
                }
            )
    return deltas
