# API Provider Benchmark 平台构建需求

## 项目目标

构建一个可长期复用的 LLM API Provider Benchmark
平台，用于评估第三方大模型 API 渠道是否适合接入模型平台。

首个应用场景： - 官方 K3 API vs Tokens 工厂 K3 API -
对比性能、质量、兼容性和准入指标

目标： - 测试周期控制在小时级 - 支持 CLI 和 Web 两种使用方式 -
每个测试模块独立解耦、可开关、可单独运行 - 自动生成 HTML / Markdown /
JSON 报告

# 核心架构要求

## 1. Provider 抽象

支持任意 OpenAI-compatible API：

配置：

``` yaml
provider:
  name:
  base_url:
  api_key:
  model:
```

禁止把 K3 或具体供应商写死。

## 2. Benchmark Plugin 化

所有测试必须插件化：

``` text
Benchmark Engine
        |
        +-- Compatibility
        +-- Latency
        +-- Concurrency
        +-- Burst
        +-- Quality
        +-- Tool Calling
        +-- Structured Output
        +-- Billing
```

每个 Plugin：

-   独立配置
-   独立 Runner
-   独立 Metric
-   独立结果
-   独立失败
-   独立重跑

接口：

``` python
class BenchmarkPlugin:

    name

    validate_config()

    prepare()

    run()

    aggregate()

    cleanup()
```

# 测试模块

第一版支持：

## API Compatibility

测试：

-   models API
-   streaming
-   non-streaming
-   system message
-   multi-turn
-   usage
-   finish_reason
-   tool calling
-   JSON output

## Latency

指标：

-   TTFT
-   TPOT
-   ITL
-   E2E latency
-   Output TPS

统计：

-   P50
-   P90
-   P95
-   P99

## Concurrency

阶梯：

    1
    2
    4
    8
    16
    32
    64
    128

统计：

-   Success Rate
-   429
-   5xx
-   Timeout
-   TTFT P95
-   TPS
-   最大稳定并发

## Burst

测试突发：

    10
    25
    50
    100

同时请求。

## Quality

支持：

-   数学
-   推理
-   中文知识
-   代码
-   指令遵循
-   JSON

Evaluator：

-   Exact Match
-   Numeric
-   JSON Validator
-   Code Test
-   LLM Judge

## Tool Calling

测试：

-   tool selection
-   arguments
-   JSON合法性
-   schema compliance

## Structured Output

测试：

-   JSON Object
-   JSON Schema
-   嵌套结构

## Model Identity

用于检测：

-   模型替换
-   降级模型
-   行为差异

## Billing

检查：

-   usage token
-   本地 tokenizer
-   计费偏差

# 配置系统

所有测试必须可开关：

``` yaml
benchmarks:

  latency:
    enabled: true

  concurrency:
    enabled: true

  quality:
    enabled: false

  billing:
    enabled: false
```

支持：

``` bash
provider-bench run config.yaml --only latency
```

以及：

``` bash
provider-bench run config.yaml --skip billing
```

# 数据结构

每次请求保存：

-   request_id
-   provider
-   case_id
-   start_time
-   first_token_time
-   end_time
-   TTFT
-   TPOT
-   TPS
-   tokens
-   status
-   error

目录：

    outputs/<run_id>/

    benchmarks/

      latency/
        requests.jsonl
        metrics.json

      quality/
        metrics.json

    report.html
    report.md
    scores.json

# CLI

支持：

``` bash
provider-bench init

provider-bench validate config.yaml

provider-bench run config.yaml

provider-bench report run_id

provider-bench compare run_a run_b
```

# Web 平台

提供：

``` bash
provider-bench web
```

启动 Web Console。

技术：

Backend:

-   FastAPI
-   WebSocket/SSE

Frontend:

-   React
-   TypeScript
-   Vite
-   ECharts

# Web 功能

## Dashboard

显示：

-   历史 Run
-   Provider
-   Model
-   Score
-   PASS/WARN/FAIL

## New Benchmark

支持：

-   输入 Provider
-   测试连接
-   选择测试模块
-   修改参数
-   开始测试

## 测试选择

动态读取 Plugin Registry：

    [x] Latency

    [x] Concurrency

    [x] Quality

    [ ] Billing

## 实时运行页面

展示：

-   总进度
-   每个 Plugin 状态
-   当前指标
-   实时图表

状态：

    PENDING
    RUNNING
    COMPLETED
    FAILED
    DISABLED

## Result 页面

展示：

-   Official vs Candidate
-   指标对比
-   图表
-   错误分析
-   原始请求查看

# 评分系统

Scorecard：

例如：

    Quality        30%
    Latency        15%
    Throughput     10%
    Concurrency    15%
    Reliability    10%
    Compatibility   8%
    Tool Calling    5%
    Billing         4%
    Cost            3%

同时支持 Hard Gate：

例如：

    quality >=95%

    success_rate >=99.5%

    TTFT P95 < SLA

    tool calling >=98%

输出：

    PASS
    WARN
    FAIL

# 工程要求

必须：

-   Python async
-   模块化
-   配置驱动
-   原始数据保存
-   CLI/Web 共用 Engine
-   Plugin 自动注册
-   前后端解耦

禁止：

-   写死 Provider
-   写死测试流程
-   CLI/Web 两套逻辑
-   只统计平均延迟
-   无限 retry 掩盖错误
-   API Key 写入日志

# 开发阶段

## Phase 1

基础：

-   Provider
-   Config
-   Plugin Interface
-   Registry

## Phase 2

性能：

-   Latency
-   Concurrency
-   Burst

## Phase 3

质量：

-   Dataset
-   Evaluator
-   Judge

## Phase 4

平台：

-   FastAPI
-   React Web
-   实时状态

## Phase 5

报告：

-   Score
-   Gate
-   Compare
-   HTML Report

# 最终目标

实现：

    API Provider Benchmark Platform

    Provider A
          |
          |
     Benchmark Engine
          |
          +-- Latency
          +-- Concurrency
          +-- Quality
          +-- Tool Calling
          +-- Compatibility
          |
          v

    HTML Report

    PASS / WARN / FAIL

新增测试时：

1.  创建 Plugin
2.  注册 Plugin
3.  定义配置
4.  定义 Metric

即可被 CLI 和 Web 自动识别。
