"""Unit tests for the ACC scheduler (proposal 005).

Pure-fn coverage of the cron parser + Schedule + ScheduleStore
round-trip + due-selection logic.  The live ``run-once`` CLI is
tested separately via a stub NATS client.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

from acc.scheduler import (
    Schedule,
    ScheduleStore,
    due_schedules,
    next_fire_time,
)


# ---------------------------------------------------------------------------
# Cron parser
# ---------------------------------------------------------------------------


def _ts(y, mo, d, h, mi) -> float:
    return datetime(y, mo, d, h, mi).timestamp()


def test_cron_every_minute():
    """``* * * * *`` returns the next minute boundary."""
    base = _ts(2026, 5, 14, 12, 0)  # exactly 12:00
    nxt = next_fire_time("* * * * *", base)
    assert nxt == _ts(2026, 5, 14, 12, 1)


def test_cron_every_n_minutes():
    """``*/5 * * * *`` returns the next slot at minute % 5 == 0."""
    base = _ts(2026, 5, 14, 12, 2)
    nxt = next_fire_time("*/5 * * * *", base)
    assert nxt == _ts(2026, 5, 14, 12, 5)


def test_cron_exact_minute():
    """``30 * * * *`` fires at minute 30 of every hour."""
    base = _ts(2026, 5, 14, 12, 0)
    nxt = next_fire_time("30 * * * *", base)
    assert nxt == _ts(2026, 5, 14, 12, 30)


def test_cron_specific_hour():
    """``0 2 * * *`` fires at 02:00 daily."""
    base = _ts(2026, 5, 14, 12, 0)
    nxt = next_fire_time("0 2 * * *", base)
    # Next 02:00 is the following day.
    assert nxt == _ts(2026, 5, 15, 2, 0)


def test_cron_skips_when_base_after_today_slot():
    """If ``base`` is past today's slot, returns tomorrow's."""
    base = _ts(2026, 5, 14, 3, 0)
    nxt = next_fire_time("0 2 * * *", base)
    assert nxt == _ts(2026, 5, 15, 2, 0)


def test_cron_minute_and_hour():
    base = _ts(2026, 5, 14, 12, 0)
    nxt = next_fire_time("15 14 * * *", base)
    assert nxt == _ts(2026, 5, 14, 14, 15)


def test_cron_rejects_dom_month_dow():
    """Unsupported fields raise ValueError."""
    with pytest.raises(ValueError):
        next_fire_time("0 0 1 * *", time.time())
    with pytest.raises(ValueError):
        next_fire_time("0 0 * 5 *", time.time())
    with pytest.raises(ValueError):
        next_fire_time("0 0 * * 1", time.time())


def test_cron_rejects_garbage():
    """Malformed expressions raise ValueError."""
    with pytest.raises(ValueError):
        next_fire_time("bogus", time.time())
    with pytest.raises(ValueError):
        next_fire_time("70 * * * *", time.time())  # minute > 59
    with pytest.raises(ValueError):
        next_fire_time("0 30 * * *", time.time())  # hour > 23


# ---------------------------------------------------------------------------
# Schedule + ScheduleStore round-trip
# ---------------------------------------------------------------------------


def test_schedule_dict_roundtrip():
    sched = Schedule(
        name="nightly",
        role="research_planner",
        task="Summarise",
        cron="0 2 * * *",
        collective_id="sol-01",
        target_agent_id="",
        enabled=True,
        last_fired_ts=0.0,
        created_ts=1234.5,
    )
    out = Schedule.from_dict(sched.to_dict())
    assert out == sched


def test_schedule_from_dict_ignores_extra_keys():
    """Extra keys in YAML (e.g. operator notes) don't break load."""
    out = Schedule.from_dict({
        "name": "x",
        "role": "y",
        "task": "z",
        "cron": "* * * * *",
        "operator_note": "this should be ignored",
    })
    assert out.name == "x"


def test_store_save_and_get(tmp_path):
    store = ScheduleStore(root=tmp_path)
    sched = Schedule(
        name="alpha",
        role="r",
        task="t",
        cron="* * * * *",
    )
    store.save(sched)
    loaded = store.get("alpha")
    assert loaded is not None
    assert loaded.name == "alpha"


def test_store_list_returns_sorted(tmp_path):
    store = ScheduleStore(root=tmp_path)
    for n in ("zeta", "alpha", "mu"):
        store.save(Schedule(name=n, role="r", task="t", cron="* * * * *"))
    names = [s.name for s in store.list()]
    assert names == ["alpha", "mu", "zeta"]


def test_store_remove_returns_false_if_missing(tmp_path):
    store = ScheduleStore(root=tmp_path)
    assert store.remove("never-existed") is False


def test_store_skips_unreadable_yaml(tmp_path):
    """A malformed YAML file is skipped, not raised."""
    store = ScheduleStore(root=tmp_path)
    (tmp_path / "bad.yaml").write_text(": : :\n", encoding="utf-8")
    store.save(Schedule(name="good", role="r", task="t", cron="* * * * *"))
    # Bad one silently dropped; good one survives.
    names = [s.name for s in store.list()]
    assert "good" in names
    assert "bad" not in names


# ---------------------------------------------------------------------------
# due_schedules
# ---------------------------------------------------------------------------


def test_due_schedules_returns_due_only():
    """A schedule whose cron fires in the past relative to its
    last_fired_ts is due."""
    past = _ts(2026, 5, 14, 11, 0)
    now = _ts(2026, 5, 14, 12, 30)
    sched = Schedule(
        name="x",
        role="r",
        task="t",
        cron="*/5 * * * *",
        last_fired_ts=past,
    )
    assert sched in due_schedules([sched], now)


def test_due_schedules_skips_disabled():
    now = time.time()
    sched = Schedule(
        name="x", role="r", task="t", cron="* * * * *", enabled=False,
    )
    assert due_schedules([sched], now) == []


def test_due_schedules_uses_created_ts_fallback():
    """A never-fired schedule uses created_ts as the base; if
    created_ts is also 0, falls back to now-60 so the first
    run-once tick after add fires."""
    now = time.time()
    sched = Schedule(
        name="brand_new", role="r", task="t", cron="* * * * *",
        created_ts=0.0, last_fired_ts=0.0,
    )
    due = due_schedules([sched], now)
    assert sched in due


def test_due_schedules_skips_bad_cron():
    """Bad cron expression — schedule skipped, others still
    eligible."""
    now = time.time()
    good = Schedule(name="good", role="r", task="t", cron="* * * * *")
    bad = Schedule(name="bad", role="r", task="t", cron="bogus")
    due = due_schedules([good, bad], now)
    assert good in due
    assert bad not in due
