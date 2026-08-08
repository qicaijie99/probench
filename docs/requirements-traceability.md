# Requirements Traceability

本文将 `API_Provider_Benchmark_Platform_Requirements.md` 的交付项映射到当前实现和自动化证据。

| 需求 | 实现证据 | 验证证据 |
| --- | --- | --- |
| 任意 OpenAI-compatible Provider | `providers/base.py`、`providers/openai.py`；URL、Key、Model、Header、连接池均配置驱动 | `test_provider.py`、`test_protocol_integration.py` |
| Key 不写日志/报告 | `SecretStr` 配置、Header 全脱敏、HTTP 错误内容二次脱敏 | `test_config.py`、`test_provider.py`、`test_engine.py` |
| Plugin 生命周期与自动注册 | `plugins/base.py`、包扫描及 `provider_bench.plugins` entry point | Web registry 测试；CLI validate 冒烟 |
| 插件独立配置/结果/失败/重跑 | 每个插件独立 Settings/Runner/Metrics 目录；Engine 捕获单插件异常；Web rerun API | `test_plugin_failure_does_not_stop_later_plugins`、`test_rerun_endpoint_can_select_one_plugin_from_live_session` |
| API Compatibility 九项 | `plugins/compatibility.py` | `test_engine_to_openai_protocol_to_report` |
| Latency 与分位数 | `plugins/latency.py`、`plugins/stats.py` | Engine 与协议集成测试 |
| Concurrency 阶梯与稳定并发 | `plugins/concurrency.py`；默认 1..128；Provider 默认 256 连接 | `test_concurrency_and_burst_execute_configured_parallel_batches` |
| Burst 10/25/50/100 | `plugins/burst.py` | `test_concurrency_and_burst_execute_configured_parallel_batches` |
| Quality 六类数据 | `datasets/core.yaml`、自定义 YAML loader/filter | `test_quality_tool_structured_identity_and_billing_plugins` |
| 五种 Evaluator | `evaluators/registry.py`；Exact/Numeric/JSON Schema/受限 Code/LLM Judge | `test_evaluators.py`、剩余插件集成测试 |
| Tool Calling | `plugins/tool_calling.py`；selection/arguments/JSON/schema | 剩余插件集成测试 |
| Structured Output | `plugins/structured_output.py`；object/schema/nested | 剩余插件集成测试 |
| Model Identity | `plugins/model_identity.py`；reported model/fingerprint/probe hash/consistency/baseline | 多 Provider comparison 测试 |
| Billing 与成本 | `plugins/billing.py`；usage、tiktoken、偏差、Provider 定价 | 剩余插件集成测试 |
| 全模块开关、only、skip | Pydantic/YAML 配置与 Engine selection | `test_only_and_skip_selection` |
| 原始请求数据结构及目录 | `RequestRecord`、`PluginContext.record`、Storage | Engine artifact tests、Web raw request test |
| CLI 五命令 | `cli.py` | CLI help/validate 冒烟与报告单元测试 |
| FastAPI + SSE | `web/app.py` | health/registry/raw/rerun/SSE tests |
| React + TS + Vite + ECharts | `web/src`，生产资源编译进 Python 包 | `npm run build`、wheel 内容验收 |
| Dashboard | React 历史表显示 Provider/Model/Score/Verdict | TypeScript build；API state tests |
| New Benchmark | 多 Provider 表单、连接测试、Registry 动态模块、JSON 参数与 Gate 编辑 | TypeScript build；连接 API test |
| 实时页面 | 总进度、插件五态、当前 Metrics、E2E ECharts | SSE tests；TypeScript build |
| Result 页面 | 基准/候选分数图、逐指标对比、身份差异、错误与原始请求 | 多 Provider report test、raw API test、TypeScript build |
| Scorecard 与 Hard Gate | `scoring.py`；权重、SLA 目标、Gate 全配置化 | `test_hard_gate_overrides_weighted_score` |
| HTML/Markdown/JSON 报告 | `report.py`；Provider 对比、图形化分数、错误分析 | `test_report.py`、多 Provider report test |
| CLI/Web 共用 Engine | 两入口均只调用 `BenchmarkEngine.run` | Engine、Web 与协议集成测试 |
| 无无限重试、非平均值统计 | HTTP 层不配置 retry；P50/P90/P95/P99 原始分布 | Provider/Latency 源码与测试 |

最终验收命令：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -p pytest_asyncio.plugin -q
ruff check src tests
mypy src/provider_bench
npm --prefix web run build
npm --prefix web audit --omit=dev
python -m pip wheel --no-deps . --wheel-dir /tmp/provider-bench-wheel
```
