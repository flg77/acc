"""Unit tests for acc.spiffe_offline (proposal 012 PR-3)."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from acc.spiffe_offline import (
    ACTION_DEGRADE,
    ACTION_ROTATE,
    ACTION_SHUTDOWN,
    STATE_FRESH,
    OfflineBundleMonitor,
)


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def publish(self, subject: str, payload: bytes) -> None:
        self.calls.append((subject, json.loads(payload.decode("utf-8"))))


def _write_bundle(tmp_path: Path, age_s: float = 0.0) -> Path:
    """Create a bundle file whose mtime is *age_s* seconds in the past."""
    path = tmp_path / "jwt_bundle.json"
    path.write_text('{"keys": []}', encoding="utf-8")
    if age_s:
        old = time.time() - age_s
        os.utime(path, (old, old))
    return path


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_invalid_action_rejected(self, tmp_path: Path):
        with pytest.raises(ValueError, match="offline_action"):
            OfflineBundleMonitor(tmp_path / "b.json", 72.0, "implode")

    def test_valid_actions_accepted(self, tmp_path: Path):
        for action in (ACTION_ROTATE, ACTION_DEGRADE, ACTION_SHUTDOWN):
            OfflineBundleMonitor(tmp_path / "b.json", 72.0, action)


# ---------------------------------------------------------------------------
# bundle_age_s / check
# ---------------------------------------------------------------------------


class TestFreshness:
    def test_fresh_bundle_is_fresh(self, tmp_path: Path):
        _write_bundle(tmp_path, age_s=0.0)
        mon = OfflineBundleMonitor(tmp_path / "jwt_bundle.json", 72.0, ACTION_DEGRADE)
        assert mon.check() == STATE_FRESH
        assert mon.fresh_count == 1
        assert mon.stale_count == 0

    def test_stale_bundle_returns_action(self, tmp_path: Path):
        # 100h old, max 72h → stale.
        _write_bundle(tmp_path, age_s=100 * 3600)
        mon = OfflineBundleMonitor(tmp_path / "jwt_bundle.json", 72.0, ACTION_DEGRADE)
        assert mon.check() == ACTION_DEGRADE
        assert mon.stale_count == 1

    def test_stale_returns_configured_action(self, tmp_path: Path):
        _write_bundle(tmp_path, age_s=100 * 3600)
        for action in (ACTION_ROTATE, ACTION_DEGRADE, ACTION_SHUTDOWN):
            mon = OfflineBundleMonitor(
                tmp_path / "jwt_bundle.json", 72.0, action)
            assert mon.check() == action

    def test_missing_bundle_is_stale(self, tmp_path: Path):
        mon = OfflineBundleMonitor(
            tmp_path / "absent.json", 72.0, ACTION_SHUTDOWN)
        assert mon.bundle_age_s() is None
        assert mon.check() == ACTION_SHUTDOWN

    def test_age_just_under_limit_is_fresh(self, tmp_path: Path):
        _write_bundle(tmp_path, age_s=71 * 3600)
        mon = OfflineBundleMonitor(tmp_path / "jwt_bundle.json", 72.0, ACTION_DEGRADE)
        assert mon.check() == STATE_FRESH

    def test_bundle_age_is_positive(self, tmp_path: Path):
        _write_bundle(tmp_path, age_s=3600)
        mon = OfflineBundleMonitor(tmp_path / "jwt_bundle.json", 72.0, ACTION_DEGRADE)
        age = mon.bundle_age_s()
        assert age is not None and 3500 < age < 3700


# ---------------------------------------------------------------------------
# Event publication
# ---------------------------------------------------------------------------


class TestPublication:
    @pytest.mark.asyncio
    async def test_publish_offline_emits_event(self, tmp_path: Path):
        pub = RecordingPublisher()
        mon = OfflineBundleMonitor(
            tmp_path / "b.json", 72.0, ACTION_DEGRADE,
            publisher=pub, events_subject="acc.spiffe",
        )
        await mon.publish_offline(ACTION_DEGRADE, 999999.0)
        assert len(pub.calls) == 1
        subject, body = pub.calls[0]
        assert subject == "acc.spiffe.offline"
        assert body["action"] == ACTION_DEGRADE
        assert body["bundle_age_s"] == 999999.0

    @pytest.mark.asyncio
    async def test_no_publisher_is_noop(self, tmp_path: Path):
        mon = OfflineBundleMonitor(tmp_path / "b.json", 72.0, ACTION_DEGRADE)
        await mon.publish_offline(ACTION_DEGRADE, 1.0)  # must not raise

    @pytest.mark.asyncio
    async def test_publisher_exception_swallowed(self, tmp_path: Path):
        class Boom:
            async def publish(self, subject: str, payload: bytes) -> None:
                raise RuntimeError("nats down")
        mon = OfflineBundleMonitor(
            tmp_path / "b.json", 72.0, ACTION_DEGRADE, publisher=Boom())
        await mon.publish_offline(ACTION_DEGRADE, 1.0)  # must not raise


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------


class TestPollLoop:
    @pytest.mark.asyncio
    async def test_handler_invoked_on_stale(self, tmp_path: Path):
        _write_bundle(tmp_path, age_s=100 * 3600)  # stale
        pub = RecordingPublisher()
        mon = OfflineBundleMonitor(
            tmp_path / "jwt_bundle.json", 72.0, ACTION_DEGRADE, publisher=pub)

        seen: list[str] = []

        async def handler(action: str) -> None:
            seen.append(action)

        await mon.start(poll_interval_s=0.05, handler=handler)
        await asyncio.sleep(0.15)
        await mon.stop()

        assert ACTION_DEGRADE in seen
        # The stale event was also published.
        assert any(s == "acc.spiffe.offline" for s, _ in pub.calls)

    @pytest.mark.asyncio
    async def test_handler_not_invoked_when_fresh(self, tmp_path: Path):
        _write_bundle(tmp_path, age_s=0.0)  # fresh
        mon = OfflineBundleMonitor(
            tmp_path / "jwt_bundle.json", 72.0, ACTION_SHUTDOWN)

        seen: list[str] = []

        async def handler(action: str) -> None:
            seen.append(action)

        await mon.start(poll_interval_s=0.05, handler=handler)
        await asyncio.sleep(0.15)
        await mon.stop()

        assert seen == []

    @pytest.mark.asyncio
    async def test_handler_exception_does_not_break_loop(self, tmp_path: Path):
        _write_bundle(tmp_path, age_s=100 * 3600)
        mon = OfflineBundleMonitor(
            tmp_path / "jwt_bundle.json", 72.0, ACTION_DEGRADE)

        calls = {"n": 0}

        async def bad_handler(action: str) -> None:
            calls["n"] += 1
            raise RuntimeError("handler boom")

        await mon.start(poll_interval_s=0.05, handler=bad_handler)
        await asyncio.sleep(0.18)
        await mon.stop()
        # The loop kept polling despite the handler raising every time.
        assert calls["n"] >= 2

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self, tmp_path: Path):
        mon = OfflineBundleMonitor(tmp_path / "b.json", 72.0, ACTION_DEGRADE)
        await mon.stop()  # must not raise

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self, tmp_path: Path):
        _write_bundle(tmp_path, age_s=0.0)
        mon = OfflineBundleMonitor(
            tmp_path / "jwt_bundle.json", 72.0, ACTION_DEGRADE)

        async def handler(action: str) -> None:
            pass

        await mon.start(poll_interval_s=10.0, handler=handler)
        await mon.start(poll_interval_s=10.0, handler=handler)  # no-op
        await mon.stop()
