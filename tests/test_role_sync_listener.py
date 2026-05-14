"""Unit tests for acc.tui.role_sync_listener (proposal 010 PR-5)."""
from __future__ import annotations

import json
import time

import pytest

from acc.tui.role_sync_listener import RoleSyncListener, RoleSyncState


def _conflict_payload(role_id: str, winner: str = "crd", loser: str = "file",
                      snippet: str = "old body", ts: float | None = None) -> bytes:
    body = {
        "role_id": role_id,
        "winner_source": winner,
        "loser_source": loser,
        "loser_snippet": snippet,
        "ts": ts if ts is not None else time.time(),
    }
    return json.dumps(body).encode("utf-8")


def _applied_payload(role_id: str, source: str = "file") -> bytes:
    return json.dumps({"role_id": role_id, "source": source}).encode("utf-8")


# ---------------------------------------------------------------------------
# State ingestion
# ---------------------------------------------------------------------------


class TestStateIngestion:
    def test_empty_listener_has_no_state(self):
        listener = RoleSyncListener()
        assert listener.state("anything") is None
        assert listener.all_roles() == []
        assert listener.has_fresh_conflict("anything") is False

    def test_conflict_event_records_state(self):
        listener = RoleSyncListener()
        listener.handle_event(
            "acc.role.sync.conflict",
            _conflict_payload("coding_agent"),
        )
        st = listener.state("coding_agent")
        assert st is not None
        assert st.role_id == "coding_agent"
        assert st.last_conflict_ts is not None
        assert st.last_winner_source == "crd"
        assert st.last_loser_source == "file"
        assert "old body" in (st.last_loser_snippet or "")
        assert st.conflict_count == 1

    def test_applied_event_records_state(self):
        listener = RoleSyncListener()
        listener.handle_event(
            "acc.role.sync.applied",
            _applied_payload("coding_agent", source="file"),
        )
        st = listener.state("coding_agent")
        assert st is not None
        assert st.last_applied_source == "file"
        assert st.applied_count == 1
        assert st.last_conflict_ts is None

    def test_multiple_events_accumulate_counters(self):
        listener = RoleSyncListener()
        for _ in range(3):
            listener.handle_event(
                "acc.role.sync.applied",
                _applied_payload("alpha"),
            )
        listener.handle_event(
            "acc.role.sync.conflict",
            _conflict_payload("alpha"),
        )
        st = listener.state("alpha")
        assert st.applied_count == 3
        assert st.conflict_count == 1

    def test_per_role_isolation(self):
        listener = RoleSyncListener()
        listener.handle_event(
            "acc.role.sync.conflict",
            _conflict_payload("alpha"),
        )
        listener.handle_event(
            "acc.role.sync.applied",
            _applied_payload("beta"),
        )
        assert listener.state("alpha").conflict_count == 1
        assert listener.state("alpha").applied_count == 0
        assert listener.state("beta").conflict_count == 0
        assert listener.state("beta").applied_count == 1
        assert listener.all_roles() == ["alpha", "beta"]


# ---------------------------------------------------------------------------
# Malformed payload tolerance
# ---------------------------------------------------------------------------


class TestRobustness:
    def test_malformed_json_dropped(self):
        listener = RoleSyncListener()
        listener.handle_event("acc.role.sync.applied", b"not json{{{")
        assert listener.all_roles() == []

    def test_payload_missing_role_id_dropped(self):
        listener = RoleSyncListener()
        listener.handle_event(
            "acc.role.sync.applied",
            json.dumps({"source": "file"}).encode("utf-8"),
        )
        assert listener.all_roles() == []

    def test_unknown_subject_ignored(self):
        listener = RoleSyncListener()
        # Subject that doesn't end in .applied or .conflict.
        listener.handle_event(
            "acc.role.sync.unknown",
            _applied_payload("x"),
        )
        # State is created (role_id present in payload) but no counters
        # advance — this is intentional so unknown event types in the
        # future are simply opaque rather than discarded silently.
        st = listener.state("x")
        assert st is not None
        assert st.applied_count == 0
        assert st.conflict_count == 0


# ---------------------------------------------------------------------------
# has_fresh_conflict + badge_window_s
# ---------------------------------------------------------------------------


class TestFreshness:
    def test_fresh_conflict_within_window(self):
        listener = RoleSyncListener(badge_window_s=60.0)
        listener.handle_event(
            "acc.role.sync.conflict",
            _conflict_payload("x", ts=time.time()),
        )
        assert listener.has_fresh_conflict("x") is True

    def test_stale_conflict_outside_window(self):
        listener = RoleSyncListener(badge_window_s=60.0)
        # ts set to 2 minutes ago
        listener.handle_event(
            "acc.role.sync.conflict",
            _conflict_payload("x", ts=time.time() - 120.0),
        )
        assert listener.has_fresh_conflict("x") is False

    def test_applied_only_is_not_fresh_conflict(self):
        listener = RoleSyncListener()
        listener.handle_event(
            "acc.role.sync.applied",
            _applied_payload("x"),
        )
        assert listener.has_fresh_conflict("x") is False


# ---------------------------------------------------------------------------
# render_badge
# ---------------------------------------------------------------------------


class TestBadge:
    def test_no_state_returns_empty(self):
        listener = RoleSyncListener()
        assert listener.render_badge("missing") == ""

    def test_fresh_conflict_renders_red(self):
        listener = RoleSyncListener()
        listener.handle_event(
            "acc.role.sync.conflict",
            _conflict_payload("x", ts=time.time()),
        )
        badge = listener.render_badge("x")
        assert "[bold red]" in badge
        assert "Sync conflict" in badge
        assert "crd" in badge
        assert "file" in badge

    def test_aged_conflict_renders_dim(self):
        listener = RoleSyncListener(badge_window_s=60.0)
        listener.handle_event(
            "acc.role.sync.conflict",
            _conflict_payload("x", ts=time.time() - 600.0),
        )
        badge = listener.render_badge("x")
        assert "[bold red]" not in badge
        assert "[dim]" in badge
        assert "Last conflict" in badge

    def test_applied_only_renders_dim(self):
        listener = RoleSyncListener()
        listener.handle_event(
            "acc.role.sync.applied",
            _applied_payload("x", source="file"),
        )
        badge = listener.render_badge("x")
        assert "[dim]" in badge
        assert "applied" in badge
        assert "file" in badge
