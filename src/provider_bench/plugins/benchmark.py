from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from pydantic import Field, model_validator

from provider_bench.cache import extract_cache_usage
from provider_bench.models import RequestRecord
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin
from provider_bench.plugins.stats import percentile

_FILLER_SENTENCE = (
    "The quick brown fox jumps over the lazy dog while the patient observer "
    "watches quietly and the river keeps flowing. "
)


def _filler_chars(token_target: int) -> str:
    length = max(token_target, 1) * 4
    return (_FILLER_SENTENCE * (length // len(_FILLER_SENTENCE) + 1))[:length]


class BenchmarkSettings(PluginSettings):
    sessions: int = Field(default=4, gt=0)
    turns: int = Field(default=3, gt=0)
    init_tokens: int = Field(default=32000, gt=0)
    output_tokens: int = Field(default=346, gt=0)
    max_inflight: int = Field(default=1, gt=0)
    arrival_start: float = Field(default=0.08, gt=0)
    arrival_end: float = Field(default=0.2, gt=0)
    ramp_seconds: float = Field(default=15.0, ge=0)
    turn_interval: float = Field(default=0.0, ge=0)
    prompt: str = "请根据上下文文档，用一句话总结其主要内容。"
    system_prompt: str | None = "You are a helpful assistant. Context document follows."
    baseline_rps_min: float | None = Field(default=0.6)
    baseline_ttft_p50_max_ms: float | None = Field(default=15000)
    baseline_tpot_p50_max_ms: float | None = Field(default=35)
    baseline_cache_hit_rate_min: float | None = Field(default=0.6)
    scenario_input_tokens_min: int | None = Field(default=4000)
    scenario_input_tokens_max: int | None = Field(default=80000)

    @model_validator(mode="after")
    def validate_benchmark(self) -> BenchmarkSettings:
        if self.arrival_end < self.arrival_start:
            raise ValueError("arrival_end cannot be less than arrival_start")
        return self


@register_plugin
class LoadBenchmarkPlugin(BenchmarkPlugin[BenchmarkSettings]):
    name = "benchmark"
    description = "Session/turn, arrival-rate driven load benchmark with TTFT/TPOT/ITL/cache metrics"
    settings_model = BenchmarkSettings

    def _arrival_rate(self, elapsed: float) -> float:
        start = self.settings.arrival_start
        end = self.settings.arrival_end
        ramp = self.settings.ramp_seconds
        if ramp <= 0:
            return end
        return start + (end - start) * min(elapsed / ramp, 1.0)

    def _new_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.settings.system_prompt:
            messages.append({"role": "system", "content": self.settings.system_prompt})
        filler = _filler_chars(self.settings.init_tokens)
        messages.append({"role": "user", "content": filler + "\n\n" + self.settings.prompt})
        return messages

    @staticmethod
    def _advance(messages: list[dict[str, Any]], record: RequestRecord) -> list[dict[str, Any]]:
        content = record.response.get("content") or ""
        next_messages = [*messages, {"role": "assistant", "content": content}]
        next_messages.append(
            {"role": "user", "content": "请继续，用一句话补充说明。"}
        )
        return next_messages

    async def run(self) -> dict[str, Any]:
        started = time.perf_counter()
        semaphore = asyncio.Semaphore(self.settings.max_inflight)
        dispatch_lock = asyncio.Lock()
        conversations = [self._new_messages() for _ in range(self.settings.sessions)]
        results: list[dict[str, Any]] = []
        next_dispatch = 0.0
        done_count = 0
        done = asyncio.Event()

        async def worker(session: int) -> None:
            nonlocal next_dispatch, done_count
            messages = conversations[session]
            for turn in range(self.settings.turns):
                async with dispatch_lock:
                    now = time.perf_counter() - started
                    if now < next_dispatch:
                        await asyncio.sleep(next_dispatch - now)
                        now = time.perf_counter() - started
                    next_dispatch += 1.0 / self._arrival_rate(max(now, 0.0))
                async with semaphore:
                    record = await self.context.provider.chat(
                        case_id=f"benchmark.s{session}.t{turn + 1}",
                        messages=messages,
                        stream=True,
                        max_tokens=self.settings.output_tokens,
                        extra={"stream_options": {"include_usage": True}},
                    )
                    await self.context.record(record)
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    results.append(self._result(session, turn + 1, elapsed_ms, record))
                    messages = self._advance(messages, record)
                if self.settings.turn_interval:
                    await asyncio.sleep(self.settings.turn_interval)
            done_count += 1
            if done_count == self.settings.sessions:
                done.set()

        workers = [asyncio.create_task(worker(session)) for session in range(self.settings.sessions)]
        await done.wait()
        await asyncio.gather(*workers)
        return {
            "results": results,
            "duration_seconds": time.perf_counter() - started,
            "stop_reason": "all_sessions_completed",
        }

    @staticmethod
    def _result(
        session: int, turn: int, elapsed_ms: float, record: RequestRecord
    ) -> dict[str, Any]:
        usage = extract_cache_usage(record.response.get("usage"))
        if usage["prompt_tokens"] is None and record.usage is not None:
            usage = extract_cache_usage(record.usage)
        return {
            "session": session,
            "turn": turn,
            "t_ms": elapsed_ms,
            "ttft_ms": record.ttft_ms,
            "ttfb_ms": record.ttfb_ms,
            "e2e_ms": record.e2e_ms,
            "tpot_ms": record.tpot_ms,
            "itl_ms": record.itl_ms,
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "cache_read_tokens": usage["cache_read_tokens"] or 0,
            "cache_write_tokens": usage["cache_write_tokens"],
            "ok": record.status == "success",
            "stream": True,
            "error": record.error,
        }

    @staticmethod
    def _dist(results: list[dict[str, Any]], key: str) -> dict[str, float | None]:
        values = [result[key] for result in results if result.get(key) is not None]
        if not values:
            return {"mean": None, "p50": None, "p75": None, "p90": None, "p95": None, "p99": None}
        values.sort()
        return {
            "mean": sum(values) / len(values),
            "p50": percentile(values, 0.50),
            "p75": percentile(values, 0.75),
            "p90": percentile(values, 0.90),
            "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99),
        }

    @staticmethod
    def _histogram(values: list[float]) -> dict[str, Any]:
        samples = [value for value in values if value is not None]
        if not samples:
            return {"bins": [], "counts": []}
        low, high = min(samples), max(samples)
        if low == high:
            high = low + 1
        bin_count = 14
        width = (high - low) / bin_count
        bins = [low + width * i for i in range(bin_count + 1)]
        counts = [0] * bin_count
        for value in samples:
            index = min(int((value - low) / width), bin_count - 1)
            counts[index] += 1
        return {"bins": bins, "counts": counts}

    def aggregate(self, raw_result: dict[str, Any]) -> dict[str, Any]:
        results: list[dict[str, Any]] = raw_result["results"]
        duration = raw_result["duration_seconds"]
        ok = [result for result in results if result["ok"]]

        total = len(results)
        success_count = len(ok)
        success_rate = success_count / total if total else 0.0

        prompt_tokens = [result["prompt_tokens"] for result in ok if result["prompt_tokens"]]
        output_tokens = [result["completion_tokens"] for result in ok if result["completion_tokens"]]
        avg_prompt = sum(prompt_tokens) / len(prompt_tokens) if prompt_tokens else 0.0
        avg_output = sum(output_tokens) / len(output_tokens) if output_tokens else 0.0

        cache_read = sum(result["cache_read_tokens"] for result in ok)
        cache_total_prompt = sum(result["prompt_tokens"] or 0 for result in ok)
        cache_hit_rounds = sum(1 for result in ok if result["cache_read_tokens"] > 0)
        cache_hit_rate = cache_hit_rounds / len(ok) if ok else 0.0
        coverage = cache_read / cache_total_prompt if cache_total_prompt else 0.0

        steady_turns = [result for result in ok if result["turn"] >= 2]
        steady_cache_read = sum(result["cache_read_tokens"] for result in steady_turns)
        steady_cache_prompt = sum(result["prompt_tokens"] or 0 for result in steady_turns)
        coverage_steady = steady_cache_read / steady_cache_prompt if steady_cache_prompt else 0.0
        input_p50 = percentile(prompt_tokens, 0.5) if prompt_tokens else None

        steady_ok = [
            result
            for result in ok
            if result["t_ms"] / 1000.0 >= self.settings.ramp_seconds
        ]
        rps = total / duration if duration else 0.0
        steady_rps = len(steady_ok) / max(duration - self.settings.ramp_seconds, 1e-6)
        input_tps = sum(prompt_tokens) / duration if duration else 0.0
        output_tps = sum(output_tokens) / duration if duration else 0.0
        input_tpm = input_tps * 60
        output_tpm = output_tps * 60
        peak_tpm = input_tpm

        ttft = self._dist(ok, "ttft_ms")
        latency = self._dist(ok, "e2e_ms")
        tpot = self._dist(ok, "tpot_ms")
        itl_values = [value for result in ok for value in result["itl_ms"]]
        itl = self._dist([{"itl_ms": value} for value in itl_values], "itl_ms")

        per_round = []
        for turn in range(1, self.settings.turns + 1):
            turn_results = [result for result in results if result["turn"] == turn]
            turn_ok = [result for result in turn_results if result["ok"]]
            turn_prompt = sum(result["prompt_tokens"] or 0 for result in turn_ok)
            turn_cache = sum(result["cache_read_tokens"] for result in turn_ok)
            turn_hit_rate = (
                sum(1 for result in turn_ok if result["cache_read_tokens"] > 0) / len(turn_ok)
                if turn_ok
                else 0.0
            )
            per_round.append(
                {
                    "turn": turn,
                    "requests": len(turn_results),
                    "success": len(turn_ok),
                    "prompt": turn_prompt,
                    "cached": turn_cache,
                    "cache_hit_rate": turn_hit_rate,
                    "ttft_avg_ms": self._dist(turn_ok, "ttft_ms")["mean"],
                    "latency_avg_ms": self._dist(turn_ok, "e2e_ms")["mean"],
                }
            )

        baseline_checks: list[dict[str, Any]] = []

        def baseline_check(
            label: str,
            actual: float | None,
            threshold: float | None,
            operator: str,
            formatter: Any = None,
        ) -> None:
            if threshold is None:
                return
            if actual is None:
                baseline_checks.append(
                    {"label": label, "actual": None, "threshold_label": f"{operator} {formatter(threshold) if formatter else threshold}", "passed": None}
                )
                return
            passed = {
                ">=": actual >= threshold,
                "<=": actual <= threshold,
                "<": actual < threshold,
            }[operator]
            baseline_checks.append(
                {
                    "label": label,
                    "actual": formatter(actual) if formatter else actual,
                    "threshold_label": f"{operator} {formatter(threshold) if formatter else threshold}",
                    "passed": passed,
                }
            )

        baseline_check(
            "吞吐 rps（判定用：全时段）", rps, self.settings.baseline_rps_min, ">="
        )
        baseline_check(
            "TTFT P50",
            ttft.get("p50"),
            self.settings.baseline_ttft_p50_max_ms,
            "<=",
            lambda value: f"{value:.0f} ms",
        )
        baseline_check(
            "TPOT P50",
            tpot.get("p50"),
            self.settings.baseline_tpot_p50_max_ms,
            "<",
            lambda value: f"{value:.2f} ms",
        )
        baseline_check(
            "稳态缓存命中率（token 覆盖，turn≥2）",
            coverage_steady if steady_turns else None,
            self.settings.baseline_cache_hit_rate_min,
            ">=",
            lambda value: f"{value * 100:.1f}%",
        )
        scenario_compliant = None
        if input_p50 is not None:
            scenario_compliant = True
            if self.settings.scenario_input_tokens_min is not None:
                scenario_compliant = scenario_compliant and input_p50 >= self.settings.scenario_input_tokens_min
            if self.settings.scenario_input_tokens_max is not None:
                scenario_compliant = scenario_compliant and input_p50 <= self.settings.scenario_input_tokens_max
        baseline = {
            "scenario_compliant": scenario_compliant,
            "input_p50": input_p50,
            "input_range": [
                self.settings.scenario_input_tokens_min,
                self.settings.scenario_input_tokens_max,
            ],
            "checks": baseline_checks,
            "all_passed": (
                all(check["passed"] for check in baseline_checks if check["passed"] is not None)
                if baseline_checks
                else None
            ),
        }

        session_details = []
        for session in range(self.settings.sessions):
            first = next(
                (result for result in results if result["session"] == session and result["turn"] == 1),
                None,
            )
            if first is None:
                continue
            session_details.append(
                {
                    "session": session,
                    "turn": first["turn"],
                    "ok": first["ok"],
                    "stream": first["stream"],
                    "prompt": first["prompt_tokens"],
                    "cached": first["cache_read_tokens"],
                    "ttft_ms": first["ttft_ms"],
                    "e2e_ms": first["e2e_ms"],
                    "tpot_ms": first["tpot_ms"],
                    "error": first["error"],
                }
            )

        ttft_samples = [result["ttft_ms"] for result in ok if result["ttft_ms"] is not None]
        scatter = [
            {
                "tMs": result["t_ms"],
                "ttftMs": result["ttft_ms"],
                "e2eMs": result["e2e_ms"],
                "ok": result["ok"],
                "stream": result["stream"],
                "turn": result["turn"],
            }
            for result in results
        ]
        charts = {
            "ttft_hist": self._histogram(ttft_samples),
            "scatter": scatter,
            "rate_series": self._rate_series(results, duration),
            "inflight_series": self._inflight_series(results, duration),
            "outcome_series": self._outcome_series(results, duration),
            "tokens_series": self._tokens_series(results, duration),
            "stream_vs": self._stream_vs(results),
            "per_round": per_round,
            "percentiles": [
                {"metric": "ttft", **ttft},
                {"metric": "e2e", **latency},
                {"metric": "tpot", **tpot},
            ],
        }

        return {
            "total_requests": total,
            "success_rate": success_rate,
            "duration_seconds": duration,
            "avg_prompt_tokens": avg_prompt,
            "avg_output_tokens": avg_output,
            "cache_hit_rate": cache_hit_rate,
            "coverage": coverage,
            "coverage_steady": coverage_steady,
            "input_p50": input_p50,
            "saved_tokens": cache_read,
            "stop_reason": raw_result["stop_reason"],
            "baseline": baseline,
            "session_details": session_details,
            "rps": rps,
            "rps_steady": steady_rps,
            "input_tokens_per_second": input_tps,
            "output_tokens_per_second": output_tps,
            "input_tpm": input_tpm,
            "output_tpm": output_tpm,
            "peak_tpm": peak_tpm,
            "ttft_ms": ttft,
            "latency_ms": latency,
            "tpot_ms": tpot,
            "itl_ms": itl,
            "per_round": per_round,
            "charts": charts,
        }

    @staticmethod
    def _bucket_series(results: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
        seconds = int(math.ceil(duration)) + 1
        return [{"tS": float(second)} for second in range(seconds)]

    def _rate_series(self, results: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
        series = self._bucket_series(results, duration)
        for result in results:
            second = int(result["t_ms"] // 1000)
            if 0 <= second < len(series):
                series[second].setdefault("rpm", 0.0)
                series[second]["rpm"] += 6.0
        for point in series:
            point["rps"] = point.get("rpm", 0.0) / 60.0
        return series

    def _inflight_series(self, results: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
        series = self._bucket_series(results, duration)
        events: list[tuple[float, int]] = []
        for result in results:
            start = result["t_ms"]
            end = start + (result["e2e_ms"] or 0)
            events.append((start, 1))
            events.append((end, -1))
        events.sort()
        index = 0
        inflight = 0
        for point in series:
            second = point["tS"] * 1000
            while index < len(events) and events[index][0] <= second:
                inflight += events[index][1]
                index += 1
            point["v"] = inflight
        return series

    def _outcome_series(self, results: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
        series = self._bucket_series(results, duration)
        for point in series:
            point["ok"] = 0
            point["fail"] = 0
        for result in results:
            second = int(result["t_ms"] // 1000)
            if 0 <= second < len(series):
                if result["ok"]:
                    series[second]["ok"] += 1
                else:
                    series[second]["fail"] += 1
        return series

    def _tokens_series(self, results: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
        series = self._bucket_series(results, duration)
        for point in series:
            point["in"] = 0.0
            point["out"] = 0.0
            point["v"] = 0.0
        for result in results:
            second = int(result["t_ms"] // 1000)
            if 0 <= second < len(series):
                series[second]["in"] += result["prompt_tokens"] or 0
                series[second]["out"] += result["completion_tokens"] or 0
        for point in series:
            point["v"] = point["in"] + point["out"]
        return series

    @staticmethod
    def _stream_vs(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ok = [result for result in results if result["ok"]]
        stream = [result for result in ok if result["stream"]]
        non_stream = [result for result in ok if not result["stream"]]

        def mean(values: list[dict[str, Any]], key: str) -> float | None:
            samples = [value[key] for value in values if value.get(key) is not None]
            return sum(samples) / len(samples) if samples else None

        return [
            {"metric": "TTFT/首包(ms)", "stream": mean(stream, "ttft_ms"), "nonStream": mean(non_stream, "ttft_ms")},
            {"metric": "E2E(ms)", "stream": mean(stream, "e2e_ms"), "nonStream": mean(non_stream, "e2e_ms")},
            {"metric": "TPOT(ms)", "stream": mean(stream, "tpot_ms"), "nonStream": mean(non_stream, "tpot_ms")},
        ]
