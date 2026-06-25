"""Headless mount test for the Configuration pane's ROLE → MODEL section.

Mounts the real ConfigurationScreen via Textual's run_test (no NATS/LLM),
points it at a temp collective.yaml, and verifies the table renders + the
"Seed split defaults" writeback persists AgentSpec.model (the locked design).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import yaml
from textual.app import App
from textual.widgets import DataTable


def _mk_collective(tmp: Path) -> Path:
    p = tmp / "collective.yaml"
    p.write_text(
        "collective_id: cfg-test\n"
        "agents:\n"
        "  - role: assistant\n"
        "  - role: reviewer\n"
        "  - role: coding_agent\n"
        "  - role: ingester\n",
        encoding="utf-8",
    )
    return p


def test_role_model_renders_and_seeds(tmp_path, monkeypatch):
    cpath = _mk_collective(tmp_path)
    monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(cpath))
    monkeypatch.setenv("ACC_SKILLS_ROOT", "skills")
    monkeypatch.setenv("ACC_MCPS_ROOT", "mcps")
    monkeypatch.setenv("ACC_ROLES_ROOT", "roles")

    from acc.tui.screens.configuration import ConfigurationScreen

    async def go():
        class _H(App):
            def on_mount(self) -> None:
                self.push_screen(ConfigurationScreen())

        app = _H()
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.pause()
            screen = app.screen
            table = screen.query_one("#role-model-table", DataTable)
            # one row per distinct role (4)
            assert table.row_count == 4, table.row_count
            # drive the seed-split writeback directly (button handler)
            screen._on_rolemodel_seed()
            await pilot.pause()

    asyncio.run(go())

    data = yaml.safe_load(cpath.read_text(encoding="utf-8"))
    models = {a["role"]: a.get("model") for a in data["agents"]}
    assert models["assistant"] == "claude-opus"        # control/review → strongest
    assert models["reviewer"] == "claude-opus"
    assert models["coding_agent"] == "maas-qwen3-14b"   # worker → cheap
    assert models["ingester"] == "maas-qwen3-14b"       # substrate → cheap


def test_backend_health_rollup_groups_and_flags_degraded():
    """N7 — the per-backend health rollup groups agents by backend and
    flags degraded ones (25.6.26 image 6: monitor the active LLMs)."""
    from types import SimpleNamespace
    from acc.tui.screens.configuration import ConfigurationScreen

    agents = {
        "a1": SimpleNamespace(llm_backend="vllm", llm_health="ok", llm_p50_latency_ms=120),
        "a2": SimpleNamespace(llm_backend="vllm", llm_health="degraded", llm_p50_latency_ms=900),
        "a3": SimpleNamespace(llm_backend="anthropic", llm_health="ok", llm_p50_latency_ms=80),
    }
    rollup = ConfigurationScreen._backend_health_rollup(agents)
    assert "vllm" in rollup and "anthropic" in rollup
    assert "1/2 degraded" in rollup          # vllm has one bad agent
    assert "1 ok" in rollup                   # anthropic all healthy
    assert ConfigurationScreen._backend_health_rollup({}) == "No active LLM backends."
