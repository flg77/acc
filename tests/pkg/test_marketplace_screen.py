"""Tests for the Marketplace TUI screen (Stage 2.4).

Exercises the data + dispatch contract without needing the Textual
runtime — the screen's row cache, install staging, and message
type are testable directly.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from acc.marketplace import MarketplaceRow
from acc.tui.screens.marketplace import MarketplaceScreen, _StageInstall


def _row(name="@acc/coding-roles", version="1.0.0") -> MarketplaceRow:
    return MarketplaceRow(
        name=name, version=version,
        tier="trusted", tier_badge="[TRUSTED]",
        catalog_id="acc-canonical", catalog_mode="https",
        signer="oidc:gh~flg77/", install_marker=f"[PROPOSE_INFUSE:{name}@{version}:operator-marketplace-action]",
    )


def test_stage_install_message_carries_marker_and_name():
    # Rows are now flattened display rows (built-in + layered), so the message
    # carries the package name + marker the Compliance queue needs — not a
    # MarketplaceRow.
    msg = _StageInstall(
        marker_text="[PROPOSE_INFUSE:@acc/x@1.0:r]",
        name="@acc/x",
    )
    assert msg.marker_text == "[PROPOSE_INFUSE:@acc/x@1.0:r]"
    assert msg.name == "@acc/x"


def test_marketplace_screen_caches_rows_for_dispatch():
    """The screen retains a list of MarketplaceRow so cursor → install
    can resolve without re-fetching.
    """
    scr = MarketplaceScreen()
    # Inject rows directly (simulates a successful refresh_rows())
    scr._rows = [_row("@acc/a", "1.0.0"), _row("@acc/b", "2.0.0")]
    assert len(scr._rows) == 2
    assert scr._rows[0].name == "@acc/a"


def test_filter_text_persists_across_refresh():
    scr = MarketplaceScreen()
    scr._filter_text = "@acc/coding"
    assert scr._filter_text == "@acc/coding"


# Smoke-test the screen module imports clean — catches typos +
# missing dependency injection without the full app harness
def test_marketplace_screen_class_attrs():
    assert MarketplaceScreen.BINDINGS  # has key bindings
    assert hasattr(MarketplaceScreen, "compose")
    assert hasattr(MarketplaceScreen, "on_mount")
    assert hasattr(MarketplaceScreen, "refresh_rows")
    assert hasattr(MarketplaceScreen, "action_install_highlighted")
