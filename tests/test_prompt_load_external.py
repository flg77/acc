"""Proposal 033 WS-B — Prompt screen external load (Diagnostics "Send").

The Diagnostics golden-prompt Send routes a prompt to the Prompt screen
via a PromptLoadMessage; the App calls ``PromptScreen.load_external``.
These tests pin that the Prompt pane populates its target-role / agent /
mode / textarea from an external load, and that an unknown (packaged)
role is added to the dropdown so the value can be set without error.
"""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Input, Select, TextArea

from acc.tui.screens.prompt import PromptScreen


class _Harness(App):
    def on_mount(self) -> None:
        self.push_screen(PromptScreen())


@pytest.mark.asyncio
async def test_load_external_populates_without_sending():
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.load_external(
            prompt_text="hello world",
            target_role="coding_agent",
            target_agent_id="coding_agent-abc",
            operating_mode="PLAN",
            auto_send=False,
        )
        await pilot.pause()
        assert (
            screen.query_one("#prompt-textarea", TextArea).text == "hello world"
        )
        assert (
            str(screen.query_one("#select-target-role", Select).value)
            == "coding_agent"
        )
        assert (
            screen.query_one("#input-target-agent-id", Input).value
            == "coding_agent-abc"
        )
        assert screen._operating_mode == "PLAN"


@pytest.mark.asyncio
async def test_load_external_adds_unknown_role_as_option():
    """A packaged role not in the built-in _TARGET_ROLES list must still
    be selectable (set_options before set value)."""
    app = _Harness()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.load_external(
            prompt_text="x", target_role="financial_analyst", auto_send=False,
        )
        await pilot.pause()
        assert (
            str(screen.query_one("#select-target-role", Select).value)
            == "financial_analyst"
        )
