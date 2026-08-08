from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from provider_bench.config import load_config
from provider_bench.plugins.registry import registry


def test_load_config_expands_environment_and_redacts_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_PROVIDER_KEY", "top-secret")
    path = tmp_path / "config.yaml"
    path.write_text(
        """
provider:
  name: test
  base_url: https://example.test/v1
  api_key: ${TEST_PROVIDER_KEY}
  model: model-a
  headers:
    X-Private-Token: ${TEST_PROVIDER_KEY}
benchmarks:
  latency:
    enabled: true
    repetitions: 2
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.provider is not None
    assert config.provider.api_key.get_secret_value() == "top-secret"
    assert config.safe_dict()["provider"]["api_key"] == "**********"
    assert config.safe_dict()["provider"]["headers"]["X-Private-Token"] == "**********"
    assert "top-secret" not in repr(config)


def test_missing_environment_variable_is_clear(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
provider:
  name: test
  base_url: https://example.test/v1
  api_key: ${DEFINITELY_NOT_CONFIGURED}
  model: model-a
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="DEFINITELY_NOT_CONFIGURED"):
        load_config(path)


def test_provider_names_must_be_unique() -> None:
    from provider_bench.models import AppConfig

    with pytest.raises(ValidationError, match="provider names must be unique"):
        AppConfig.model_validate(
            {
                "providers": [
                    {"name": "same", "base_url": "https://a.test/v1", "api_key": "x", "model": "m"},
                    {"name": "same", "base_url": "https://b.test/v1", "api_key": "y", "model": "m"},
                ]
            }
        )


def test_every_builtin_plugin_has_a_valid_default_configuration() -> None:
    for name, plugin in registry.items():
        settings = plugin.validate_config({"enabled": True})
        assert settings.enabled is True, name
