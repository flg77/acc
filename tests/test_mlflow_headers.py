"""Unit tests for the RHOAI workspace request-header plugin."""

import pytest

pytest.importorskip("mlflow")  # needs the [mlflow] extra (mlflow-skinny)

from acc.backends.mlflow_headers import (  # noqa: E402
    WORKSPACE_HEADER,
    WorkspaceHeaderProvider,
)


def test_header_added_when_workspace_set(monkeypatch):
    monkeypatch.setenv("MLFLOW_WORKSPACE", "mlflow")
    p = WorkspaceHeaderProvider()
    assert p.in_context() is True
    assert p.request_headers() == {WORKSPACE_HEADER: "mlflow"}


def test_noop_when_workspace_unset(monkeypatch):
    monkeypatch.delenv("MLFLOW_WORKSPACE", raising=False)
    p = WorkspaceHeaderProvider()
    assert p.in_context() is False
    assert p.request_headers() == {}


def test_noop_when_workspace_blank(monkeypatch):
    monkeypatch.setenv("MLFLOW_WORKSPACE", "   ")
    p = WorkspaceHeaderProvider()
    assert p.in_context() is False
    assert p.request_headers() == {}


def test_registered_as_entrypoint():
    """The plugin must be discoverable by MLflow's registry."""
    from mlflow.utils.plugins import get_entry_points

    names = {ep.name for ep in get_entry_points("mlflow.request_header_provider")}
    # Present only after an editable/real install; skip if the dist metadata
    # isn't built in this env (source checkout without install).
    if not names:
        pytest.skip("no installed dist metadata for entry points")
    assert "acc-workspace" in names
