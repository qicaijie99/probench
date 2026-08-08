from __future__ import annotations

import importlib
import pkgutil
from importlib.metadata import entry_points
from collections.abc import Iterable
from typing import Any

from provider_bench.plugins.base import BenchmarkPlugin


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, type[BenchmarkPlugin]] = {}
        self._discovered = False

    def register(self, plugin: type[BenchmarkPlugin]) -> type[BenchmarkPlugin]:
        if not plugin.name:
            raise ValueError("plugin name cannot be empty")
        existing = self._plugins.get(plugin.name)
        if existing is not None and existing is not plugin:
            raise ValueError(f"plugin {plugin.name!r} is already registered")
        self._plugins[plugin.name] = plugin
        return plugin

    def discover(self) -> None:
        if self._discovered:
            return
        package = importlib.import_module("provider_bench.plugins")
        ignored = {"base", "registry", "stats"}
        for module in pkgutil.iter_modules(package.__path__, f"{package.__name__}."):
            if module.name.rsplit(".", 1)[-1] not in ignored:
                importlib.import_module(module.name)
        for entry_point in entry_points(group="provider_bench.plugins"):
            loaded = entry_point.load()
            if isinstance(loaded, type) and issubclass(loaded, BenchmarkPlugin):
                self.register(loaded)
        self._discovered = True

    def get(self, name: str) -> type[BenchmarkPlugin]:
        self.discover()
        try:
            return self._plugins[name]
        except KeyError as exc:
            raise KeyError(f"unknown benchmark plugin: {name}") from exc

    def names(self) -> list[str]:
        self.discover()
        return sorted(self._plugins)

    def items(self) -> Iterable[tuple[str, type[BenchmarkPlugin]]]:
        self.discover()
        return self._plugins.items()

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "description": plugin.description,
                "config_schema": plugin.settings_model.model_json_schema(),
            }
            for name, plugin in sorted(self.items())
        ]


registry = PluginRegistry()


def register_plugin(plugin: type[BenchmarkPlugin]) -> type[BenchmarkPlugin]:
    return registry.register(plugin)
