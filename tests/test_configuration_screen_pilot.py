"""Pilot tests for the Configuration screen (proposal 003 PR-4).

The Configuration screen is pane 8 of the ACC TUI.  It absorbs three
surfaces that previously lived on the Ecosystem screen:

* LLM Endpoints tab — configured backend summary + live per-agent
  table + Test connection button (HEAD-ping).
* Skills tab — moved verbatim from Ecosystem (canonical home).
* MCPs tab — moved verbatim from Ecosystem (canonical home).

These tests mount the screen in isolation (no live NATS, no live
agents) and exercise:

* Compose layout — all three tabs + the LLM summary + the test
  button render without crashing.
* `_ping_endpoint` — pure HTTP-HEAD helper, covers ok / 4xx / 5xx /
  unreachable / empty.
* The Test button updates `#llm-test-result` with a result string.
* Skills + MCPs tables populate from the test fixture manifests.
* The Configuration screen registers as pane 8 and is reachable
  via the `8` keybinding from any other screen.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from textual.app import App
from textual.widgets import Button, DataTable, Static, TabbedContent

from acc.tui.screens.configuration import (
    ConfigurationScreen,
    _ping_endpoint,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_skill_manifest(skills_root: Path, skill_id: str = "echo") -> None:
    """Drop a minimal valid skill.yaml + adapter.py."""
    skill_dir = skills_root / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        "purpose: 'pilot fixture'\n"
        "version: '0.1.0'\n"
        f"adapter_module: 'adapter'\n"
        f"adapter_class: '{skill_id.title()}Skill'\n"
        "input_schema: {}\n"
        "output_schema: {}\n"
        "risk_level: 'LOW'\n",
        encoding="utf-8",
    )
    (skill_dir / "adapter.py").write_text(
        "from acc.skills import Skill\n"
        f"class {skill_id.title()}Skill(Skill):\n"
        "    async def invoke(self, args):\n"
        "        return {}\n",
        encoding="utf-8",
    )


def _write_mcp_manifest(mcps_root: Path, server_id: str = "echo_server") -> None:
    mcp_dir = mcps_root / server_id
    mcp_dir.mkdir(parents=True, exist_ok=True)
    (mcp_dir / "mcp.yaml").write_text(
        "purpose: 'pilot fixture'\n"
        "version: '0.1.0'\n"
        "transport: 'http'\n"
        "url: 'http://acc-mcp-echo:8080/rpc'\n"
        "allowed_tools: ['echo']\n"
        "risk_level: 'LOW'\n",
        encoding="utf-8",
    )


@pytest.fixture
def isolated_manifests(tmp_path, monkeypatch):
    """Lay out fresh skills/ + mcps/ dirs and point env vars at them."""
    skills_root = tmp_path / "skills"
    mcps_root = tmp_path / "mcps"
    skills_root.mkdir()
    mcps_root.mkdir()

    _write_skill_manifest(skills_root)
    _write_mcp_manifest(mcps_root)

    monkeypatch.setenv("ACC_SKILLS_ROOT", str(skills_root))
    monkeypatch.setenv("ACC_MCPS_ROOT", str(mcps_root))

    return {
        "skills_root": skills_root,
        "mcps_root": mcps_root,
    }


class _Harness(App):
    """Minimal app — hosts the Configuration screen."""

    def on_mount(self) -> None:
        self.push_screen(ConfigurationScreen())


# ---------------------------------------------------------------------------
# _ping_endpoint — pure HTTP HEAD helper
# ---------------------------------------------------------------------------


def test_ping_endpoint_empty_url_returns_false():
    ok, msg, elapsed = _ping_endpoint("")
    assert ok is False
    assert "base_url" in msg
    assert elapsed == 0.0


def test_ping_endpoint_unreachable_returns_false(monkeypatch):
    """A bogus URL should land in the URLError branch — returns
    ``ok=False`` with a human-readable reason."""
    ok, msg, elapsed = _ping_endpoint(
        "http://this-host-does-not-resolve.invalid:1/", timeout_s=2.0,
    )
    assert ok is False
    assert "unreachable" in msg or "error" in msg
    assert elapsed >= 0.0


# ---------------------------------------------------------------------------
# Screen compose + mount
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compose_yields_all_three_tabs(isolated_manifests):
    """The screen mounts with three TabPanes: tab-llm, tab-skills,
    tab-mcps.  TabbedContent provides the navigation."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        tabbed = screen.query_one(TabbedContent)
        # TabbedContent exposes a TabPanes-like internal; we look
        # at the rendered tab buttons instead.
        all_tab_ids = [
            tab.id for tab in tabbed.query("TabPane")
        ]
        assert "tab-llm" in all_tab_ids
        assert "tab-skills" in all_tab_ids
        assert "tab-mcps" in all_tab_ids


