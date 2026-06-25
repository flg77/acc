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
from textual.widgets import Input, Select

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
        assert tb.startswith("4096"), (
            f"token_budget should be assistant's 4096, got {tb!r}"
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
