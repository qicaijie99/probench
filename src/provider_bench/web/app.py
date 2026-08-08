from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from provider_bench.engine import BenchmarkEngine, new_run_id
from pydantic import BaseModel, Field

from provider_bench.models import AppConfig, ProviderConfig, RunStatus
from provider_bench.plugins.registry import registry
from provider_bench.providers.openai import OpenAICompatibleProvider
from provider_bench.storage import read_json, safe_name


class RerunRequest(BaseModel):
    plugins: list[str] = Field(default_factory=list)
    config: AppConfig | None = None


class RunManager:
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.states: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self.configs: dict[str, AppConfig] = {}

    async def emit(self, event: dict[str, Any]) -> None:
        run_id = str(event["run_id"])
        normalized = json.loads(json.dumps(event, default=str))
        self.events[run_id].append(normalized)
        state = {**self.states.get(run_id, {}), "last_event": normalized["type"]}
        if normalized["type"].startswith("run."):
            state.update(normalized)
        if normalized.get("plugin") and normalized.get("provider"):
            plugin_states = dict(state.get("plugins") or {})
            plugin_states[f"{normalized['provider']}/{normalized['plugin']}"] = normalized.get(
                "status"
            )
            state["plugins"] = plugin_states
        if "progress" in normalized:
            state["progress"] = normalized["progress"]
        self.states[run_id] = state
        for queue in tuple(self.subscribers[run_id]):
            await queue.put(normalized)

    def start(
        self,
        config: AppConfig,
        *,
        only: set[str] | None = None,
        parent_run_id: str | None = None,
    ) -> str:
        run_id = new_run_id()
        self.configs[run_id] = config
        self.states[run_id] = {
            "run_id": run_id,
            "status": RunStatus.PENDING,
            "started_at": datetime.now(UTC).isoformat(),
            "parent_run_id": parent_run_id,
            "providers": {
                provider.name: {"model": provider.model, "score": None, "verdict": None}
                for provider in config.selected_providers
            },
        }

        async def execute() -> None:
            try:
                await BenchmarkEngine().run(
                    config, run_id=run_id, only=only, event_handler=self.emit
                )
                state_file = config.output_dir / run_id / "state.json"
                if state_file.is_file():
                    self.states[run_id] = read_json(state_file)
            except Exception as exc:
                await self.emit(
                    {
                        "type": "run.failed",
                        "run_id": run_id,
                        "status": RunStatus.FAILED,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

        self.tasks[run_id] = asyncio.create_task(execute())
        return run_id

    async def stream(self, run_id: str):
        if run_id not in self.states:
            raise KeyError(run_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.subscribers[run_id].add(queue)
        try:
            for event in self.events[run_id]:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            status = self.states[run_id].get("status")
            while status not in {RunStatus.COMPLETED, RunStatus.FAILED, "COMPLETED", "FAILED"}:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    status = event.get("status", status)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            self.subscribers[run_id].discard(queue)


manager = RunManager()
app = FastAPI(title="Provider Bench API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/plugins")
async def plugins() -> list[dict[str, Any]]:
    return registry.describe()


@app.post("/api/providers/test")
async def test_provider(config: ProviderConfig) -> dict[str, Any]:
    probe_config = config.model_copy(
        update={"timeout_seconds": min(config.timeout_seconds, 30.0)}
    )
    provider = OpenAICompatibleProvider(probe_config)
    try:
        models = await provider.list_models("connection.models")
        chat = await provider.chat(
            case_id="connection.chat",
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            stream=False,
            max_tokens=8,
            temperature=0,
        )
    finally:
        await provider.close()
    model_ids = [item.get("id") for item in models.response.get("data", [])]
    return {
        "connected": chat.status == "success",
        "model_available": config.model in model_ids if models.status == "success" else None,
        "models_status": models.status,
        "chat_status": chat.status,
        "latency_ms": chat.e2e_ms,
        "error": chat.error or models.error,
    }


@app.get("/api/runs")
async def runs(output_dir: str = "outputs") -> list[dict[str, Any]]:
    root = Path(output_dir)
    found: dict[str, dict[str, Any]] = {}
    if root.is_dir():
        for state_file in root.glob("*/state.json"):
            try:
                state = read_json(state_file)
                found[state["run_id"]] = state
            except (OSError, ValueError, KeyError):
                continue
    found.update(manager.states)
    return sorted(found.values(), key=lambda item: item.get("started_at", ""), reverse=True)


@app.post("/api/runs", status_code=202)
async def start_run(config: AppConfig) -> dict[str, Any]:
    run_id = manager.start(config)
    return {"run_id": run_id, "status": RunStatus.PENDING}


@app.post("/api/runs/{run_id}/rerun", status_code=202)
async def rerun(run_id: str, request: RerunRequest) -> dict[str, Any]:
    config = request.config or manager.configs.get(run_id)
    if config is None:
        raise HTTPException(
            409,
            "credentials are not persisted; provide config to rerun a historical server session",
        )
    only = set(request.plugins) or None
    try:
        BenchmarkEngine().selected_plugins(config, only=only)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    new_id = manager.start(config, only=only, parent_run_id=run_id)
    return {"run_id": new_id, "status": RunStatus.PENDING, "parent_run_id": run_id}


@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str, output_dir: str = "outputs") -> Any:
    result_file = Path(output_dir) / run_id / "run.json"
    if result_file.is_file():
        return JSONResponse(read_json(result_file))
    if run_id in manager.states:
        return manager.states[run_id]
    raise HTTPException(404, "run not found")


@app.get("/api/runs/{run_id}/comparison")
async def run_comparison(run_id: str, output_dir: str = "outputs") -> dict[str, Any]:
    result_file = Path(output_dir) / run_id / "run.json"
    if not result_file.is_file():
        raise HTTPException(404, "completed run not found")
    return read_json(result_file).get("comparisons", {})


@app.get("/api/runs/{run_id}/requests")
async def raw_requests(
    run_id: str,
    provider: str | None = None,
    plugin: str | None = None,
    status: str | None = None,
    case_id: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    output_dir: str = "outputs",
) -> dict[str, Any]:
    run_dir = Path(output_dir) / run_id
    result_file = run_dir / "run.json"
    if not result_file.is_file():
        raise HTTPException(404, "completed run not found")
    result = read_json(result_file)
    providers = result.get("providers", {})
    if provider is None:
        if len(providers) != 1:
            raise HTTPException(400, "provider is required for a multi-provider run")
        provider = next(iter(providers))
    if provider not in providers:
        raise HTTPException(404, "provider not found in run")
    available_plugins = providers[provider].get("plugins", {})
    if plugin is not None and plugin not in available_plugins:
        raise HTTPException(404, "plugin not found in provider run")
    provider_dir = (
        run_dir / "providers" / safe_name(provider) if len(providers) > 1 else run_dir
    )
    paths = (
        [provider_dir / "benchmarks" / plugin / "requests.jsonl"]
        if plugin
        else sorted(provider_dir.glob("benchmarks/*/requests.jsonl"))
    )
    records = []
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if status and record.get("status") != status:
                continue
            if case_id and case_id not in str(record.get("case_id", "")):
                continue
            records.append(record)
    return {"total": len(records), "offset": offset, "limit": limit, "items": records[offset : offset + limit]}


@app.get("/api/runs/{run_id}/events")
async def run_events(run_id: str) -> StreamingResponse:
    if run_id not in manager.states:
        raise HTTPException(404, "run not found")
    return StreamingResponse(manager.stream(run_id), media_type="text/event-stream")


@app.get("/api/runs/{run_id}/report")
async def run_report(run_id: str, output_dir: str = "outputs") -> FileResponse:
    report_file = Path(output_dir) / run_id / "report.html"
    if not report_file.is_file():
        raise HTTPException(404, "report not found")
    return FileResponse(report_file)


_DIST_DIR = Path(__file__).resolve().parent / "static"
if _DIST_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST_DIR / "assets"), name="assets")

    @app.get("/{path:path}")
    async def console(path: str) -> FileResponse:
        candidate = (_DIST_DIR / path).resolve()
        if candidate.is_file() and _DIST_DIR in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(_DIST_DIR / "index.html")
else:

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "name": "Provider Bench API",
            "docs": "/docs",
            "console": "Build the React app in web/ to enable the console.",
        }
