"""Integration tests for the proposal 010 wire-up.

These tests stitch together the building blocks that landed in
PR-1 through PR-5 and verify the end-to-end flow:

* RoleCRDProjector + ConflictDetector — every CRD-driven write
  records a body hash; subsequent file-event classification follows
  echo / applied / conflict semantics.
* RoleSyncListener — receives serialised conflict events identical
  to what the ConflictDetector emits and renders the expected badge.

The Textual + NATS wiring in ``acc/tui/app.py`` is exercised by
manual smoke (no test harness today); these tests cover the
non-Textual half.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from acc.role_crd_loader import RoleCRDProjector
from acc.role_sync_conflict import ConflictDetector
from acc.tui.role_sync_listener import RoleSyncListener


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeCRDClient:
    """Minimal CRDClient impl for the wire-up tests."""

    def __init__(self, collectives: list[dict] | None = None) -> None:
        self.collectives = collectives or []

    def list_collectives(self, namespace: str) -> list[dict]:
        return list(self.collectives)


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        self.calls.append((subject, json.loads(payload.decode("utf-8"))))


# ---------------------------------------------------------------------------
# Projector → Detector wire-up
# ---------------------------------------------------------------------------


class TestProjectorDetectorWireUp:
    def test_projector_records_write_to_detector(self, tmp_path: Path):
        detector = ConflictDetector(conflict_window_s=2.0)
        proj = RoleCRDProjector(
            tmp_path, "default", FakeCRDClient(),
            conflict_detector=detector,
        )
        # Project a role — the projector should hand the body to the
        # detector via record_our_write.
        proj.project_one("coding_agent", {"purpose": "Generate code"})
        # Read the body that was written so we can check it matches
        # what the detector recorded.
        body = (tmp_path / "coding_agent" / "role.yaml").read_text(encoding="utf-8")
        # Strip the sentinel header to get just the YAML body that
        # the detector recorded.
        from acc.role_crd_loader import _strip_sentinel
        stripped = _strip_sentinel(body)
        # Within the conflict window, the recorded write should
        # classify the same content as 'echo'.
        result = detector.classify_file_change("coding_agent", stripped)
        assert result == "echo"

    def test_detector_optional_projector_works_without_it(self, tmp_path: Path):
        """Backwards compatibility: projector still works with
        conflict_detector=None (PR-3 default)."""
        proj = RoleCRDProjector(tmp_path, "default", FakeCRDClient())
        assert proj.project_one("x", {"purpose": "p"}) is True

    def test_detector_exception_does_not_break_projector(
        self, tmp_path: Path, caplog,
    ):
        class BoomDetector:
            def record_our_write(self, role_id: str, body: str) -> None:
                raise RuntimeError("detector exploded")

        proj = RoleCRDProjector(
            tmp_path, "default", FakeCRDClient(),
            conflict_detector=BoomDetector(),
        )
        # Should still write the file + return True despite the
        # detector raising.
        assert proj.project_one("x", {"purpose": "p"}) is True
        assert (tmp_path / "x" / "role.yaml").exists()


# ---------------------------------------------------------------------------
# Detector → Listener round-trip
# ---------------------------------------------------------------------------


class TestDetectorListenerRoundTrip:
    @pytest.mark.asyncio
    async def test_conflict_event_flows_to_listener_badge(self):
        """A ConflictDetector.publish_conflict produces a payload that
        RoleSyncListener.handle_event consumes correctly — i.e. the
        wire format is the same on both sides."""
        publisher = RecordingPublisher()
        detector = ConflictDetector(
            publisher=publisher,
            events_subject="acc.role.sync",
            conflict_window_s=10.0,
            now=lambda: 100.0,
        )
        # Stage: detector observes a conflict and publishes.
        detector.record_our_write("coding_agent", "role_definition:\n  purpose: X\n")
        # Simulate a concurrent operator edit within the window.
        verdict = detector.classify_file_change(
            "coding_agent",
            "role_definition:\n  purpose: Y\n",
        )
        assert verdict == "conflict"
        await detector.publish_conflict(
            role_id="coding_agent",
            winner_source="crd",
            loser_source="file",
            loser_body="role_definition:\n  purpose: Y\n",
        )

        # Now feed the captured publisher call into a fresh listener,
        # exactly as the TUI's NATS callback would.
        subject, body = publisher.calls[0]
        listener = RoleSyncListener(badge_window_s=300.0)
        listener.handle_event(subject, json.dumps(body).encode("utf-8"))

        # Badge should be the fresh-conflict variant.
        badge = listener.render_badge("coding_agent")
        assert "[bold red]" in badge
        assert "Sync conflict" in badge
        assert "winner=[bold]crd[/bold]" in badge

    @pytest.mark.asyncio
    async def test_applied_event_flows_to_listener_dim_badge(self):
        publisher = RecordingPublisher()
        detector = ConflictDetector(publisher=publisher)
        await detector.publish_applied("alpha", "file")

        subject, body = publisher.calls[0]
        listener = RoleSyncListener()
        listener.handle_event(subject, json.dumps(body).encode("utf-8"))

        badge = listener.render_badge("alpha")
        assert "[dim]" in badge
        assert "applied" in badge
        assert "file" in badge
        assert "[bold red]" not in badge
