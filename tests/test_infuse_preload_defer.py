"""Regression test for the lighthouse v0.3.20 'i' infusion bug.

Symptom on lighthouse:
    Infusion preload failed for coding_agent:
    No nodes match '#input-version' on InfuseScreen()

Cause:
    `app.on_role_preload_message` was calling
    ``infuse.preload_from_role(...)`` BEFORE ``switch_screen("nucleus")``.
    On the very first 'i' press of a session, InfuseScreen has been
    constructed but its ``compose`` hasn't run, so ``query_one("#input-
    version")`` raises ``NoMatches``.

Fix (v0.3.21):
    1. App switches first, then preloads.
    2. ``preload_from_role`` is mount-safe — when widgets don't exist
       yet it stashes the role name in ``_pending_preload`` and lets
       ``on_mount`` replay the call once the tree is real.

This test asserts both halves: calling ``preload_from_role`` on a
freshly-constructed InfuseScreen (no compose yet) must NOT raise, and
the role name must end up in ``_pending_preload``.
"""

from __future__ import annotations

from acc.tui.screens.infuse import InfuseScreen


def test_preload_before_mount_defers_instead_of_raising():
    """Calling preload_from_role on an unmounted InfuseScreen must
    defer the work instead of crashing on NoMatches."""
    screen = InfuseScreen()
    # No compose, no on_mount — widget tree doesn't exist yet.
    screen.preload_from_role("coding_agent")
    assert screen._pending_preload == "coding_agent", (
        "Unmounted preload must be stashed on _pending_preload for "
        "on_mount to replay; got %r" % screen._pending_preload
    )


def test_pending_preload_default_is_empty_string():
    """Fresh InfuseScreen starts with no pending preload."""
    screen = InfuseScreen()
    assert screen._pending_preload == ""
