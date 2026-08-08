from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from provider_bench.config import EXAMPLE_CONFIG, load_config
from provider_bench.engine import BenchmarkEngine
from provider_bench.plugins.registry import registry
from provider_bench.report import compare_runs, regenerate_report

app = typer.Typer(
    name="provider-bench",
    help="Benchmark OpenAI-compatible API providers.",
    no_args_is_help=True,
)


def _run_dir(reference: str, output_dir: Path) -> Path:
    direct = Path(reference)
    return direct if direct.is_dir() else output_dir / reference


@app.command()
def init(
    path: Annotated[Path, typer.Argument(help="Configuration file to create")] = Path(
        "benchmark.yaml"
    ),
) -> None:
    """Create a documented starter configuration."""
    if path.exists():
        raise typer.BadParameter(f"{path} already exists; refusing to overwrite it")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    typer.echo(f"Created {path}")


@app.command()
def validate(config: Annotated[Path, typer.Argument(exists=True, readable=True)]) -> None:
    """Validate providers and every enabled plugin configuration without making API calls."""
    try:
        loaded = load_config(config)
        engine = BenchmarkEngine()
        selected = engine.selected_plugins(loaded)
        for name in selected:
            settings = loaded.benchmarks[name]
            if settings.get("enabled", True):
                registry.get(name).validate_config(settings)
    except (OSError, ValueError, ValidationError, KeyError) as exc:
        typer.echo(f"Invalid configuration: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"Valid: {len(loaded.selected_providers)} provider(s), "
        f"{sum(loaded.benchmarks[name].get('enabled', True) for name in selected)} enabled plugin(s)"
    )


@app.command("run")
def run_benchmarks(
    config: Annotated[Path, typer.Argument(exists=True, readable=True)],
    only: Annotated[
        list[str] | None, typer.Option("--only", help="Run only this plugin; repeatable")
    ] = None,
    skip: Annotated[
        list[str] | None, typer.Option("--skip", help="Skip this plugin; repeatable")
    ] = None,
) -> None:
    """Execute configured benchmarks and generate JSON, Markdown and HTML reports."""
    try:
        loaded = load_config(config)

        async def show_event(event: dict[str, object]) -> None:
            if event["type"] in {
                "run.started",
                "provider.started",
                "plugin.started",
                "plugin.completed",
                "run.completed",
            }:
                parts = [str(event["type"])]
                for key in ("provider", "plugin", "status"):
                    if key in event:
                        parts.append(f"{key}={event[key]}")
                typer.echo(" · ".join(parts))

        result = asyncio.run(
            BenchmarkEngine().run(
                loaded,
                only=set(only or []),
                skip=set(skip or []),
                event_handler=show_event,
            )
        )
    except (OSError, ValueError, ValidationError, KeyError) as exc:
        typer.echo(f"Benchmark could not start: {exc}", err=True)
        raise typer.Exit(1) from exc
    run_dir = loaded.output_dir / result.run_id
    typer.echo(f"Report: {run_dir / 'report.html'}")
    typer.echo(f"Result: {result.status.value}")
    if result.status.value == "FAILED":
        raise typer.Exit(2)


@app.command()
def report(
    run_id: Annotated[str, typer.Argument(help="Run ID or run directory")],
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path("outputs"),
) -> None:
    """Regenerate Markdown and HTML reports from a completed run."""
    run_dir = _run_dir(run_id, output_dir)
    if not (run_dir / "run.json").is_file():
        raise typer.BadParameter(f"run data not found in {run_dir}")
    markdown, html = regenerate_report(run_dir)
    typer.echo(f"Generated {markdown} and {html}")


@app.command()
def compare(
    run_a: Annotated[str, typer.Argument(help="First run ID or directory")],
    run_b: Annotated[str, typer.Argument(help="Second run ID or directory")],
    output_dir: Annotated[Path, typer.Option("--output-dir")] = Path("outputs"),
) -> None:
    """Compare provider scores and verdicts from two runs."""
    first = _run_dir(run_a, output_dir)
    second = _run_dir(run_b, output_dir)
    for run_dir in (first, second):
        if not (run_dir / "run.json").is_file():
            raise typer.BadParameter(f"run data not found in {run_dir}")
    typer.echo(json.dumps(compare_runs(first, second), ensure_ascii=False, indent=2))


@app.command()
def web(
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option()] = 8000,
    reload: Annotated[bool, typer.Option()] = False,
) -> None:
    """Start the FastAPI backend and bundled React console."""
    import uvicorn

    uvicorn.run("provider_bench.web.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