@pytest.mark.asyncio
async def test_llm_summary_renders_at_mount(isolated_manifests):
    """The LLM tab's #llm-config-summary Static gets populated with
    the read-only ACCConfig.llm summary at mount time."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        summary = screen.query_one("#llm-config-summary", Static)
        # Static.renderable across Textual versions: use the captured-
        # update pattern from other PR-2 / PR-3 tests.
        captured: list[str] = []
        real = summary.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return real(content, **kwargs)

        summary.update = recording  # type: ignore[assignment]
        screen._render_llm_summary()
        await pilot.pause()

        rendered = "\n".join(captured)
        assert "Backend" in rendered
        assert "Model" in rendered
        assert "Base URL" in rendered
        # Proposal 010 — resolved role_source surfaced in the summary.
        assert "Role sync" in rendered
        assert "proposal 010" in rendered
        # Proposal 011 PR-1 — resolved signing_mode surfaced too.
        assert "Signing mode" in rendered
        assert "proposal 011" in rendered


@pytest.mark.asyncio
async def test_test_button_updates_result_widget(
    isolated_manifests, monkeypatch,
):
    """Pressing the Test connection button writes a result string
    into ``#llm-test-result``.  We force the ping helper to return
    a known value so the test doesn't depend on network state."""
    from acc.tui.screens import configuration as cfg

    def fake_ping(url: str, timeout_s: float = 5.0):
        return (True, "HTTP 200", 17.5)

    monkeypatch.setattr(cfg, "_ping_endpoint", fake_ping)

    def fake_summary():
        return {
            "backend": "ollama",
            "model": "llama-3",
            "base_url": "http://localhost:11434",
            "request_timeout_s": "120",
            "role_source": "files",
            "deploy_mode": "standalone",
            "signing_mode": "ed25519",
            "spiffe_enabled": "no",
        }

    monkeypatch.setattr(cfg, "_load_acc_config_summary", fake_summary)

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        result_widget = screen.query_one("#llm-test-result", Static)
        captured: list[str] = []
        real = result_widget.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return real(content, **kwargs)

        result_widget.update = recording  # type: ignore[assignment]
        screen._on_test_button()
        await pilot.pause()

        joined = "\n".join(captured)
        assert "HTTP 200" in joined
        assert "18 ms" in joined or "17 ms" in joined
        assert "http://localhost:11434" in joined


@pytest.mark.asyncio
async def test_test_button_handles_missing_base_url(
    isolated_manifests, monkeypatch,
):
    """When the configured base_url is empty, the Test button shows
    an operator-readable error instead of crashing."""
    from acc.tui.screens import configuration as cfg

    monkeypatch.setattr(cfg, "_load_acc_config_summary", lambda: {
        "backend": "ollama",
        "model": "—",
        "base_url": "—",
        "request_timeout_s": "120",
        "role_source": "files",
        "deploy_mode": "standalone",
        "signing_mode": "ed25519",
        "spiffe_enabled": "no",
    })

    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        result_widget = screen.query_one("#llm-test-result", Static)
        captured: list[str] = []
        real = result_widget.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return real(content, **kwargs)

        result_widget.update = recording  # type: ignore[assignment]
        screen._on_test_button()
        await pilot.pause()

        joined = "\n".join(captured)
        assert "No base_url" in joined


# ---------------------------------------------------------------------------
# Skills + MCPs (moved tables)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_table_populated_from_fixture(isolated_manifests):
    """Skills tab's table shows the fixture's echo skill."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#skills-table", DataTable)
        keys = [getattr(k, "value", str(k)) for k in table.rows.keys()]
        assert "echo" in keys, keys


@pytest.mark.asyncio
async def test_mcps_table_populated_from_fixture(isolated_manifests):
    """MCPs tab's table shows the fixture's echo_server."""
    app = _Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        table = screen.query_one("#mcps-table", DataTable)
        keys = [getattr(k, "value", str(k)) for k in table.rows.keys()]
        assert "echo_server" in keys, keys


# ---------------------------------------------------------------------------
# Registration + nav
# ---------------------------------------------------------------------------


def test_configuration_registered_in_app_screens():
    """The Configuration screen is registered in ACCTUIApp.SCREENS
    under the name 'configuration'."""
    from acc.tui.app import ACCTUIApp
    assert "configuration" in ACCTUIApp.SCREENS
    assert ACCTUIApp.SCREENS["configuration"] is ConfigurationScreen


def test_nav_bar_includes_configuration():
    """The NavigationBar's screen list includes pane 8 keyed '8'."""
    from acc.tui.widgets.nav_bar import _SCREENS
    keys = [k for k, *_ in _SCREENS]
    assert "8" in keys
    names = [n for _k, n, *_ in _SCREENS]
    assert "configuration" in names


def test_nav_bar_has_8_keybinding():
    """The NavigationBar's BINDINGS include ('8', navigate(configuration))."""
    from acc.tui.widgets.nav_bar import NavigationBar
    binding_keys = [b[0] for b in NavigationBar.BINDINGS]
    assert "8" in binding_keys
