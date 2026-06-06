"""Tests for the Catalogs admin TUI screen (Stage 2.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.tui.screens.catalogs import CatalogsScreen


def test_screen_constructor_accepts_workspace():
    ws = Path("/tmp/some-workspace")
    scr = CatalogsScreen(workspace=ws)
    assert scr._workspace == ws


def test_screen_default_workspace_none():
    scr = CatalogsScreen()
    assert scr._workspace is None


def test_screen_caches_catalogs():
    scr = CatalogsScreen()
    assert scr._catalogs == []


def test_screen_has_required_actions():
    """Action bindings cover the operator's primary verbs."""
    assert CatalogsScreen.BINDINGS
    expected_keys = {"n", "d", "r", "+", "-"}
    actual_keys = {b.key for b in CatalogsScreen.BINDINGS}
    assert expected_keys <= actual_keys


def test_screen_imports_clean():
    """Smoke check: the module imports + class defines all expected methods."""
    for method in (
        "compose", "on_mount", "refresh_rows",
        "action_delete_highlighted", "action_raise_priority",
        "action_lower_priority", "_submit_form", "_read_form",
        "_clear_form",
    ):
        assert hasattr(CatalogsScreen, method), f"missing {method}"
