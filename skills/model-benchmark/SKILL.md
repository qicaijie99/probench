---
name: model-benchmark
description: Benchmark an OpenAI-compatible model API with this repo's provider-bench tool and produce a unified functional + performance + comparison report. Use when the user asks to test, benchmark, evaluate, or accept a model/provider API, hands over a base URL and API key, or says 测试模型 / 压测 / 接入验收 / 出报告.
---

# Model benchmark

Turn an OpenAI-compatible API into a functional + performance + comparison report, using this repo's `provider-bench` tool. The finished report is a single `report.html` that mirrors a reference format: scorecard, functional sub-items, per-case results, cache-hit rate, latency percentiles, a session/turn load benchmark with charts, and (multi-provider) comparison.

## Steps

### 1. Collect provider facts
Gather from the user (ask only what is missing):

- `base_url` — e.g. `https://tokens.example.com/v1`
- `api_key` — keep in an environment variable, never write it into a report
- `model` — the model name to test
- optional: provider `name` (for the report label) and a `reference_provider` when comparing two providers

Completion criterion: all of `base_url`, `api_key`, `model` are known, and any second provider (for comparison) is known too.

### 2. Write the config
Create `benchmark.yaml` enabling the **full suite** — the union of what the reference report shows:

```yaml
provider:
  name: candidate
  base_url: ${API_BASE_URL}
  api_key: ${API_KEY}
  model: ${API_MODEL}

# For a two-provider comparison, list both under `providers:` instead and set
# reference_provider to one name.

benchmarks:
  compatibility: { enabled: true }
  protocol:
    enabled: true
    checks: [ping, stream_integrity, usage_stream, image_base64, video_base64]
    max_tokens: 512
  features:
    enabled: true
    reasoning_effort_levels: [low, high, max]
    expect_reasoning_by_default: true
  cache:
    enabled: true
    prefix_chars: 4096
    rounds: 2
    warmup: true
  tool_calling:
    enabled: true
    branches: [default, auto, required, none, function, allowed_tools]
  structured_output: { enabled: true, strict: true }
  model_identity: { enabled: true, repetitions: 2 }
  billing: { enabled: true, max_tokens: 512 }
  quality: { enabled: true }
  latency: { enabled: true, warmup: 1, repetitions: 10, max_tokens: 512 }
  benchmark:
    enabled: true
    sessions: 4
    turns: 3
    init_tokens: 32000
    output_tokens: 346
    max_inflight: 1
    arrival_start: 0.08
    arrival_end: 0.2
    ramp_seconds: 15.0
    # 基线对照（参考报告口径，可按目标模型调整）：
    baseline_rps_min: 0.6
    baseline_ttft_p50_max_ms: 15000
    baseline_tpot_p50_max_ms: 35
    baseline_cache_hit_rate_min: 0.6
    scenario_input_tokens_min: 4000
    scenario_input_tokens_max: 80000
  concurrency:
    enabled: true
    levels: [1, 2, 4, 8, 16, 32, 64, 128]
    requests_per_level: 8
    stable_success_rate: 0.995
  burst: { enabled: false }
```

If the target is a reasoning model (e.g. K3) whose sampling params are **fixed** (only one accepted value), set the `features.param_cases` list to the model's accepted values as `expect: accept` and everything else as `expect: reject`. When in doubt, ask the user for the model's official parameter policy before running.

Completion criterion: `provider-bench validate benchmark.yaml` prints `Valid:` with no error.

### 3. Run and inspect
Run `provider-bench run benchmark.yaml` (or `python -m provider_bench run benchmark.yaml`). It prints `Report: outputs/<run-id>/report.html` at the end.

Completion criterion: the run prints a run id, and `outputs/<run-id>/report.html` exists.

### 4. Verify the report
Open `report.html` and confirm it contains, per provider:

