"""`20261030-external-work-board` -- external coding agents on the work board.

CS-03: an external work item is stored beside the oversight queue, rides the
arbiter's HEARTBEAT, updates by EXTERNAL_WORK signal, and is projected onto
the board.  CS-10: every item says who did it (executor), and an external
item carries what its agent reported (cost, tokens).
"""

from __future__ import annotations

import asyncio
import types

import pytest

from acc.external_work import (
    DONE,
    RUNNING,
    STALE_AFTER_S,
    ExternalWorkItem,
    ExternalWorkStore,
    new_item_id,
)
from acc.work_board import (
    FAILED,
    KIND_EXTERNAL,
    QUEUED,
    SOURCE_ACC,
    SOURCE_EXTERNAL,
    filter_snapshot,
    project_board,
)
from tests.test_058_k1_ask_once_phase0 import _FakeRedis


def _row(**over) -> dict:
    item = ExternalWorkItem(item_id="ext-1", title="fix the login redirect", status=RUNNING,
                            executor="codex", repo="tui-designer", updated_ts=1000.0)
    item.add_progress("found the cause", now=1000.0)
    row = item.summary()
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# the item and the store
# ---------------------------------------------------------------------------


def test_progress_is_bounded_and_the_summary_carries_the_last_line():
    item = ExternalWorkItem(item_id=new_item_id(), title="t")
    for i in range(30):
        item.add_progress(f"step {i}", now=float(i))
    assert len(item.progress) == 20 and item.progress[0] == "step 10"
    row = item.summary()
    assert "progress" not in row and row["last_progress"] == "step 29"
    assert row["updated_ts"] == 29.0
    assert item.item_id.startswith("ext-")


def test_an_empty_progress_line_only_touches_the_clock():
    item = ExternalWorkItem(item_id="ext-1", title="t")
    item.add_progress("   ", now=5.0)
    assert item.progress == [] and item.updated_ts == 5.0


@pytest.mark.asyncio
async def test_store_round_trips_through_redis_newest_first():
    redis = _FakeRedis()
    store = ExternalWorkStore(redis, "sol-01")
    await store.save(ExternalWorkItem(item_id="ext-a", title="a", updated_ts=1.0))
    await store.save(ExternalWorkItem(item_id="ext-b", title="b", updated_ts=2.0, executor="cursor"))
    assert "acc:sol-01:ext_work:ext-a" in redis.kv
    assert redis.sets["acc:sol-01:ext_work"] == {"ext-a", "ext-b"}
    got = await store.get("ext-b")
    assert got is not None and got.executor == "cursor"
    assert [it.item_id for it in await store.all()] == ["ext-b", "ext-a"]
    assert [it.item_id for it in await store.all(limit=1)] == ["ext-b"]


@pytest.mark.asyncio
async def test_store_without_redis_keeps_items_in_process():
    store = ExternalWorkStore(None)
    await store.save(ExternalWorkItem(item_id="ext-a", title="a"))
    assert (await store.get("ext-a")).title == "a"
    assert await store.get("missing") is None


def test_store_for_queue_is_shared_per_queue():
    queue = types.SimpleNamespace(_redis=None, _cid="sol-02")
    first = ExternalWorkStore.for_queue(queue)
    assert ExternalWorkStore.for_queue(queue) is first and first._cid == "sol-02"


# ---------------------------------------------------------------------------
# the heartbeat and the observer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_arbiter_heartbeat_carries_external_work_summaries():
    from acc.agent import Agent

    queue = types.SimpleNamespace(_redis=_FakeRedis(), _cid="sol-01")
    await ExternalWorkStore.for_queue(queue).save(
        ExternalWorkItem(item_id="ext-a", title="a", executor="claude-vertex", updated_ts=3.0))
    agent = types.SimpleNamespace(_oversight_queue=queue)
    rows = await Agent._external_work_summaries(agent)
    assert [r["item_id"] for r in rows] == ["ext-a"] and rows[0]["executor"] == "claude-vertex"

    assert await Agent._external_work_summaries(types.SimpleNamespace(_oversight_queue=None)) == []


