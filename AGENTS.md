# AGENTS.md

This repo is **provider-bench**, a Python tool for benchmarking OpenAI-compatible model APIs.

## When the user asks to test / benchmark / evaluate a model or provider API

Load the skill at [`skills/model-benchmark/SKILL.md`](skills/model-benchmark/SKILL.md) and follow it. It walks through collecting the API facts, writing `benchmark.yaml` with the full suite, running the benchmark, and verifying the generated `report.html`.

Quick reference:

```bash
provider-bench init                 # write a starter config
provider-bench validate benchmark.yaml
provider-bench run benchmark.yaml   # run + emit outputs/<run-id>/report.html
provider-bench report <run-id>      # regenerate reports without re-running
provider-bench compare <a> <b>      # compare two runs
provider-bench web                  # bundled web console
```

## Development conventions

- Python 3.11+; tests with `pytest` (run with a clean `PYTHONPATH` if ROS pollutes it):
  `env -u PYTHONPATH python -m pytest -q`
- Type check with `python -m mypy src/provider_bench`.
- New benchmarks are plugins under `src/provider_bench/plugins/` (subclass `BenchmarkPlugin`, decorate `@register_plugin`).
- Reports are rendered by `src/provider_bench/report.py` into a unified HTML.