- 总体结论 (scorecard + verdict + sub-item counts + TTFT/E2E p50)
- 评分子项 (weighted sub-items: http_ok, ping_ok, model_match, usage_present, cache_hit, stream_integrity, usage_stream, image_base64, video_base64, tool_choice, structured_output, reasoning_effort, thinking_switch)
- 功能用例 tables (协议 / 工具调用 / 结构化输出 / 思考参数 / 参数约束)，其中协议含 Usage 非流式 + Usage 流式，参数约束含 `param_omit_sampling` / `param_fixed_*` / `param_reject_*`
- 缓存命中率 section (rounds table + 每轮 Token 构成 bar chart)
- 返回模型汇总 (model consistency)
- 延迟 percentiles (TTFB/TTFT/TPOT/ITL/E2E) + 流式用例 TTFT 明细
- 性能压测 `BENCHMARK RESULTS` block + 基线对照（场景合规 / 参考基线）/ 分会话明细 / ECharts 图表（TTFT 直方图, 散点, RPS/RPM, 在途并发, 成败, token/s, 流式对比, 分轮次缓存）
- 并发探针 (阶梯并发级别表 + 最大稳定并发量) / 突发并发（按需）
- 失败详情 (reason + evidence + request/response)
- Provider comparison (when two providers configured)
- TLS / SSL 稳定性诊断 (when SSL errors occurred, e.g. `UNEXPECTED_EOF_WHILE_READING`)

Completion criterion: every section above is present, and each FAILED case shows a reason in 错误用例汇总 and a request/response block in 失败详情.

### 5. Read the score by dimension
The 总体结论 card reports three dimension scores, separating **第三方网关/转发层** from **模型本身**:

- **网关合规 (gateway)** — protocol compliance, parameter rejection, tool_choice semantics, multimodal format, model routing consistency, cache, usage/billing. Low scores here point at the *gateway deployment*, not the model.
- **模型能力 (model)** — quality, structured output, tool-calling correctness, thinking/reasoning. Low scores here point at the *model*.
- **性能 (performance)** — latency, throughput, concurrency, reliability, cost.

When reporting to the user, attribute gateway-dimension failures to the gateway (e.g. "网关不校验参数 / 网关不支持该多模态格式 / 网关不遵守 tool_choice=none") and model-dimension failures to the model — do not collapse them into one "model failed" verdict.

For reasoning models, latency/billing use `max_tokens: 512` (otherwise the model spends its whole budget thinking and returns empty `content`).

## Reference

Plugin → reference-report mapping:

| Plugin | Covers |
| --- | --- |
| `compatibility` | http_ok, usage 回传, tool_calling, json_output, multi-turn |
| `protocol` | ping, stream_integrity (SSE chunks + `[DONE]`), usage_stream (include_usage), image/video base64 |
| `features` | reasoning_effort low/high/max, thinking 开关 (enable_thinking / thinking.type / chat_template_kwargs), 参数约束 (fixed/reject) |
| `cache` | Prefix Cache 命中率：预热 1 轮 + 测量 N 轮，长文固定前缀 + 随机后缀 |
| `tool_calling` | tool_choice 全分支：default/auto/required/none/function/allowed_tools |
| `structured_output` | json_object + json_schema |
| `model_identity` | 返回模型一致性、指纹、行为漂移 |
| `billing` | usage 回传、token 偏差、成本 |
| `latency` | TTFT / TPOT / ITL / E2E 分位数 (p50/p75/p90/p95/p99) |
| `benchmark` | 会话/轮次压测：sessions × turns、到达率、TTFB、输入/输出 token 吞吐、分轮次缓存、RPS/TPM、稳态缓存（turn≥2）、基线对照（场景合规/参考基线）、分会话明细 |
| `concurrency` | 并发探针：阶梯并发（levels × requests_per_level）+ 最大稳定并发量（按 success_rate/TTFT p95 阈值） |
| `burst` | 突发并发：同时发起不同规模批次（可选，按需开启） |

> **多模态素材**：`image_base64`/`video_base64` 内置极小 base64 占位素材（1px PNG / GIF），开箱即用。若网关对视频格式有要求（真实视频编解码），需在 `src/provider_bench/assets.py` 中替换 `video_content` 的素材与 content-type。
>
> **`tool_choice=allowed_tools`**：实现按 OpenAI 新版语义发送 `tool_choice=["get_weather"]`（允许的工具数组）；若网关期望 `"allowed_tools"` 字符串等其它格式，该分支会 FAIL，此时按网关文档调整 `tool_calling.branches` 或 `_run_branch` 的取值。

`provider-bench init` writes a commented starter config; `provider-bench report <run-id>` regenerates reports without re-running.
