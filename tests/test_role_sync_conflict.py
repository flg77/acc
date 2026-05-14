"""Unit tests for acc.role_sync_conflict (proposal 010 PR-4).

Mirror-mode classifier — distinguishes echoes (our own writes coming
back through the file watcher) from genuine operator edits, and from
conflict scenarios where an external editor races with our CRD-driven
write.

The time source is injected (`now=lambda: t`) so the conflict window
behaviour is deterministic without `time.sleep`.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from acc.role_sync_conflict import ConflictDetector


# ---------------------------------------------------------------------------
# RecordingPublisher — captures NATS publish calls
# ---------------------------------------------------------------------------


class RecordingPublisher:
    """Stand-in for SignalingBackend; captures every publish."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        body = json.loads(payload.decode("utf-8"))
        self.calls.append((subject, body))


# ---------------------------------------------------------------------------
# classify_file_change — three outcomes
# ---------------------------------------------------------------------------


class TestClassify:
    def test_change_without_prior_write_is_applied(self):
        cd = ConflictDetector(now=lambda: 100.0)
        result = cd.classify_file_change("alpha", "role_definition:\n  purpose: x\n")
        assert result == "applied"
        assert cd.applied_count == 1

    def test_matching_write_within_window_is_echo(self):
        t = [100.0]
        cd = ConflictDetector(conflict_window_s=2.0, now=lambda: t[0])
        body = "role_definition:\n  purpose: x\n"
        cd.record_our_write("alpha", body)
        # 0.5 s later, same body comes back through the watcher.
        t[0] = 100.5
        result = cd.classify_file_change("alpha", body)
        assert result == "echo"
        assert cd.echo_count == 1
        assert cd.conflict_count == 0

    def test_differing_body_within_window_is_conflict(self):
        t = [100.0]
        cd = ConflictDetector(conflict_window_s=2.0, now=lambda: t[0])
        cd.record_our_write("alpha", "role_definition:\n  purpose: A\n")
        t[0] = 101.0
        # Operator just edited the file to a different value within
        # the conflict window.
        result = cd.classify_file_change("alpha", "role_definition:\n  purpose: B\n")
        assert result == "conflict"
        assert cd.conflict_count == 1

    def test_change_outside_window_is_applied(self):
        t = [100.0]
        cd = ConflictDetector(conflict_window_s=2.0, now=lambda: t[0])
        cd.record_our_write("alpha", "x")
        # Way past the window — operator editing later, not a conflict.
        t[0] = 200.0
        result = cd.classify_file_change("alpha", "y")
        assert result == "applied"
        assert cd.applied_count == 1
        assert cd.conflict_count == 0

    def test_record_replaced_by_subsequent_write(self):
        """Each record_our_write overwrites the previous one.

        After overwrite, the matching content is "second" → echo.
        A body of "first" within the window is NOT a match anymore,
        so it's classified as conflict (real operator edit racing
        with our most-recent CRD-driven write).
        """
        t = [100.0]
        cd = ConflictDetector(conflict_window_s=2.0, now=lambda: t[0])
        cd.record_our_write("alpha", "first")
        t[0] = 100.5
        cd.record_our_write("alpha", "second")
        t[0] = 100.6
        assert cd.classify_file_change("alpha", "second") == "echo"
        # A separate call must re-record "second" first because the
        # echo path doesn't refresh the record — but we want to test
        # that "first" is classified as conflict when "second" is
        # the recorded write.
        cd.record_our_write("alpha", "second")
        t[0] = 100.7
        assert cd.classify_file_change("alpha", "first") == "conflict"

    def test_per_role_isolation(self):
        """A record for alpha does not affect classification for beta."""
        t = [100.0]
        cd = ConflictDetector(conflict_window_s=10.0, now=lambda: t[0])
        cd.record_our_write("alpha", "x")
        # Beta's first event is classified as applied — no record exists.
        assert cd.classify_file_change("beta", "anything") == "applied"


# ---------------------------------------------------------------------------
# Event publication
# ---------------------------------------------------------------------------


class TestPublication:
    @pytest.mark.asyncio
    async def test_publish_applied_emits_event(self):
        pub = RecordingPublisher()
        cd = ConflictDetector(publisher=pub, events_subject="acc.role.sync")
        await cd.publish_applied("coding_agent", "file")
        assert len(pub.calls) == 1
        subject, body = pub.calls[0]
        assert subject == "acc.role.sync.applied"
        assert body["role_id"] == "coding_agent"
        assert body["source"] == "file"

    @pytest.mark.asyncio
    async def test_publish_conflict_includes_loser_snippet(self):
        pub = RecordingPublisher()
        cd = ConflictDetector(publisher=pub, events_subject="acc.role.sync")
        await cd.publish_conflict(
            "coding_agent",
            winner_source="crd",
            loser_source="file",
            loser_body="role_definition:\n  purpose: overwritten\n",
        )
        assert len(pub.calls) == 1
        subject, body = pub.calls[0]
        assert subject == "acc.role.sync.conflict"
        assert body["role_id"] == "coding_agent"
        assert body["winner_source"] == "crd"
        assert body["loser_source"] == "file"
        assert "overwritten" in body["loser_snippet"]
        assert "ts" in body  # operators / dashboards need a timestamp

    @pytest.mark.asyncio
    async def test_no_publisher_means_no_calls(self):
        """When publisher is None, publish_* is a silent no-op."""
        cd = ConflictDetector(publisher=None)
        # Should not raise.
        await cd.publish_applied("x", "file")
        await cd.publish_conflict("x", "crd", "file", "body")

    @pytest.mark.asyncio
    async def test_publisher_exception_swallowed(self):
        """A NATS hiccup must not break the projector's hot loop."""
        class BoomPublisher:
            async def publish(self, subject: str, payload: bytes) -> None:
                raise RuntimeError("nats unreachable")
        cd = ConflictDetector(publisher=BoomPublisher())
        await cd.publish_applied("x", "file")  # should not raise

    @pytest.mark.asyncio
    async def test_subject_prefix_stripped_correctly(self):
        """Trailing dots in events_subject are normalised."""
        pub = RecordingPublisher()
        cd = ConflictDetector(publisher=pub, events_subject="acc.role.sync.")
        await cd.publish_applied("x", "file")
        subject, _ = pub.calls[0]
        assert subject == "acc.role.sync.applied"


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


class TestCounters:
    def test_counters_increment_per_outcome(self):
        t = [100.0]
        cd = ConflictDetector(conflict_window_s=2.0, now=lambda: t[0])

        # 1 applied (no prior record)
        cd.classify_file_change("a", "x")
        # 1 echo
        cd.record_our_write("a", "y")
        t[0] = 100.5
        cd.classify_file_change("a", "y")
        # 1 conflict
        cd.record_our_write("a", "y2")
        t[0] = 101.0
        cd.classify_file_change("a", "y3")
        # 1 applied (outside window)
        cd.record_our_write("b", "z")
        t[0] = 200.0
        cd.classify_file_change("b", "z-edit")

        assert cd.applied_count == 2
        assert cd.echo_count == 1
        assert cd.conflict_count == 1
