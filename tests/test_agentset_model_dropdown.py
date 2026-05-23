"""PR-MM2 — Agentset Model dropdown (1:1 subagent→model).

Mounts the Ecosystem screen, drives the model Select + Set-model button,
and asserts the highlighted agent's model is written into the editor
YAML (round-tripping through CollectiveSpec).
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import DataTable, Select, TextArea

from acc.tui.screens.ecosystem import EcosystemScreen

_REGISTRY = """\
models:
  - model_id: claude-sonnet
    backend: anthropic
    model: claude-sonnet-4-6
    label: "Sonnet (reviewer)"
  - model_id: ollama-small
    backend: ollama
    model: "llama3.2:3b"
    label: "Ollama small (worker)"
"""

_COLLECTIVE = """\
collective_id: sol-01
agents:
  - role: coding_agent_implementer
    replicas: 1
  - role: reviewer
    replicas: 1
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Isolate manifest roots so on_mount loaders are deterministic/empty.
    for var in ("ACC_ROLES_ROOT", "ACC_SKILLS_ROOT", "ACC_MCPS_ROOT"):
        d = tmp_path / var.lower()
        d.mkdir(exist_ok=True)
        monkeypatch.setenv(var, str(d))
    reg = tmp_path / "models.yaml"
    reg.write_text(_REGISTRY, encoding="utf-8")
    monkeypatch.setenv("ACC_MODELS_PATH", str(reg))
    return tmp_path


class _Harness(App):
    def on_mount(self) -> None:
        self.push_screen(EcosystemScreen())


async def _open_agentset(pilot, app):
    """Switch to the Agentset tab + seed the editor with a 2-agent spec."""
    from acc.collective import CollectiveSpec
    import yaml
    screen = app.screen
    screen.query_one("#collective-editor", TextArea).text = _COLLECTIVE
    spec = CollectiveSpec.model_validate(yaml.safe_load(_COLLECTIVE))
    screen._refresh_agentset_table(spec)
    await pilot.pause()
    return screen


@pytest.mark.asyncio
async def test_model_dropdown_populated_from_registry(env):
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        select = screen.query_one("#agentset-model-select", Select)
        # The registry's model_ids are options (plus the default blank).
        values = {v for _label, v in select._options} if hasattr(select, "_options") else set()
        # Fallback: assert we can set a registry value without error.
        select.value = "claude-sonnet"
        await pilot.pause()
        assert select.value == "claude-sonnet"


@pytest.mark.asyncio
async def test_set_model_writes_into_editor_for_highlighted_agent(env):
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = await _open_agentset(pilot, app)
        table = screen.query_one("#agentset-table", DataTable)
        table.move_cursor(row=1)  # the reviewer agent
        await pilot.pause()
        screen.query_one("#agentset-model-select", Select).value = "claude-sonnet"
        screen._handle_agentset_set_model()
        await pilot.pause()

        # Editor YAML now pins the reviewer's model; parse to confirm 1:1.
        import yaml
        from acc.collective import CollectiveSpec
        spec = CollectiveSpec.model_validate(
            yaml.safe_load(screen.query_one("#collective-editor", TextArea).text)
        )
        by_role = {a.role: a for a in spec.agents}
        assert by_role["reviewer"].model == "claude-sonnet"
        assert by_role["coding_agent_implementer"].model is None  # untouched


@pytest.mark.asyncio
async def test_set_default_clears_model(env):
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = await _open_agentset(pilot, app)
        table = screen.query_one("#agentset-table", DataTable)
        table.move_cursor(row=0)
        await pilot.pause()
        sel = screen.query_one("#agentset-model-select", Select)
        # First assign, then clear back to default.
        sel.value = "ollama-small"
        screen._handle_agentset_set_model()
        await pilot.pause()
        sel.value = ""  # (collective default)
        screen._handle_agentset_set_model()
        await pilot.pause()

        import yaml
        from acc.collective import CollectiveSpec
        spec = CollectiveSpec.model_validate(
            yaml.safe_load(screen.query_one("#collective-editor", TextArea).text)
        )
        assert spec.agents[0].model is None


@pytest.mark.asyncio
async def test_set_model_without_selection_warns(env):
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        # Empty editor → parse yields empty spec (no agents).
        screen.query_one("#collective-editor", TextArea).text = "collective_id: sol-01\n"
        statuses: list[str] = []
        screen.query_one("#agentset-status").update = lambda m: statuses.append(str(m))  # type: ignore
        screen._handle_agentset_set_model()
        await pilot.pause()
        assert any("highlight an agent" in s.lower() for s in statuses)