def test_the_observer_takes_external_work_from_the_arbiter_and_the_signal():
    from acc.tui.client import NATSObserver

    obs = NATSObserver("nats://localhost:4222", "sol-01", asyncio.Queue())
    obs._route_heartbeat("coding-1", {"role": "coding_agent", "external_work": [_row()]})
    assert obs._snapshot.external_work == {}

    obs._route_heartbeat("arbiter-1", {"role": "arbiter", "external_work": [_row(), {"title": "no id"}]})
    assert list(obs._snapshot.external_work) == ["ext-1"]

    obs._route_external_work("", {"signal_type": "EXTERNAL_WORK", "item": _row(status=DONE)})
    assert obs._snapshot.external_work["ext-1"]["status"] == DONE
    obs._route_external_work("", {"signal_type": "EXTERNAL_WORK", "item": "not a dict"})
    assert len(obs._snapshot.external_work) == 1


def test_external_work_signal_has_a_mode_and_a_subject():
    from acc.signals import SIG_EXTERNAL_WORK, SIGNAL_MODES, subject_external_work

    assert SIG_EXTERNAL_WORK in SIGNAL_MODES
    assert subject_external_work("sol-01") == "acc.sol-01.external.work"


# ---------------------------------------------------------------------------
# the projection
# ---------------------------------------------------------------------------


def test_an_external_item_is_projected_with_its_executor():
    [item] = project_board(external_work={"ext-1": _row(cost_usd=0.42, tokens=1200, pr="#12")},
                           now=1000.0)
    assert item.id == "ext:ext-1" and item.kind == KIND_EXTERNAL and item.status == RUNNING
    assert item.executor == "codex" and item.source == SOURCE_EXTERNAL and item.repo == "tui-designer"
    assert item.output_head == "found the cause"
    assert (item.cost_usd, item.tokens) == (0.42, 1200)
    assert item.outcome == "#12"
    # ACC cannot cancel, retry or reassign another agent's work
    assert not (item.can_cancel or item.can_retry or item.can_reassign)


def test_a_list_of_rows_works_too_and_rows_without_an_id_are_skipped():
    items = project_board(external_work=[_row(), {"title": "x"}], now=1000.0)
    assert [it.id for it in items] == ["ext:ext-1"]


def test_a_running_item_without_progress_for_a_day_is_stale():
    [fresh] = project_board(external_work=[_row()], now=1000.0 + STALE_AFTER_S - 1)
    [stale] = project_board(external_work=[_row()], now=1000.0 + STALE_AFTER_S + 1)
    assert fresh.status_detail == "" and stale.status_detail == "stale"
    assert stale.status == RUNNING                     # flagged, never moved


def test_a_handed_over_item_says_by_whom_and_an_unknown_status_queues():
    [item] = project_board(external_work=[_row(status=QUEUED, handoff_from="cursor")], now=1000.0)
    assert item.status_detail == "handed over by cursor"
    [odd] = project_board(external_work=[_row(status="parked")], now=1000.0)
    assert odd.status == QUEUED


def test_outcome_and_pr_are_joined():
    [item] = project_board(external_work=[_row(status=FAILED, outcome="tests red\nmore", pr="#9")],
                           now=1000.0)
    assert item.outcome == "tests red (#9)"


def test_the_collectives_own_items_name_their_agent():
    log = [{"signal_type": "TASK_ASSIGN", "task_id": "t-1", "target_role": "analyst", "ts": 5.0},
           {"signal_type": "TASK_ASSIGN", "task_id": "t-2", "target_role": "", "ts": 6.0}]
    items = {it.task_id: it for it in project_board(signal_flow_log=log)}
    assert items["t-1"].executor == "acc:analyst" and items["t-1"].source == SOURCE_ACC
    assert items["t-2"].executor == "acc"


def test_a_viewer_sees_only_the_external_items_they_asked_for():
    snap = {"external_work": {"ext-1": _row(requester="slack:U1"), "ext-2": _row(item_id="ext-2")}}
    assert set(filter_snapshot(snap, "slack:U1@C9", "member")["external_work"]) == {"ext-1"}
    assert filter_snapshot(snap, "op", "operator") is snap
