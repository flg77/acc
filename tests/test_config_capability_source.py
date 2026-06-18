"""033 WS-D — Skills/MCP provenance (core vs pack) on the Config tabs.

The 2026-06-16 TUI review asked for trust columns on the Skills/MCP
tabs.  Full signer/signature/install-time provenance needs install-
pipeline capture (a documented follow-up), but the most-useful trust
distinction — built-in baseline vs package-added — is derivable today
from CORE_BASELINE_SKILLS / CORE_BASELINE_MCPS.  These tests pin that
``Source`` cell helper.
"""
from __future__ import annotations

from acc.pkg.manifest import CORE_BASELINE_MCPS, CORE_BASELINE_SKILLS
from acc.tui.screens.configuration import _capability_source


def test_baseline_skill_is_core():
    assert "core" in _capability_source("fs_read", CORE_BASELINE_SKILLS)


def test_non_baseline_skill_is_pack():
    assert "pack" in _capability_source("acme_widget", CORE_BASELINE_SKILLS)


def test_baseline_mcp_is_core():
    assert "core" in _capability_source("arxiv", CORE_BASELINE_MCPS)


def test_non_baseline_mcp_is_pack():
    # A skill name is not an MCP baseline member → pack.
    assert "pack" in _capability_source("github", CORE_BASELINE_MCPS)
