# Provider Bench

Provider Bench 是一个面向任意 OpenAI-compatible API 的异步 Benchmark 平台。CLI 和 Web 控制台共用同一个执行引擎，可对单个或多个 Provider 进行兼容性、延迟、并发、突发流量、质量、工具调用、结构化输出、模型身份和计费准确性测试，并生成 JSON、Markdown 与 HTML 报告。

> Benchmark 会向配置的 Provider 发起真实请求并可能产生费用。首次接入请按本文的“低成本分阶段运行”顺序执行；确认限流和计费策略前，不要直接启用高并发或突发测试。

## 文档导航

- [快速开始](#快速开始)
- [完整验收流程（等价 Skill）](#完整验收流程等价-skill)
- [Provider 配置](#provider-配置)
- [Benchmark 插件](#benchmark-插件)
- [CLI 使用指南](#cli-使用指南)
- [Quality 数据集与 Judge](#quality-数据集与-judge)
- [评分、Hard Gate 与验收结论](#评分hard-gate-与验收结论)
- [Web 控制台](#web-控制台)
- [输出目录与结果解读](#输出目录与结果解读)
- [常见问题](#常见问题)

## 功能概览

- 单 Provider 测试或多 Provider 横向对比，支持指定基线 Provider
- OpenAI-compatible `/models` 与 `/chat/completions` 接口
- Compatibility、Protocol、Features、Cache、Latency、Concurrency、Burst、Benchmark、Quality、Tool Calling、Structured Output、Model Identity、Billing 十三个插件
- TTFT、TTFB、TPOT、ITL、E2E、Output TPS 及 P50/P75/P90/P95/P99
- Success Rate、429、5xx、Timeout、SSL 错误、最大稳定并发和突发批次统计
- 内置数学、推理、中文知识、代码、指令遵循和 JSON 数据集
- Exact Match、Numeric、JSON Schema、Code Test、LLM Judge 五类评估器
- `tiktoken` 本地 Token 计数、Provider usage 偏差与成本估算
- 网关合规 / 模型能力 / 性能 三维度评分，Hard Gate 和 PASS/WARN/FAIL 验收结论
- 阶梯/突发并发探针（含最大稳定并发量）、前缀缓存命中率、会话/轮次压测
- 原始请求 JSONL、插件指标、运行状态、跨 Provider 对比和错误分析
- Typer CLI、FastAPI API、SSE 实时进度、React + ECharts Web 控制台
- API Key、自定义 Header 和持久化配置全程脱敏
- 内置模型评测 Skill（`skills/model-benchmark`），可由 Claude Code / Codex 驱动全流程

## 环境要求

运行 CLI 和已构建的 Web 控制台只需要：

- Python 3.11 或更高版本
- Linux、macOS 或 Windows
- 能访问被测 Provider 的网络

只有修改或重新构建前端时才需要 Node.js 20.19+ 和 npm。项目已经包含构建后的 Web 静态资源，普通使用者不必安装 Node.js。

建议使用独立虚拟环境。Python 包和 `provider-bench` 命令只安装到执行 `pip install` 时所在的环境；切换 Conda/venv 环境后不会自动继承。

## 快速开始

### 1. 创建并激活 Python 环境

使用 Conda：

```bash
cd ~/api-benchmark-tool
conda create -n provider-bench python=3.11 -y
conda activate provider-bench
python --version
```

或使用 venv：

```bash
cd ~/api-benchmark-tool
python3.11 -m venv .venv
source .venv/bin/activate
python --version
```

Windows PowerShell 激活 venv：

```powershell
.venv\Scripts\Activate.ps1
```

### 2. 安装项目

只运行平台：

```bash
python -m pip install -e .
provider-bench --help
```

需要运行项目测试时安装开发依赖：

```bash
python -m pip install -e '.[dev]'
```

如果安装后当前 Shell 仍找不到命令，可以刷新命令缓存，或直接通过 Python 模块启动：

```bash
hash -r
provider-bench --help
python -m provider_bench.cli --help
```

### 3. 生成配置

```bash
provider-bench init benchmark.yaml
```

`init` 不会覆盖已经存在的文件。需要重新生成时，请先换一个文件名：

```bash
provider-bench init benchmark.new.yaml
```

仓库中的 [`benchmark.example.yaml`](benchmark.example.yaml) 是更完整的双 Provider 示例。

### 4. 配置第一个 Provider

编辑 `benchmark.yaml`：

```yaml
provider:
  name: candidate
  base_url: https://api.example.com/v1
  api_key: ${PROVIDER_API_KEY}
  model: your-model-name

benchmarks:
  compatibility:
    enabled: true
    max_tokens: 64
  latency:
    enabled: true
    warmup: 1
    repetitions: 5
    prompt: "用两句话解释什么是 API。"
    max_tokens: 512  # reasoning 模型需足够输出预算

output_dir: outputs
```

`base_url` 应指向 OpenAI-compatible API 根路径，平台会在它后面追加 `models` 和 `chat/completions`。

### 5. 设置 API Key

Linux/macOS：

```bash
export PROVIDER_API_KEY='替换为真实 API Key'
```

Windows PowerShell：

```powershell
$env:PROVIDER_API_KEY = '替换为真实 API Key'
```

不要把真实 API Key 直接提交到 YAML 或版本控制。配置中的 `${VAR}` 会从当前进程环境展开；缺少变量时，校验会明确报错。

### 6. 校验配置

```bash
provider-bench validate benchmark.yaml
```

`validate` 会检查环境变量、Provider 字段、插件参数、数据集和评分配置，但不会发起 API 请求，也不会产生模型费用。成功输出类似：

```text
Valid: 1 provider(s), 2 enabled plugin(s)
```

### 7. 低成本分阶段运行

先只确认接口兼容性：

```bash
provider-bench run benchmark.yaml --only compatibility
```

再采集少量延迟样本：

```bash
provider-bench run benchmark.yaml --only latency
```

确认以上两步没有认证、模型名、超时或协议问题后，再运行配置中所有 `enabled: true` 的插件：

```bash
provider-bench run benchmark.yaml
```

`--only` 只筛选插件，不会把 `enabled: false` 改成 `true`。如果要单独测试 Quality、Tool Calling 等插件，需要先在 YAML 中启用它。

局部试跑仍会计算配置中的 Scorecard 和 Hard Gate。如果某个 Gate 引用了本次未运行的插件，其实际值为空并会判定该 Gate 未通过。因此局部试跑主要看插件状态和指标，最终 PASS/WARN/FAIL 应以启用了验收所需全部插件的完整运行结果为准。

### 8. 查看结果

每次运行结束会打印 HTML 报告路径和运行状态：

```text
Report: outputs/20260808T120000Z-ab12cd34/report.html
Result: COMPLETED
```

可以直接用浏览器打开 `report.html`，也可以启动 Web 控制台：

```bash
provider-bench web
```

然后访问 <http://127.0.0.1:8000>。

## 完整验收流程（等价 Skill）

以下手动流程与内置 Skill [`skills/model-benchmark/SKILL.md`](skills/model-benchmark/SKILL.md) 完全等价：当使用 Claude Code / Codex 等 agent 时，直接让它加载该 Skill 即可代劳全流程；手动操作时按本节的步骤执行。

### 1. 收集目标 API 信息

需要三要素，其余可后补：

- `base_url`：OpenAI-compatible 根路径，通常以 `/v1` 结尾
- `api_key`：放环境变量，不要写进 YAML 或版本控制
- `model`：被测模型名

双 Provider 对比时，另需第二个 Provider 的同款信息。

### 2. 写全量配置

用 `provider-bench init benchmark.yaml` 生成后，改写成完整套件（覆盖功能测试 + 性能压测 + 对比）：

```yaml
provider:
  name: candidate
  base_url: ${API_BASE_URL}
  api_key: ${API_KEY}
  model: ${API_MODEL}

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

output_dir: outputs
```

> 对 reasoning 模型（如 K3），`latency` / `protocol` / `billing` 的 `max_tokens` 要给足（示例统一为 512），否则思考 token 会挤占输出预算，导致 `content` 为空、TTFT/多模态/工具参数被误判失败。非 reasoning 模型可用更小值。

### 3. 校验并运行

```bash
export API_BASE_URL='https://...'
export API_KEY='...'
export API_MODEL='...'
provider-bench validate benchmark.yaml   # 应打印 Valid: 1 provider(s), 12 enabled plugin(s)
provider-bench run benchmark.yaml        # 结束打印 Report: outputs/<run-id>/report.html
```

### 4. 验证报告

用浏览器打开 `report.html`，确认每个 Provider 都包含：总体结论、评分子项、功能用例表、缓存命中率（含每轮 Token 构成柱状图）、返回模型汇总、延迟分位数（TTFB/TTFT/TPOT/ITL/E2E）+ 流式用例明细、`BENCHMARK RESULTS`、基线对照、分会话明细、8 张 ECharts 图表、并发探针、失败详情、Provider 对比（双 Provider 时）。

### 5. 按维度解读

总体结论卡给出三维度分数，用于区分「第三方网关/转发层」和「模型本身」：

- **网关合规 (gateway)**：协议兼容、参数拒绝、`tool_choice` 语义、多模态格式、模型路由一致性、缓存、usage 计费。分数低指向网关部署（如「网关不校验参数」「网关不遵守 `tool_choice=none`」「跨请求前缀缓存不稳定」），不应算作模型能力失败。
- **模型能力 (model)**：质量、结构化输出、工具调用正确性、思考/推理。分数低指向模型。
- **性能 (performance)**：延迟、吞吐、并发、可靠性、成本。

报告失败详情里每个 FAIL 用例附原因 + 请求/响应证据，便于回溯归因。

## Provider 配置

### 单 Provider

使用顶层 `provider`：

```yaml
provider:
  name: candidate
  base_url: https://candidate.example.com/v1
  api_key: ${CANDIDATE_API_KEY}
  model: model-name
```

### 多 Provider 对比

使用顶层 `providers` 数组，并通过 `reference_provider` 指定对比基线：

```yaml
providers:
  - name: official
    base_url: https://official.example.com/v1
    api_key: ${OFFICIAL_API_KEY}
    model: model-name
  - name: candidate
    base_url: https://candidate.example.com/v1
    api_key: ${CANDIDATE_API_KEY}
    model: model-name

reference_provider: official
```

每个 Provider 会执行相同的已启用插件。未设置 `reference_provider` 时，第一个 Provider 自动成为基线。Provider 名称必须唯一。

导出凭据后再校验：

```bash
export OFFICIAL_API_KEY='...'
export CANDIDATE_API_KEY='...'
provider-bench validate benchmark.yaml
```

### Provider 完整字段

| 字段 | 是否必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `name` | 是 | — | Provider 唯一名称，也是报告和价格配置中的标识 |
| `base_url` | 是 | — | OpenAI-compatible API 根路径，通常以 `/v1` 结尾 |
| `api_key` | 是 | — | 推荐写成环境变量引用 |
| `model` | 是 | — | 请求体中的模型名称 |
| `timeout_seconds` | 否 | `120` | 单次 HTTP 请求超时 |
| `stream_include_usage` | 否 | `false` | 流式请求是否发送 `stream_options.include_usage=true` |
| `max_connections` | 否 | `256` | HTTP 连接池最大连接数 |
| `max_keepalive_connections` | 否 | `128` | Keep-alive 最大连接数，不可大于 `max_connections` |
| `headers` | 否 | `{}` | 额外认证或路由 Header；所有值均按敏感信息脱敏 |

自定义 Header 示例：

```yaml
provider:
  name: gateway
  base_url: https://gateway.example.com/v1
  api_key: ${GATEWAY_API_KEY}
  model: model-name
  timeout_seconds: 180
  headers:
    X-Tenant-ID: ${TENANT_ID}
    X-Gateway-Token: ${GATEWAY_TOKEN}
```

环境变量也支持 `${VAR:-default}` 语法，但不要给密钥设置明文默认值。

## Benchmark 插件

| 插件 | 主要用途 | 关键配置 | 调用量/风险 |
| --- | --- | --- | --- |
| `compatibility` | 检查 models、流式/非流式、多轮、System、usage、finish reason、工具调用和 JSON 输出 | `checks`、`max_tokens` | 低，建议第一个运行 |
| `protocol` | 探活、Stream 完整性（SSE chunks + `[DONE]`）、流式 usage、多模态 image/video base64 | `checks`、`max_tokens` | 低 |
| `features` | 思考开关、reasoning_effort 三档、采样参数约束（固定值/应拒绝值） | `reasoning_effort_levels`、`thinking_variants`、`param_cases` | 中 |
| `cache` | Prefix Cache 命中率：预热 + 测量轮，长文固定前缀 + 随机后缀 | `prefix_chars`、`rounds`、`warmup` | 中 |
| `latency` | 统计 TTFT、TPOT、ITL、E2E 和输出 TPS 分布 | `warmup`、`repetitions`、`prompt`、`max_tokens` | 低到中 |
| `concurrency` | 阶梯并发和最大稳定并发 | `levels`、`requests_per_level`、稳定性阈值 | 高，可能触发 429 |
| `burst` | 同时发起不同规模的突发批次 | `sizes`、`max_tokens` | 高，可能触发限流和费用 |
| `benchmark` | 会话/轮次 + 到达率压测：TTFB、输入/输出 token 吞吐、分轮次缓存、稳态缓存（turn≥2）、RPS/TPM、基线对照（场景合规/参考基线）、分会话明细 | `sessions`、`turns`、`init_tokens`、`arrival_*`、`ramp_seconds`、`baseline_*` | 高，可能触发限流和费用 |
| `quality` | 使用内置或自定义数据集评估模型质量 | `datasets`、`categories`、`evaluators`、`max_cases`、`concurrency` | 取决于用例数；LLM Judge 会增加请求 |
| `tool_calling` | 检查工具选择、参数 JSON、Schema 合规，以及 tool_choice 全分支 | `cases`、`branches`、`concurrency`、`max_tokens` | 中 |
| `structured_output` | 检查 JSON Object、JSON Schema 和嵌套结构 | `cases`、`strict`、`concurrency` | 中 |
| `model_identity` | 检查响应模型名、指纹、确定性探针和行为漂移 | `probes`、`repetitions`、期望模型/指纹 | 中；结论是启发式证据 |
| `billing` | 比较 Provider usage 与本地 Token 计数并估算成本 | Tokenizer、允许偏差、Provider 价格 | 低 |

高负载插件默认关闭。以默认配置为例：

- Concurrency 在每个 level 发起 `max(level, requests_per_level)` 个请求。
- Burst 的请求总数是所有 `sizes` 之和。
- 多 Provider 运行会为每个 Provider 重复全部请求。

> 对 reasoning 模型（如 K3），涉及模型「可见输出」的插件（`latency`、`protocol`、`billing`、`tool_calling`、`model_identity`）需要足够的 `max_tokens`（建议 512），否则思考 token 会耗尽输出预算，导致 `content` 为空、流式 TTFT 记为 None、工具参数被截断、身份探针一致性下降等**误判**。

首次压测建议从小规模开始：

```yaml
benchmarks:
  concurrency:
    enabled: true
    levels: [1, 2, 4, 8]
    requests_per_level: 4
    max_tokens: 32
  burst:
    enabled: true
    sizes: [5, 10]
    max_tokens: 8
```

## CLI 使用指南

### 命令总览

| 命令 | 作用 | 是否发送 API 请求 |
| --- | --- | --- |
| `provider-bench init [PATH]` | 生成入门配置，默认 `benchmark.yaml` | 否 |
| `provider-bench validate CONFIG` | 校验配置与插件参数 | 否 |
| `provider-bench run CONFIG` | 执行 Benchmark 并生成报告 | 是 |
| `provider-bench report RUN_ID` | 从已有 `run.json` 重新生成报告 | 否 |
| `provider-bench compare RUN_A RUN_B` | 比较两次历史运行 | 否 |
| `provider-bench web` | 启动 API 与 Web 控制台 | 仅在用户发起连接测试或运行时请求 Provider |

查看任意命令的完整参数：

```bash
provider-bench --help
provider-bench run --help
provider-bench web --help
```

### 选择或跳过插件

`--only` 和 `--skip` 可以重复：

```bash
provider-bench run benchmark.yaml --only tool_calling --only structured_output
provider-bench run benchmark.yaml --skip concurrency --skip burst
```

插件必须出现在配置的 `benchmarks` 中；`--only` 指定不存在的插件会报错。

### 重新生成报告

可以使用 Run ID，也可以直接提供运行目录：

```bash
provider-bench report 20260808T120000Z-ab12cd34
provider-bench report /absolute/path/to/outputs/20260808T120000Z-ab12cd34
provider-bench report 20260808T120000Z-ab12cd34 --output-dir custom-outputs
```

### 比较两次运行

```bash
provider-bench compare <run-a> <run-b>
provider-bench compare <run-a> <run-b> --output-dir outputs
```

比较结果以 JSON 输出到终端，包括 Provider 分数、结论和指标差异。

### 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 命令执行成功；对于 `run`，表示所有被执行插件完成，不等同于评分一定 PASS |
| `1` | 配置、输入文件或启动阶段错误 |
| `2` | 至少一个插件运行异常并标记为 FAILED |

单个插件失败不会中断其他插件。平台会继续执行并保留已经获得的结果。

## Quality 数据集与 Judge

内置 `builtin:core` 数据集覆盖：

- `math`
- `reasoning`
- `chinese_knowledge`
- `code`
- `instruction_following`
- `json`

启用内置数据集：

```yaml
benchmarks:
  quality:
    enabled: true
    datasets: [builtin:core]
    categories: [math, code, json]
    evaluators: [numeric, code_test, json_validator]
    max_cases: 20
    concurrency: 4
```

`categories`、`evaluators` 为空时不做对应筛选。`max_cases` 可用于低成本试跑。

### 自定义数据集

数据集是 YAML 列表。相对路径以 `benchmark.yaml` 所在目录为基准：

```yaml
# datasets/private.yaml
- id: private-math-01
  category: math
  prompt: "计算 21 * 2，只输出数字。"
  evaluator: numeric
  expected: 42
  tolerance: 0
  max_tokens: 16

- id: private-json-01
  category: json
  prompt: '只输出 JSON：{"ok": true}'
  evaluator: json_validator
  expected:
    ok: true
  json_schema:
    type: object
    properties:
      ok: {type: boolean}
    required: [ok]
    additionalProperties: false
  max_tokens: 32
```

在主配置中引用：

```yaml
benchmarks:
  quality:
    enabled: true
    datasets: [builtin:core, ./datasets/private.yaml]
    concurrency: 4
```

所有数据集中的 `id` 必须唯一。

### 评估器

| Evaluator | 必要字段 | 说明 |
| --- | --- | --- |
| `exact_match` | `expected` | 规范化后精确匹配 |
| `numeric` | `expected`，可选 `tolerance` | 数值与容差匹配 |
| `json_validator` | 可选 `expected`、`json_schema` | 校验 JSON、期望内容和 Schema |
| `code_test` | `code_tests` | 在隔离 Python 子进程中执行断言 |
| `llm_judge` | `rubric`，可选 `pass_threshold` | 使用模型根据 Rubric 评分 |

需要独立 Judge 时添加：

```yaml
judge_provider:
  name: judge
  base_url: https://judge.example.com/v1
  api_key: ${JUDGE_API_KEY}
  model: judge-model
```

没有 `judge_provider` 时，LLM Judge 使用当前被测 Provider，因此每个被测 Provider 可能得到不同的 Judge 行为。正式横向验收建议配置固定的独立 Judge。

Code Test 会拒绝 import、文件访问、动态执行和私有属性，并限制执行时间、内存与文件大小，但它不是面向敌意代码的系统级沙箱。只应运行可信数据集中的测试代码。

## Billing 与价格

为多 Provider 分别配置每百万 Token 的美元价格：

```yaml
benchmarks:
  billing:
    enabled: true
    tokenizer_encoding: cl100k_base
    allowed_deviation: 0.05
    target_cost_per_request_usd: 0.01
    provider_prices:
      official:
        input_per_million: 2.50
        output_per_million: 10.00
      candidate:
        input_per_million: 1.00
        output_per_million: 4.00
```

`provider_prices` 的键必须与 Provider 的 `name` 一致。若所有 Provider 共用价格，也可以使用 `input_price_per_million` 和 `output_price_per_million`。

Token 估算依赖所选模型或 `tokenizer_encoding`。不同 Provider 的服务端分词、隐藏系统提示或缓存计费策略可能导致偏差，因此 Billing 结果用于发现异常，不应替代账单系统。

## 评分、Hard Gate 与验收结论

平台只对本次有可用指标的评分组件应用权重，并在这些组件之间自动归一化。组件被划分为三个维度：**网关合规 (gateway)**（compatibility、protocol、features_param、tool_choice、cache、model_identity、billing）、**模型能力 (model)**（quality、structured_output、tool_calling、features_thinking）和**性能 (performance)**（latency、throughput、concurrency、reliability、cost）。总体结论卡会同时展示三维度分，便于区分网关层与模型层问题。

```yaml
scoring:
  weights:
    quality: 30
    latency: 15
    throughput: 10
    concurrency: 15
    reliability: 10
    compatibility: 4
    protocol: 4
    structured_output: 4
    tool_calling: 3
    tool_choice: 2
    features_thinking: 3
    features_param: 2
    cache: 5
    model_identity: 3
    billing: 4
    cost: 3
  latency_ttft_good_ms: 1000
  latency_ttft_fail_ms: 10000
  output_tps_target: 20
  warn_score_below: 80
  fail_score_below: 60
  gates:
    - metric: compatibility.success_rate
      operator: ">="
      value: 0.95
      severity: fail
    - metric: latency.success_rate
      operator: ">="
      value: 0.995
      severity: fail
    - metric: latency.ttft_ms.p95
      operator: "<"
      value: 3000
      severity: warn
```

Gate 的 `metric` 使用 `插件名.指标路径`，运算符支持 `>=`、`<=`、`>`、`<`、`==`。

- 任一 `severity: fail` Gate 未通过，最终结论为 FAIL。
- 没有 Fail Gate 失败，但 Warn Gate 未通过，最终结论为 WARN。
- 分数低于 `fail_score_below` 为 FAIL。
- 分数低于 `warn_score_below` 为 WARN。
- 其余情况为 PASS。

Gate 引用的插件应在最终验收配置中启用；指标缺失会被视为 Gate 未通过。

### 状态与结论不是同一概念

- `COMPLETED`：执行引擎和插件正常完成。
- `FAILED`：至少一个插件发生运行异常。
- `PASS/WARN/FAIL`：根据指标、权重和 Hard Gate 得出的业务验收结论。

因此 `Result: COMPLETED` 仍可能对应 Scorecard 的 WARN 或 FAIL。

## Web 控制台

### 使用已构建前端

```bash
provider-bench web
```

默认监听 `127.0.0.1:8000`。指定地址和端口：

```bash
provider-bench web --host 0.0.0.0 --port 8000
```

Web 控制台支持：

- 单/多 Provider 配置和连接测试
- 从插件注册表动态加载插件与参数
- 实时 SSE 进度和延迟图表
- Provider 分数、指标和模型身份对比
- 历史运行、原始请求和错误查看
- 选定插件重跑

Web 进程只在内存中保留凭据以支持当前会话重跑，不会把真实凭据写入运行目录。服务重启后重跑历史任务，需要重新提交配置和凭据。

Web 服务本身没有用户认证。使用 `--host 0.0.0.0` 时，只应在可信网络中运行，或在前面部署带认证和 TLS 的反向代理。

### 前端开发模式

终端 1：

```bash
provider-bench web --reload
```

终端 2：

```bash
npm --prefix web install
npm --prefix web run dev
```

访问 Vite 输出的地址，通常是 <http://127.0.0.1:5173>。`/api` 会代理到 `127.0.0.1:8000`。

修改前端后生成生产静态资源：

```bash
npm --prefix web run build
provider-bench web
```

## HTTP API

FastAPI 交互文档位于 <http://127.0.0.1:8000/docs>。

| API | 作用 |
| --- | --- |
| `GET /api/health` | 健康检查 |
| `GET /api/plugins` | 插件列表和配置 JSON Schema |
| `POST /api/providers/test` | 测试 models 与 chat 连接，不持久化凭据 |
| `GET /api/runs` | 查询历史和运行中任务 |
| `POST /api/runs` | 使用与 YAML 等价的 JSON 配置启动任务 |
| `GET /api/runs/{run_id}` | 获取运行状态或完整结果 |
| `POST /api/runs/{run_id}/rerun` | 重跑选定插件 |
| `GET /api/runs/{run_id}/events` | SSE 实时事件 |
| `GET /api/runs/{run_id}/comparison` | 获取同次运行的 Provider 对比 |
| `GET /api/runs/{run_id}/requests` | 分页、筛选原始请求 |
| `GET /api/runs/{run_id}/report` | 获取 HTML 报告 |

## 输出目录与结果解读

### 单 Provider

```text
outputs/<run_id>/
├── benchmarks/
│   └── <plugin>/
│       ├── requests.jsonl
│       └── metrics.json
├── config.json
├── state.json
├── run.json
├── scores.json
├── report.md
└── report.html
```

### 多 Provider

```text
outputs/<run_id>/
├── providers/
│   ├── official/benchmarks/<plugin>/
│   └── candidate/benchmarks/<plugin>/
├── config.json
├── state.json
├── run.json
├── scores.json
├── report.md
└── report.html
```

主要文件：

- `state.json`：轻量运行状态，适合轮询。
- `run.json`：完整 Provider、插件、Scorecard 和对比结果。
- `scores.json`：各 Provider 评分和 Gate 结果。
- `requests.jsonl`：逐请求时序、状态、Token、请求体和响应。
- `metrics.json`：单插件聚合指标。
- `report.md`、`report.html`：人工验收报告。

保存的请求不含认证 Header；API Key 和自定义 Header 值会在配置、错误和报告中脱敏。Prompt 与模型响应会写入 `requests.jsonl`，因此不要在测试数据中放入不应落盘的敏感业务内容。

## 常见问题

### `provider-bench：未找到命令`

先确认当前环境：

```bash
python --version
command -v python
python -m pip show provider-bench
command -v provider-bench
```

如果 Python 低于 3.11，请创建新的环境；不要在旧环境中使用 `--ignore-requires-python` 强制安装。

```bash
conda create -n provider-bench python=3.11 -y
conda activate provider-bench
cd ~/api-benchmark-tool
python -m pip install -e .
```

如果包安装在另一个 Conda 环境，可切换环境，或临时运行：

```bash
conda run -n provider-bench provider-bench --help
```

### `environment variable '...' is not set`

`validate` 也会解析环境变量。请在运行命令的同一个终端导出变量：

```bash
export PROVIDER_API_KEY='...'
provider-bench validate benchmark.yaml
```

`sudo`、IDE、Systemd、Docker 和新的终端窗口可能不会继承当前 Shell 的变量。

### 返回 401 或 403

检查 API Key、额外 Header、租户/项目 ID 和模型访问权限。错误响应会写入请求记录并自动对已配置密钥脱敏。

### 返回 404

确认 `base_url` 是 API 根路径。例如平台会请求：

```text
<base_url>/models
<base_url>/chat/completions
```

不要把 `base_url` 配置成完整的 `/chat/completions` 地址。

### Provider 不支持 `/models`

可以从 Compatibility 的 `checks` 中移除 `models`：

```yaml
benchmarks:
  compatibility:
    enabled: true
    checks:
      - non_streaming
      - streaming
      - system_message
      - multi_turn
      - usage
      - finish_reason
```

### 出现 429、Timeout 或连接池等待

- 先减少 `concurrency.levels`、`requests_per_level` 或 `burst.sizes`。
- 根据 Provider SLA 调整 `timeout_seconds`。
- 确保 `max_connections` 不低于预期并发。
- 检查 Provider 的 RPM、TPM 和同时请求数限制。
- 不要通过自动重试掩盖限流；平台默认不重试，以保留真实稳定性结果。

### Streaming 没有 usage

如果 Provider 支持 OpenAI 的流式 usage 选项：

```yaml
provider:
  # 其他字段省略
  stream_include_usage: true
```

若 Provider 不支持该选项，请保持 `false`。平台仍可使用本地 Tokenizer 估算部分指标，但 Billing 准确性检查需要服务端 usage。

### 局部试跑为什么是 WARN/FAIL

Scorecard 和 Gate 会在每次运行后计算。若 Gate 引用了 `--only` 未执行的插件，指标缺失会导致 Gate 未通过。局部试跑请关注目标插件的状态和指标；最终结论使用完整验收配置。

## 安全与成本注意事项

- 所有 API 请求都可能计费，多 Provider 会按 Provider 数量放大调用量。
- Concurrency 和 Burst 会主动制造压力，只应对已获授权的服务运行。
- 不要把真实密钥写入 YAML、测试数据、命令行参数或版本控制。
- 输出会保存 Prompt 和模型响应；使用敏感数据集前先确认落盘策略。
- Web 控制台无内置登录功能，不应直接暴露到公网。
- Model Identity 的相似度与替换判断是排查信号，不是模型来源的密码学证明。
- 本地 Token 成本是估算值，最终费用以 Provider 账单为准。

## 新增插件

在 `src/provider_bench/plugins/` 新建模块并使用装饰器注册。Registry 会自动扫描内置插件，也支持 Python Entry Point 组 `provider_bench.plugins`：

```python
from provider_bench.plugins.base import BenchmarkPlugin, PluginSettings
from provider_bench.plugins.registry import register_plugin


class MySettings(PluginSettings):
    repetitions: int = 5


@register_plugin
class MyPlugin(BenchmarkPlugin):
    name = "my_benchmark"
    description = "My custom benchmark"
    settings_model = MySettings

    async def run(self):
        ...

    def aggregate(self, raw_result):
        return {"score": 1.0}
```

插件通过 `self.context.provider` 发请求，使用 `await self.context.record(record)` 保存原始观测。`prepare` 与 `cleanup` 可按需覆盖。新增插件后，CLI 和 Web 会共用同一注册结果。

## 开发与验证

运行 Python 测试：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin -q
```

禁用 pytest 自动加载是为了避免宿主机全局插件影响项目测试；项目自身的 asyncio 插件会显式加载。

构建前端：

```bash
npm --prefix web install
npm --prefix web run build
```

检查 CLI 和配置：

```bash
provider-bench --help
provider-bench validate benchmark.yaml
```

逐项需求、实现位置和测试证据见 [`docs/requirements-traceability.md`](docs/requirements-traceability.md)。
