from __future__ import annotations

import ast
import asyncio
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal, InvalidOperation

from provider_bench.evaluators.base import EvaluationResult, EvaluatorContext
from provider_bench.quality.dataset import QualityCase
from provider_bench.validation import validate_json_schema

Evaluator = Callable[[QualityCase, str, EvaluatorContext], Awaitable[EvaluationResult]]
_EVALUATORS: dict[str, Evaluator] = {}


def evaluator(name: str) -> Callable[[Evaluator], Evaluator]:
    def register(function: Evaluator) -> Evaluator:
        _EVALUATORS[name] = function
        return function

    return register


def evaluator_names() -> list[str]:
    return sorted(_EVALUATORS)


async def evaluate_case(
    case: QualityCase, response: str, context: EvaluatorContext
) -> EvaluationResult:
    try:
        selected = _EVALUATORS[case.evaluator]
    except KeyError as exc:
        raise ValueError(f"unknown evaluator {case.evaluator!r}") from exc
    return await selected(case, response, context)


def _normalize(text: str) -> str:
    return " ".join(text.strip().casefold().split())


@evaluator("exact_match")
async def exact_match(
    case: QualityCase, response: str, context: EvaluatorContext
) -> EvaluationResult:
    expected = str(case.expected)
    passed = _normalize(response) == _normalize(expected)
    return EvaluationResult(
        passed=passed,
        score=float(passed),
        reason=None if passed else f"expected exact value {expected!r}",
    )


@evaluator("contains")
async def contains_match(
    case: QualityCase, response: str, context: EvaluatorContext
) -> EvaluationResult:
    expected = _normalize(str(case.expected))
    passed = bool(expected) and expected in _normalize(response)
    return EvaluationResult(
        passed=passed,
        score=float(passed),
        reason=None if passed else f"expected response containing {expected!r}",
    )


_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


@evaluator("numeric")
async def numeric(
    case: QualityCase, response: str, context: EvaluatorContext
) -> EvaluationResult:
    match = _NUMBER.search(response.replace(",", ""))
    try:
        actual = Decimal(match.group(0)) if match else None
        expected = Decimal(str(case.expected))
        tolerance = Decimal(str(case.tolerance))
    except InvalidOperation:
        actual = None
        expected = Decimal(0)
        tolerance = Decimal(0)
    passed = actual is not None and abs(actual - expected) <= tolerance
    return EvaluationResult(
        passed=passed,
        score=float(passed),
        reason=None if passed else f"expected {expected} ± {tolerance}, got {actual}",
        details={"actual": float(actual) if actual is not None else None},
    )


@evaluator("json_validator")
async def json_validator(
    case: QualityCase, response: str, context: EvaluatorContext
) -> EvaluationResult:
    try:
        value = json.loads(response)
    except (json.JSONDecodeError, TypeError) as exc:
        return EvaluationResult(passed=False, score=0, reason=f"invalid JSON: {exc}")
    errors = validate_json_schema(value, case.json_schema) if case.json_schema else []
    if case.expected is not None and value != case.expected:
        errors.append("JSON value differs from expected value")
    return EvaluationResult(
        passed=not errors,
        score=float(not errors),
        reason="; ".join(errors) or None,
        details={"parsed": value},
    )


def _extract_code(response: str) -> str:
    fenced = re.search(r"```(?:python)?\s*(.*?)```", response, re.DOTALL | re.IGNORECASE)
    return fenced.group(1).strip() if fenced else response.strip()


def _validate_code_ast(code: str) -> str | None:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return f"syntax error: {exc.msg} at line {exc.lineno}"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            return f"disallowed syntax: {type(node).__name__}"
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            return f"disallowed private attribute: {node.attr}"
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            return f"disallowed private name: {node.id}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {
            "eval",
            "exec",
            "open",
            "compile",
            "__import__",
            "globals",
            "locals",
            "vars",
            "getattr",
            "setattr",
            "delattr",
            "input",
            "breakpoint",
            "help",
        }:
            return f"disallowed function: {node.func.id}"
    return None


@evaluator("code_test")
async def code_test(
    case: QualityCase, response: str, context: EvaluatorContext
) -> EvaluationResult:
    code = _extract_code(response)
    validation_error = _validate_code_ast(code)
    if validation_error:
        return EvaluationResult(passed=False, score=0, reason=validation_error)
    script = code + "\n" + "\n".join(case.code_tests)
    if os.name == "posix":
        cpu_seconds = max(1, math.ceil(case.code_timeout_seconds))
        memory_bytes = case.code_memory_mb * 1024 * 1024
        script = (
            "import resource\n"
            f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu_seconds}, {cpu_seconds}))\n"
            f"resource.setrlimit(resource.RLIMIT_AS, ({memory_bytes}, {memory_bytes}))\n"
            "resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))\n"
            + script
        )
    process = subprocess.Popen(
        [sys.executable, "-I", "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + case.code_timeout_seconds
    while process.poll() is None and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    if process.poll() is None:
        process.kill()
        process.communicate()
        return EvaluationResult(passed=False, score=0, reason="code test timed out")
    stdout, stderr = process.communicate()
    passed = process.returncode == 0
    return EvaluationResult(
        passed=passed,
        score=float(passed),
        reason=None if passed else stderr.decode(errors="replace")[-1000:],
        details={"stdout": stdout.decode(errors="replace")[-1000:]},
    )


@evaluator("llm_judge")
async def llm_judge(
    case: QualityCase, response: str, context: EvaluatorContext
) -> EvaluationResult:
    judge = context.plugin.judge_provider or context.plugin.provider
    prompt = (
        "You are a strict evaluator. Score the candidate answer against the rubric. "
        "Return JSON with passed (boolean), score (number 0..1), and reason (string).\n\n"
        f"Question:\n{case.prompt}\n\nRubric:\n{case.rubric}\n\nCandidate answer:\n{response}"
    )
    judge_max_tokens = getattr(context.settings, "max_tokens", None) or 0
    record = await judge.chat(
        case_id=f"quality.judge.{case.id}",
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        max_tokens=max(judge_max_tokens, case.max_tokens, 512),
        response_format={"type": "json_object"},
    )
    await context.plugin.record(record)
    if record.status != "success":
        return EvaluationResult(passed=False, score=0, reason=f"judge failed: {record.error}")
    try:
        verdict = json.loads(record.response.get("content", ""))
        score = max(0.0, min(1.0, float(verdict["score"])))
        passed = bool(verdict.get("passed", score >= case.pass_threshold))
        return EvaluationResult(
            passed=passed and score >= case.pass_threshold,
            score=score,
            reason=str(verdict.get("reason") or "") or None,
            details={"judge_provider": judge.name},
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return EvaluationResult(passed=False, score=0, reason=f"invalid judge output: {exc}")
