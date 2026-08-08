from __future__ import annotations

import json
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


_HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Provider Bench · {{ result.run_id }}</title>
  <style>
    :root { color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    body { margin: 0; background: #f4f7fb; color: #172033; }
    main { width: min(1120px, calc(100% - 32px)); margin: 36px auto 72px; }
    h1 { margin-bottom: 4px; } .muted { color: #667085; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit,minmax(240px,1fr)); gap: 16px; margin: 24px 0; }
    .card { background: white; border: 1px solid #e4e7ec; border-radius: 14px; padding: 20px; box-shadow: 0 4px 16px #1018280a; }
    .score { font-size: 36px; font-weight: 750; } .PASS { color: #087443; } .WARN { color: #b54708; } .FAIL { color: #b42318; }
    .bar { height: 8px; background: #eef2f6; border-radius: 99px; overflow: hidden; margin-top: 12px; } .bar i { display:block; height:100%; background:#12b76a; }
    .better { color: #087443; font-weight: 700; } .worse { color: #b42318; font-weight: 700; }
    table { border-collapse: collapse; width: 100%; font-size: 14px; } th, td { text-align: left; border-bottom: 1px solid #eaecf0; padding: 10px; vertical-align: top; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; font-size: 12px; }
    section { margin-top: 30px; } details { margin: 10px 0; }
  </style>
</head>
<body><main>
  <h1>API Provider Benchmark</h1>
  <div class="muted">{{ result.run_id }} · {{ result.started_at.isoformat() }} — {{ result.ended_at.isoformat() }}</div>
  <div class="grid">
  {% for provider in result.providers.values() %}
    <article class="card">
      <div class="muted">{{ provider.provider }} · {{ provider.model }}</div>
      <div class="score {{ provider.scorecard.verdict }}">{{ '%.2f'|format(provider.scorecard.score) }}</div>
      <strong class="{{ provider.scorecard.verdict }}">{{ provider.scorecard.verdict }}</strong>
      <div class="bar"><i style="width: {{ provider.scorecard.score }}%"></i></div>
    </article>
  {% endfor %}
  </div>
  {% if result.comparisons %}
  <section>
    <h2>Provider comparison</h2>
    <div class="muted">Reference: {{ result.comparisons.reference }}</div>
    {% for candidate_name, comparison in result.comparisons.candidates.items() %}
    <h3>{{ candidate_name }} vs {{ result.comparisons.reference }}</h3>
    <table><thead><tr><th>Metric</th><th>Reference</th><th>Candidate</th><th>Delta</th><th>Better</th></tr></thead><tbody>
      {% for metric in comparison.metrics %}
      <tr><td>{{ metric.label }}</td><td>{{ metric.reference }}</td><td>{{ metric.candidate }}</td><td>{{ '%+.4g'|format(metric.delta) }}</td><td class="{{ 'better' if metric.candidate_better else 'worse' }}">{{ candidate_name if metric.candidate_better else result.comparisons.reference }}</td></tr>
      {% endfor %}
    </tbody></table>
    {% if comparison.identity %}<p>Behavior similarity: <strong>{{ '%.3f'|format(comparison.identity.behavior_similarity) if comparison.identity.behavior_similarity is not none else '—' }}</strong> · Possible substitution: <strong class="{{ 'FAIL' if comparison.identity.possible_substitution else 'PASS' }}">{{ comparison.identity.possible_substitution }}</strong></p>{% endif %}
    {% endfor %}
  </section>
  {% endif %}
  {% if errors %}
  <section><h2>Error analysis</h2><table><thead><tr><th>Provider</th><th>Case</th><th>Status</th><th>HTTP</th><th>Error</th></tr></thead><tbody>
  {% for error in errors %}<tr><td>{{ error.provider }}</td><td>{{ error.case_id }}</td><td class="FAIL">{{ error.status }}</td><td>{{ error.status_code or '—' }}</td><td>{{ error.error or 'No error body' }}</td></tr>{% endfor %}
  </tbody></table></section>
  {% endif %}
  {% for provider in result.providers.values() %}
  <section>
    <h2>{{ provider.provider }}</h2>
    <table><thead><tr><th>Plugin</th><th>Status</th><th>Requests</th><th>Metrics</th></tr></thead><tbody>
    {% for plugin in provider.plugins.values() %}
      <tr><td>{{ plugin.name }}</td><td>{{ plugin.status.value }}</td><td>{{ plugin.request_count }}</td><td><details><summary>View metrics</summary><pre>{{ plugin.metrics | tojson(indent=2) }}</pre></details>{% if plugin.error %}<div class="FAIL">{{ plugin.error }}</div>{% endif %}</td></tr>
    {% endfor %}
    </tbody></table>
  </section>
  {% endfor %}
</main></body></html>"""


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


def write_reports(run_dir: Path, result: RunResult) -> None:
    (run_dir / "report.md").write_text(render_markdown(result), encoding="utf-8")
    environment = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html"]))
    rendered = environment.from_string(_HTML_TEMPLATE).render(
        result=result, errors=_collect_errors(run_dir)
    )
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
