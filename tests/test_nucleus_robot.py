"""Robot (pilot-driven) TUI tests — standard operator scenarios.

These drive REAL widget interactions (select a role, read the form) through
the Textual pilot + interleave the live per-tick snapshot push, rather than
calling handlers directly.  They exist because the 25.6-2.26 manual test hit
a Nucleus regression — the role dropdown was "not selectable" / stuck on the
default role with the screen blinking — that handler-level tests masked.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Button, Input, Select, Static

from acc.tui.screens.infuse import InfuseScreen
from acc.tui.models import AgentSnapshot, CollectiveSnapshot


class _Host(App):
    def on_mount(self) -> None:
        self.push_screen(InfuseScreen())


@pytest.mark.asyncio
async def test_role_select_sticks_under_snapshot_churn(monkeypatch):
    """Standard scenario: operator switches the Nucleus role to 'assistant'
    (to fix its token budget) while the app keeps pushing snapshots.

    The selection must STICK and the form must reflect the chosen role —
    reproduces the 25.6-2.26 "assistant not selectable / stuck on default"
    bug (image 8).
    """
    monkeypatch.setenv("ACC_ROLES_ROOT", "roles")
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        sel = screen.query_one("#select-role", Select)

        snap = CollectiveSnapshot(collective_id="sol-01")
        sel.value = "assistant"
        # Pump the message queue while the live snapshot loop keeps ticking.
        for _ in range(6):
            screen.apply_snapshot(snap)
            await pilot.pause()

        assert sel.value == "assistant", (
            f"role select reverted to {sel.value!r} under snapshot churn"
        )
        tb = screen.query_one("#input-token-budget", Input).value
        assert tb.startswith("20480"), (
            f"token_budget should be the 20480 default, got {tb!r}"
        )


@pytest.mark.asyncio
async def test_infused_role_visible_in_nucleus_dropdown(monkeypatch):
    """Standard scenario: an infused pack role (e.g. an auto-researcher) must
    appear in the Nucleus role dropdown — not only the Ecosystem library
    (25.6-2.26: infused autoresearcher invisible in Nucleus, images 2/3/8)."""
    import acc.tui.screens.infuse as infuse_mod

    monkeypatch.setenv("ACC_ROLES_ROOT", "roles")
    infused = sorted([
        "arbiter", "assistant", "compliance_officer", "ingester",
        "observer", "orchestrator", "reviewer", "autoresearcher",
    ])
    # The dropdown enumerates in-tree ∪ installed-pack roles via this helper.
    monkeypatch.setattr(infuse_mod, "list_all_role_names", lambda root: infused)

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        sel = app.screen.query_one("#select-role", Select)
        options = [v for _, v in sel._options]
        assert "autoresearcher" in options, (
            f"infused role missing from the Nucleus dropdown: {options}"
        )


@pytest.mark.asyncio
async def test_edited_token_budget_holds_under_churn(monkeypatch):
    """Standard scenario: operator selects a role, edits its token_budget to
    fix an exhausted budget, and the edit must HOLD while snapshots tick
    (the corrective flow from 25.6-2.26 image 6→8)."""
    monkeypatch.setenv("ACC_ROLES_ROOT", "roles")
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        sel = screen.query_one("#select-role", Select)
        sel.value = "assistant"
        await pilot.pause()
        tb_input = screen.query_one("#input-token-budget", Input)
        tb_input.value = "8192"            # operator raises the budget
        snap = CollectiveSnapshot(collective_id="sol-01")
        for _ in range(6):
            screen.apply_snapshot(snap)
            await pilot.pause()
        assert sel.value == "assistant", f"role reverted to {sel.value!r}"
        assert tb_input.value == "8192", (
            f"edited token_budget lost under churn: {tb_input.value!r}"
        )


@pytest.mark.asyncio
async def test_active_llm_falls_back_to_live_backend(monkeypatch):
    """Agent→model mapping: when collective.yaml has no per-role binding, the
    Nucleus Active-LLM line shows the backend a RUNNING agent actually uses,
    not a bare '—' (25.6-2.26 image 8 'Active LLM: —')."""
    monkeypatch.setenv("ACC_ROLES_ROOT", "roles")
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        snap = CollectiveSnapshot(collective_id="sol-01")
        snap.agents["assistant-1"] = AgentSnapshot(
            agent_id="assistant-1", role="assistant",
            llm_backend="vllm", llm_model="llama-3.2-3B",
            last_heartbeat_ts=1.0,
        )
        screen.apply_snapshot(snap)
        live = screen._live_backend_for_role("assistant")
        assert "vllm" in live and "llama-3.2-3B" in live and "(live)" in live, live


@pytest.mark.asyncio
async def test_nucleus_dev_prod_stage_gates_build_button(monkeypatch):
    """26.6.26 finding #4 — Nucleus differentiates DEV/PROD: a security-floor
    badge renders, and the Build-package button is hidden in the DEV
    (finetune) stage, revealed only after switching the Stage selector to
    PROD (build an immutable package)."""
    monkeypatch.setenv("ACC_ROLES_ROOT", "roles")
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        # The security-floor (DEV/PROD) badge is painted (capture the update
        # — Static.renderable isn't reliably readable across Textual versions).
        badge = screen.query_one("#nucleus-mode-badge", Static)
        captured: list[str] = []
        real = badge.update

        def recording(content="", **kwargs):
            captured.append(str(content))
            return real(content, **kwargs)

        badge.update = recording  # type: ignore[assignment]
        screen._render_mode_badge()
        assert any("Security floor" in c for c in captured), captured

        # DEV stage (default): the Build button is hidden.
        build_btn = screen.query_one("#btn-build-pkg", Button)
        assert build_btn.display is False

        # Switch the workflow stage to PROD → Build button revealed.
        stage = screen.query_one("#select-nucleus-stage", Select)
        stage.value = "prod"
        for _ in range(4):
            await pilot.pause()
        assert build_btn.display is True
        assert getattr(screen, "_nucleus_stage", "") == "prod"

        # Back to DEV → hidden again.
        stage.value = "dev"
        for _ in range(4):
            await pilot.pause()
        assert build_btn.display is False


@pytest.mark.asyncio
async def test_nucleus_caps_tables_list_allowed_not_just_installed(monkeypatch):
    """26.6.26 finding #4 — the Skills/MCP browse tables list the role's
    ALLOWED capabilities (not the empty allowed∩installed intersection), so
    they're informative even when nothing is installed on the deploy."""
    from textual.widgets import DataTable

    monkeypatch.setenv("ACC_ROLES_ROOT", "roles")
    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        # assistant grants several skills; select it + let caps repaint.
        screen.query_one("#select-role", Select).value = "assistant"
        for _ in range(4):
            await pilot.pause()
        skills_table = screen.query_one("#caps-skills-table", DataTable)
        # The table has the browse columns + at least one allowed-skill row
        # (it is NOT the single "—" empty-intersection placeholder).
        assert len(skills_table.columns) == 4
        assert skills_table.row_count >= 1


def test_apply_persists_token_budget_to_role_yaml(tmp_path, monkeypatch):
    """26.6.26 — Nucleus Apply must WRITE the edited token_budget to
    roles/<name>/role.yaml (tier-0) so it's durable + re-read by the harness,
    not just a NATS ROLE_UPDATE the tier-0 file shadows on reload."""
    import shutil
    from acc.role_loader import RoleLoader

    roles = tmp_path / "roles"
    roles.mkdir()
    shutil.copytree("roles/_base", roles / "_base")
    shutil.copytree("roles/observer", roles / "observer")
    monkeypatch.setenv("ACC_ROLES_ROOT", str(roles))

    rd = RoleLoader(str(roles), "observer").load()
    merged = rd.model_dump()
    merged["category_b_overrides"] = dict(merged.get("category_b_overrides") or {})
    merged["category_b_overrides"]["token_budget"] = 99999

    screen = InfuseScreen()
    assert screen._persist_role_yaml("observer", merged) is True

    reloaded = RoleLoader(str(roles), "observer").load()
    assert (reloaded.category_b_overrides or {}).get("token_budget") == 99999
