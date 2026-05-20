"""Regression tests for `_load_acc_config_summary()`.

The TUI Configuration screen's "CONFIGURED BACKEND" panel was silently
showing Pydantic defaults because the helper instantiated
`LLMConfig()` / `ACCConfig()` with no arguments — bypassing both
acc-config.yaml AND the documented `ACC_LLM_*` env overlay.  These
tests pin the fix: the helper now calls `acc.config.load_config()`
and reflects YAML + env overrides.
"""

from __future__ import annotations

import textwrap

import pytest

# The configuration screen imports Textual transitively; skip on
# environments without it (matches the surrounding tui test conventions).
pytest.importorskip("textual")

from acc.tui.screens.configuration import (  # noqa: E402
    _load_acc_config_summary,
    _resolve_acc_config_path,
)


def _write_yaml(tmp_path, body: str):
    p = tmp_path / "acc-config.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


class TestResolveAccConfigPath:
    def test_explicit_env_var_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACC_CONFIG_PATH", str(tmp_path / "custom.yaml"))
        assert _resolve_acc_config_path() == str(tmp_path / "custom.yaml")

    def test_falls_back_to_cwd(self, monkeypatch):
        # /app/acc-config.yaml is the container mount; outside a
        # container the cwd default must take over.
        monkeypatch.delenv("ACC_CONFIG_PATH", raising=False)
        # We can't easily simulate /app being absent on every host, but
        # _resolve_acc_config_path returns one of two known sentinels.
        result = _resolve_acc_config_path()
        assert result in ("/app/acc-config.yaml", "acc-config.yaml")


class TestLoadAccConfigSummary:
    def test_reads_yaml(self, monkeypatch, tmp_path):
        p = _write_yaml(tmp_path, """
            deploy_mode: standalone
            llm:
              backend: openai_compat
              model: my-model-id
              base_url: http://test:8001/v1
              request_timeout_s: 42
        """)
        monkeypatch.setenv("ACC_CONFIG_PATH", str(p))
        # Make sure stale env vars do not contaminate the assertion.
        for k in ("ACC_LLM_BACKEND", "ACC_LLM_MODEL",
                  "ACC_LLM_BASE_URL", "ACC_LLM_TIMEOUT_S"):
            monkeypatch.delenv(k, raising=False)

        summary = _load_acc_config_summary()

        assert summary["backend"] == "openai_compat"
        assert summary["model"] == "my-model-id"
        assert summary["base_url"] == "http://test:8001/v1"
        assert summary["request_timeout_s"] == "42"

    def test_env_overrides_yaml(self, monkeypatch, tmp_path):
        """The regression: ACC_LLM_* must override the YAML — this was
        silently broken before the bare-LLMConfig() removal."""
        p = _write_yaml(tmp_path, """
            deploy_mode: standalone
            llm:
              backend: openai_compat
              model: yaml-model
              base_url: http://yaml-host:9999/v1
              request_timeout_s: 30
        """)
        monkeypatch.setenv("ACC_CONFIG_PATH", str(p))
        monkeypatch.setenv("ACC_LLM_BACKEND", "anthropic")
        monkeypatch.setenv("ACC_LLM_MODEL", "claude-sonnet-4-5")
        monkeypatch.setenv("ACC_LLM_BASE_URL", "https://api.anthropic.com")
        monkeypatch.setenv("ACC_LLM_TIMEOUT_S", "180")

        summary = _load_acc_config_summary()

        assert summary["backend"] == "anthropic"
        assert summary["model"] == "claude-sonnet-4-5"
        assert summary["base_url"] == "https://api.anthropic.com"
        assert summary["request_timeout_s"] == "180"

    def test_missing_yaml_returns_dashes_not_crash(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ACC_CONFIG_PATH", str(tmp_path / "does-not-exist.yaml"))

        summary = _load_acc_config_summary()

        # Every key present so the UI never KeyErrors.
        for key in ("backend", "model", "base_url", "request_timeout_s",
                    "role_source", "deploy_mode", "signing_mode",
                    "spiffe_enabled", "nkey_enabled", "nkey_role"):
            assert summary[key] == "—", f"{key} should be the dash placeholder"
