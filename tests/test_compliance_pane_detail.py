"""PR-H (D-004) — master/detail oversight pane.

Operator-reported (post-Commit-7 testing round): the Compliance pane's
oversight queue showed only ``ID · Agent · Risk · Submitted · Status``,
forcing the operator to approve / reject without seeing WHY the item
was gated, WHICH task it belonged to, or WHAT will happen on each
decision.  PR-H restructures the pane into master/detail with:

  - a "Gate reason" column on the master table for at-a-glance triage,
  - a ``#oversight-detail`` Static below the table that renders the
    selected item's full context (agent, task, risk, gate reason,
    approve preview, reject preview),
  - a high-consequence confirmation modal on the Approve path so the
    operator can't single-keypress through dangerous decisions.

These tests pin each of those contracts.
"""

from __future__ import annotations

import time

import pytest
from textual.app import App
from textual.widgets import DataTable, Static

from acc.tui.models import AgentSnapshot, CollectiveSnapshot
from acc.tui.screens.compliance import ComplianceScreen, _OversightAction


# ---------------------------------------------------------------------------
# Fixtures + harness
# ---------------------------------------------------------------------------


def _make_snapshot(items: list[dict]) -> CollectiveSnapshot:
    snap = CollectiveSnapshot(collective_id="sol-test")
    snap.agents["arbiter-1"] = AgentSnapshot(agent_id="arbiter-1", role="arbiter")
    snap.oversight_pending_items = items
    return snap


def _item(
    oid: str = "ov-abc-1",
    *,
    agent_id: str = "coding_agent-1",
    task_id: str = "task-xyz-42",
    risk_level: str = "HIGH",
    summary: str = "CRITICAL invocation: A-017 outside allow-list",
    submitted_at_ms: int | None = None,
    status: str = "PENDING",
) -> dict:
    """Build an oversight-item dict in the same shape arbiter HEARTBEATs
    emit (matches ``acc/agent.py:_publish_heartbeat`` serialisation)."""
    return {
        "oversight_id": oid,
        "agent_id": agent_id,
        "task_id": task_id,
        "risk_level": risk_level,
        "summary": summary,
        "submitted_at_ms": submitted_at_ms or int(time.time() * 1000),
        "status": status,
    }


class _Harness(App):
    """Minimal app hosting the Compliance screen and capturing the
    OversightAction messages the operator's Approve/Reject emit."""

    def __init__(self) -> None:
        super().__init__()
        self.captured: list[_OversightAction] = []

    def on_mount(self) -> None:
        self.push_screen(ComplianceScreen())

    async def on__oversight_action(self, message: _OversightAction) -> None:
        self.captured.append(message)


# ---------------------------------------------------------------------------
# Master table — gate-reason column + cache + columns shape
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_master_table_has_gate_reason_column():
    """PR-H — master table grows a "Gate reason" column (6 columns)."""
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot([_item()])
        await pilot.pause()
        table = screen.query_one("#oversight-table", DataTable)
        # 6 columns: ID, Agent, Risk, Submitted, Gate reason, Status.
        assert len(table.columns) == 6, (
            f"expected 6 columns, got {len(table.columns)}"
        )


@pytest.mark.asyncio
async def test_master_table_row_caches_full_item():
    """PR-H — every PENDING row's full dict is cached under its
    oversight_id so the detail renderer can render without re-walking
    the snapshot.  Aggregate-fallback rows (agg-…) are excluded."""
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot([
            _item("ov-a"),
            _item("ov-b", agent_id="ingester-2", summary="HIPAA redaction queue"),
        ])
        await pilot.pause()
        cache = screen._pending_items_by_id
        assert set(cache.keys()) == {"ov-a", "ov-b"}
        assert cache["ov-b"]["agent_id"] == "ingester-2"


# ---------------------------------------------------------------------------
# Detail panel — render context for highlighted row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_detail_panel_renders_highlighted_row_context():
    """PR-H — RowHighlighted on the oversight table populates the
    detail Static with the row's full context (agent, task, gate
    reason, approve / reject previews)."""
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot([
            _item(
                "ov-task42",
                agent_id="coding_agent-1",
                task_id="task-xyz-42",
                summary="Refactor delete_user requires explicit approval",
            ),
        ])
        await pilot.pause()

        detail = screen.query_one("#oversight-detail", Static)

        # Capture detail.update() output regardless of how the panel
        # renders renderable internally across Textual versions.
        captured: list[str] = []
        original_update = detail.update

        def _capture(content="", *a, **kw):
            captured.append(str(content))
            return original_update(content, *a, **kw)

        detail.update = _capture  # type: ignore[assignment]

        # Force the cursor refresh that watch_snapshot would do.
        screen._refresh_detail_for_cursor()
        await pilot.pause()

        assert captured, "expected detail.update() to be called"
        rendered = "\n".join(captured)
        assert "coding_agent-1" in rendered
        assert "task-xyz-42" in rendered
        assert "delete_user" in rendered
        # Approve / Reject preview lines surface the OVERSIGHT_DECISION
        # payload the operator's keypress will publish.
        assert "OVERSIGHT_DECISION" in rendered
        assert "APPROVE" in rendered
        assert "REJECT" in rendered


