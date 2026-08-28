from __future__ import annotations

from pathlib import Path

from provider_bench.evaluators import EvaluatorContext, evaluate_case
from provider_bench.plugins.base import PluginContext
from provider_bench.quality import QualityCase

from .fakes import FakeProvider


async def _ignore(event: dict[str, object]) -> None:
    return None


def _context(tmp_path: Path) -> EvaluatorContext:
    provider = FakeProvider()
    return EvaluatorContext(
        plugin=PluginContext(
            run_id="evaluator",
            provider=provider,
            judge_provider=provider,
            output_dir=tmp_path,
            emit=_ignore,
        )
    )


async def test_numeric_and_json_evaluators(tmp_path: Path) -> None:
    numeric = QualityCase(
        id="numeric", category="math", prompt="x", evaluator="numeric", expected=42, tolerance=0.1
    )
    json_case = QualityCase(
        id="json",
        category="json",
        prompt="x",
        evaluator="json_validator",
        json_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    )
    assert (await evaluate_case(numeric, "The answer is 42.05", _context(tmp_path))).passed
    invalid = await evaluate_case(json_case, '{"count":"three"}', _context(tmp_path))
    assert not invalid.passed
    assert "integer" in (invalid.reason or "")


async def test_code_evaluator_runs_tests_and_rejects_imports(tmp_path: Path) -> None:
    case = QualityCase(
        id="code",
        category="code",
        prompt="x",
        evaluator="code_test",
        code_tests=["assert add(2, 3) == 5"],
    )
    passed = await evaluate_case(case, "def add(a, b):\n    return a + b", _context(tmp_path))
    rejected = await evaluate_case(case, "import os\ndef add(a,b): return a+b", _context(tmp_path))
    assert passed.passed
    assert not rejected.passed
    assert "disallowed" in (rejected.reason or "")


async def test_contains_evaluator_accepts_wrapped_answer(tmp_path: Path) -> None:
    case = QualityCase(
        id="knowledge",
        category="chinese_knowledge",
        prompt="x",
        evaluator="contains",
        expected="王勃",
    )
    assert (await evaluate_case(case, "《滕王阁序》的作者是王勃。", _context(tmp_path))).passed
    wrong = await evaluate_case(case, "《滕王阁序》的作者是李白。", _context(tmp_path))
    assert not wrong.passed
    assert "王勃" in (wrong.reason or "")

