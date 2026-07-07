"""Pilot + unit tests: the Configuration pane's MODEL REGISTRY CRUD.

Covers the read-only→CRUD upgrade of the models.yaml registry: the roles
(role_models reverse) column, Add/Edit via the modal writing models.yaml,
Delete, the role→default map, and the model-editor's validation.  The reload
broadcast + per-model Test are exercised for "does not crash" (no live NATS /
endpoint in the harness).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable, Input, Select

import acc.tui.app as appmod
from acc.models import ModelEntry, load_registry
from acc.tui.screens.configuration import ConfigurationScreen
from acc.tui.widgets.model_editor_modal import ModelEditorModal

_APP_CSS = Path(appmod.__file__).parent / "app.tcss"


@pytest.fixture()
def models_file(tmp_path, monkeypatch):
    """A seeded temp models.yaml selected via ACC_MODELS_PATH."""
    p = tmp_path / "models.yaml"
    p.write_text(
        "# hdr\n"
        "models:\n"
        "  - model_id: a\n    backend: vllm\n    model: m-a\n    base_url: http://a\n"
        "  - model_id: b\n    backend: anthropic\n    model: m-b\n"
        "role_models:\n  coding: a\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ACC_MODELS_PATH", str(p))
    return p


class _ConfigHarness(App):
    CSS_PATH = _APP_CSS

    def on_mount(self) -> None:
        self.push_screen(ConfigurationScreen())


# --------------------------------------------------------------------------
# Pure row builder
# --------------------------------------------------------------------------


def test_registry_rows_show_roles_reverse_map():
    entries = [
        ModelEntry(model_id="a", backend="vllm", model="m-a"),
        ModelEntry(model_id="b", backend="anthropic", model="m-b"),
    ]
    rows = ConfigurationScreen._model_registry_rows(
        entries, {"coding": "a", "reviewer": "a", "planner": "b"}
    )
    # last column = the roles defaulting to each model, sorted
    assert rows[0][0] == "a" and rows[0][4] == "coding, reviewer"
    assert rows[1][0] == "b" and rows[1][4] == "planner"


def test_registry_rows_empty_registry_explains():
    rows = ConfigurationScreen._model_registry_rows([], {})
    assert len(rows) == 1 and "no models.yaml" in rows[0][1]


# --------------------------------------------------------------------------
# CRUD through the screen
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_renders_with_roles_column(models_file):
    app = _ConfigHarness()
    async with app.run_test(size=(150, 55)) as pilot:
        await pilot.pause()
        await pilot.pause()
        cfg = app.screen
        table = cfg.query_one("#llm-registry-table", DataTable)
        assert [str(c.label) for c in table.columns.values()] == [
            "model_id", "backend", "model", "base_url", "roles"
        ]
        assert table.row_count == 2
        assert cfg._registry_model_ids == ["a", "b"]


@pytest.mark.asyncio
async def test_add_model_via_editor_writes_models_yaml(models_file):
    app = _ConfigHarness()
    async with app.run_test(size=(150, 55)) as pilot:
        await pilot.pause()
        await pilot.pause()
        cfg = app.screen
        cfg._open_model_editor(None)
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ModelEditorModal)
        app.screen.query_one("#model-editor-id", Input).value = "c-new"
        app.screen.query_one("#model-editor-backend", Select).value = "ollama"
        app.screen.query_one("#model-editor-model", Input).value = "m-c"
        await pilot.click("#model-editor-save")
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, ConfigurationScreen)
        assert [e.model_id for e in load_registry().models] == ["a", "b", "c-new"]


@pytest.mark.asyncio
async def test_delete_selected_model(models_file):
    app = _ConfigHarness()
    async with app.run_test(size=(150, 55)) as pilot:
        await pilot.pause()
        await pilot.pause()
        cfg = app.screen
        cfg.query_one("#llm-registry-table", DataTable).move_cursor(row=0)  # "a"
        cfg._on_model_delete()
        await pilot.pause()
        reg = load_registry()
        assert [e.model_id for e in reg.models] == ["b"]
        assert reg.role_models == {}  # coding→a dropped with the model


@pytest.mark.asyncio
async def test_set_role_default_writes_role_models(models_file):
    app = _ConfigHarness()
    async with app.run_test(size=(150, 55)) as pilot:
        await pilot.pause()
        await pilot.pause()
        cfg = app.screen
        cfg.query_one("#registry-role", Select).value = "coding"
        cfg.query_one("#registry-model", Select).value = "b"
        cfg._on_registry_setrole()
        await pilot.pause()
        assert load_registry().role_models.get("coding") == "b"


@pytest.mark.asyncio
async def test_reload_and_test_do_not_crash(models_file):
    app = _ConfigHarness()
    async with app.run_test(size=(150, 55)) as pilot:
        await pilot.pause()
        await pilot.pause()
        cfg = app.screen
        cfg.query_one("#llm-registry-table", DataTable).move_cursor(row=1)  # "b" (hosted)
        cfg._on_model_test()   # anthropic has no base_url → "hosted API" note
        cfg._on_reload_agents()  # no NATS client → graceful message
        await pilot.pause()


# --------------------------------------------------------------------------
# Model editor validation
# --------------------------------------------------------------------------


class _ModalHarness(App):
    def on_mount(self) -> None:
        self.push_screen(ModelEditorModal(None))


@pytest.mark.asyncio
async def test_model_editor_requires_id_and_backend():
    app = _ModalHarness()
    async with app.run_test(size=(90, 40)) as pilot:
        await pilot.pause()
        modal = app.screen
        assert isinstance(modal, ModelEditorModal)
        # empty model_id → Save is rejected (modal stays open, error shown)
        await pilot.click("#model-editor-save")
        await pilot.pause()
        assert isinstance(app.screen, ModelEditorModal)
        assert "required" in str(app.screen.query_one("#model-editor-error").render())