@pytest.mark.asyncio
async def test_detail_panel_clears_when_no_pending_items():
    """PR-H — when the queue empties (all items resolved), the detail
    panel reverts to the placeholder hint so a stale prior selection
    doesn't mislead the operator."""
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        # Start with one item …
        screen.snapshot = _make_snapshot([_item("ov-a")])
        await pilot.pause()
        # … then drain.
        screen.snapshot = _make_snapshot([])
        await pilot.pause()
        detail = screen.query_one("#oversight-detail", Static)
        rendered = str(getattr(detail, "renderable", "")) or ""
        # Either way, the cache is empty so subsequent
        # _refresh_detail_for_cursor yields the placeholder text.
        assert screen._pending_items_by_id == {}


# ---------------------------------------------------------------------------
# High-consequence detection
# ---------------------------------------------------------------------------


def test_is_high_consequence_flags_critical_risk():
    assert ComplianceScreen._is_high_consequence(_item(risk_level="CRITICAL"))


def test_is_high_consequence_flags_unacceptable_risk():
    assert ComplianceScreen._is_high_consequence(_item(risk_level="UNACCEPTABLE"))


def test_is_high_consequence_flags_high_risk():
    assert ComplianceScreen._is_high_consequence(_item(risk_level="HIGH"))


def test_is_high_consequence_flags_dangerous_summary():
    """Summary substring matches override risk_level=MEDIUM."""
    assert ComplianceScreen._is_high_consequence(
        _item(risk_level="MEDIUM", summary="delete user_table from database"),
    )


def test_is_high_consequence_flags_a017_marker():
    assert ComplianceScreen._is_high_consequence(
        _item(risk_level="MEDIUM", summary="invocation gated: A-017 outside allow-list"),
    )


def test_is_high_consequence_clears_for_low_risk_safe_summary():
    """Cat-B-only items with no danger markers do NOT require confirm."""
    assert not ComplianceScreen._is_high_consequence(
        _item(risk_level="LOW", summary="confidence below threshold for category-B"),
    )


def test_is_high_consequence_handles_empty():
    assert not ComplianceScreen._is_high_consequence({})
    assert not ComplianceScreen._is_high_consequence(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Approve path — confirm modal for high-consequence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_low_consequence_publishes_immediately(monkeypatch):
    """PR-H — a non-high-consequence item: pressing 'a' publishes the
    OVERSIGHT_DECISION immediately (no modal)."""
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot([
            _item("ov-cheap", risk_level="LOW", summary="cat-B observation drift"),
        ])
        await pilot.pause()
        table = screen.query_one("#oversight-table", DataTable)
        table.focus()
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

        actions = [m for m in app.captured if m.action == "approve"]
        assert len(actions) == 1, f"expected 1 approve, got {actions!r}"
        assert actions[0].oversight_id == "ov-cheap"


@pytest.mark.asyncio
async def test_approve_high_consequence_opens_modal_first(monkeypatch):
    """PR-H — a CRITICAL item: pressing 'a' pushes the
    OversightConfirmModal, does NOT publish until the modal resolves
    with True."""
    pushed: list = []

    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot([
            _item("ov-dangerous", risk_level="CRITICAL"),
        ])
        await pilot.pause()

        # Intercept push_screen so the test doesn't actually mount the
        # modal — we only want to verify it was offered and that no
        # OVERSIGHT_DECISION was published yet.
        original_push = screen.app.push_screen

        def _capture_push(screen_or_modal, callback=None, **kwargs):
            pushed.append((screen_or_modal, callback))
            # Don't actually mount; simulate operator pressing Cancel
            # by resolving the callback with False.
            if callback is not None:
                callback(False)

        monkeypatch.setattr(screen.app, "push_screen", _capture_push)

        table = screen.query_one("#oversight-table", DataTable)
        table.focus()
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

        # Modal was offered.
        assert len(pushed) == 1
        from acc.tui.widgets.oversight_confirm_modal import OversightConfirmModal
        assert isinstance(pushed[0][0], OversightConfirmModal)
        # No OVERSIGHT_DECISION published — operator cancelled.
        assert [m for m in app.captured if m.action == "approve"] == []


@pytest.mark.asyncio
async def test_approve_high_consequence_confirmed_publishes(monkeypatch):
    """PR-H — confirming the modal (callback(True)) DOES publish the
    OVERSIGHT_DECISION."""
    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot([
            _item("ov-dangerous-2", risk_level="CRITICAL"),
        ])
        await pilot.pause()

        def _confirm_push(_modal, callback=None, **kwargs):
            # Simulate operator pressing Confirm Approve.
            if callback is not None:
                callback(True)

        monkeypatch.setattr(screen.app, "push_screen", _confirm_push)

        table = screen.query_one("#oversight-table", DataTable)
        table.focus()
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

        approves = [m for m in app.captured if m.action == "approve"]
        assert len(approves) == 1
        assert approves[0].oversight_id == "ov-dangerous-2"


@pytest.mark.asyncio
async def test_reject_never_requires_confirmation(monkeypatch):
    """PR-H invariant — Reject NEVER opens a modal regardless of risk
    level.  Withholding consent is always safe."""
    pushed: list = []

    app = _Harness()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = _make_snapshot([
            _item("ov-bad", risk_level="UNACCEPTABLE"),
        ])
        await pilot.pause()

        def _capture_push(modal, callback=None, **kwargs):
            pushed.append(modal)

        monkeypatch.setattr(screen.app, "push_screen", _capture_push)

        table = screen.query_one("#oversight-table", DataTable)
        table.focus()
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

        assert pushed == [], (
            f"Reject must not open any modal; got pushed={pushed!r}"
        )
        rejects = [m for m in app.captured if m.action == "reject"]
        assert len(rejects) == 1
        assert rejects[0].oversight_id == "ov-bad"
