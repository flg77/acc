"""Tests for the shared TUI config helper — 033 WS-F.

``load_operator_mode()`` reads ``ACCConfig.operator_mode`` from the live
config and must NEVER raise: any failure (missing YAML, validation
error, import error) falls back to the safe ``"prod"`` security floor.
"""

from __future__ import annotations

from acc.tui.config_helpers import load_operator_mode


def test_load_operator_mode_returns_configured_value(monkeypatch):
    """When load_config yields a config, its operator_mode is returned."""
    import acc.config as cfg

    class _FakeConfig:
        operator_mode = "dev"

    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _FakeConfig())
    assert load_operator_mode() == "dev"


def test_load_operator_mode_returns_prod_when_configured(monkeypatch):
    """A prod config round-trips as 'prod' (not just the fallback)."""
    import acc.config as cfg

    class _FakeConfig:
        operator_mode = "prod"

    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _FakeConfig())
    assert load_operator_mode() == "prod"


def test_load_operator_mode_falls_back_to_prod_on_failure(monkeypatch):
    """Any exception from load_config collapses to the safe 'prod' floor."""
    import acc.config as cfg

    def _boom(*a, **k):
        raise RuntimeError("no config on disk")

    monkeypatch.setattr(cfg, "load_config", _boom)
    assert load_operator_mode() == "prod"


def test_load_operator_mode_never_raises_on_import_failure(monkeypatch):
    """Even if the config module is unavailable, the helper returns 'prod'.

    We simulate the import failing by removing the attribute the helper
    imports; the broad except must catch it.
    """
    import acc.config as cfg

    # A load_config that raises ImportError mimics the import-error path
    # the helper guards against.
    def _import_boom(*a, **k):
        raise ImportError("acc.config unavailable")

    monkeypatch.setattr(cfg, "load_config", _import_boom)
    assert load_operator_mode() == "prod"
