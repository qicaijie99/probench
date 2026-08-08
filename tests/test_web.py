import httpx
from pathlib import Path

from provider_bench.engine import BenchmarkEngine
from provider_bench.models import AppConfig
from provider_bench.web import app as web_app_module
from provider_bench.web.app import RunManager, app
from provider_bench.web.app import manager

from .fakes import FakeProvider


async def test_web_exposes_health_and_dynamic_registry() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/api/health")
        plugins = await client.get("/api/plugins")
    assert health.json() == {"status": "ok"}
    names = {plugin["name"] for plugin in plugins.json()}
    assert names == {
        "billing",
        "burst",
        "compatibility",
        "concurrency",
        "latency",
        "model_identity",
        "quality",
        "structured_output",
        "tool_calling",
    }


async def test_connection_endpoint_does_not_echo_credentials(monkeypatch) -> None:
    monkeypatch.setattr(
        web_app_module,
        "OpenAICompatibleProvider",
        lambda config: FakeProvider(config.name, config.model),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/providers/test",
            json={
                "name": "candidate",
                "base_url": "https://candidate.test/v1",
                "api_key": "never-echo-this",
                "model": "candidate-model",
            },
        )
    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert "never-echo-this" not in response.text


async def test_raw_request_endpoint_reads_single_and_multi_provider_runs(
    tmp_path: Path,
) -> None:
    config = AppConfig.model_validate(
        {
            "providers": [
                {"name": "a", "base_url": "https://a.test/v1", "api_key": "x", "model": "m-a"},
                {"name": "b", "base_url": "https://b.test/v1", "api_key": "y", "model": "m-b"},
            ],
            "output_dir": str(tmp_path),
            "benchmarks": {"latency": {"enabled": True, "warmup": 0, "repetitions": 1}},
        }
    )
    await BenchmarkEngine(
        provider_factory=lambda item: FakeProvider(item.name, item.model)
    ).run(config, run_id="web-raw")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        missing_provider = await client.get(
            "/api/runs/web-raw/requests", params={"output_dir": str(tmp_path)}
        )
        response = await client.get(
            "/api/runs/web-raw/requests",
            params={"output_dir": str(tmp_path), "provider": "b", "plugin": "latency"},
        )
    assert missing_provider.status_code == 400
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["provider"] == "b"


async def test_run_manager_replays_terminal_sse_events() -> None:
    run_manager = RunManager()
    run_manager.states["run"] = {"run_id": "run", "status": "PENDING"}
    await run_manager.emit({"type": "run.started", "run_id": "run", "status": "RUNNING"})
    await run_manager.emit({"type": "run.completed", "run_id": "run", "status": "COMPLETED"})
    events = [event async for event in run_manager.stream("run")]
    assert len(events) == 2
    assert "run.completed" in events[-1]


async def test_rerun_endpoint_can_select_one_plugin_from_live_session(monkeypatch) -> None:
    selected: list[set[str] | None] = []

    class StubEngine:
        def selected_plugins(self, config, *, only=None, skip=None):
            return list(only or config.benchmarks)

        async def run(self, config, *, run_id, only=None, event_handler=None, **kwargs):
            selected.append(only)
            await event_handler({"type": "run.started", "run_id": run_id, "status": "RUNNING"})
            await event_handler({"type": "run.completed", "run_id": run_id, "status": "COMPLETED"})

    monkeypatch.setattr(web_app_module, "BenchmarkEngine", StubEngine)
    config = AppConfig.model_validate(
        {
            "provider": {
                "name": "fake",
                "base_url": "https://fake.test/v1",
                "api_key": "secret",
                "model": "fake-model",
            },
            "benchmarks": {"latency": {"enabled": True}, "billing": {"enabled": False}},
        }
    )
    manager.configs["original"] = config
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/runs/original/rerun", json={"plugins": ["latency"]}
        )
    assert response.status_code == 202
    new_id = response.json()["run_id"]
    await manager.tasks[new_id]
    assert selected == [{"latency"}]
    for mapping in (manager.tasks, manager.states, manager.events, manager.configs):
        mapping.pop(new_id, None)
    manager.configs.pop("original", None)
