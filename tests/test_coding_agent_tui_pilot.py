"""Tier B — Pilot tests for the ``coding_agent`` TUI flow.

Verifies that the canonical TUI surfaces consume the ``coding_agent``
role correctly:

* **Ecosystem screen** — coding_agent appears in ROLE LIBRARY, the
  ROLE DETAIL panel renders its YAML, the SKILLS / MCP SERVERS
  tables show the echo skill + echo_server allow-listed by the role.
* **Schedule infusion** routes to the Nucleus form with task_types
  pre-filled.
* **Prompt pane** targets ``coding_agent`` by default; Send publishes
  a TASK_ASSIGN whose ``target_role`` matches.
* **Performance screen** renders capability-invocation telemetry +
  recent failures from synthetic TASK_COMPLETEs sourced from a
  ``coding_agent-*`` agent_id.

Tier B reuses the harness patterns established in PR-A
(``_capture_static_updates``) and PR-B (``_StubObserver`` /
``_PromptHarness``).  No live NATS, no live agents — every signal
that the TUI consumes is synthesised in the test.

Schema invariants the TUI relies on (persona, task_types, risk
ceilings, …) are pinned in :mod:`tests.test_coding_agent_role` (Tier
A).  This file does NOT re-assert those — if Tier A fails, the
operator-facing TUI flow these tests cover is undefined behaviour
anyway.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import DataTable, Input, Select, Static, TextArea

from acc.tui.client import NATSObserver
from acc.tui.messages import RolePreloadMessage
from acc.tui.models import CollectiveSnapshot
from acc.tui.screens.ecosystem import EcosystemScreen
from acc.tui.screens.performance import PerformanceScreen
from acc.tui.screens.prompt import PromptScreen


# Repo-anchored paths — same resolution path the TUI uses at runtime.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES_ROOT = _REPO_ROOT / "roles"
_SKILLS_ROOT = _REPO_ROOT / "skills"
_MCPS_ROOT = _REPO_ROOT / "mcps"


@pytest.fixture(autouse=True)
def _pin_repo_roots(monkeypatch):
    """Ensure every TUI screen resolves the manifest dirs to the repo's
    canonical paths regardless of the test runner's cwd.  Mirrors the
    PR-A ``resolve_manifest_root`` precedence (env var wins)."""
    monkeypatch.setenv("ACC_ROLES_ROOT", str(_ROLES_ROOT))
    monkeypatch.setenv("ACC_SKILLS_ROOT", str(_SKILLS_ROOT))
    monkeypatch.setenv("ACC_MCPS_ROOT", str(_MCPS_ROOT))


def _capture_static_updates(widget) -> list[str]:
    """Same monkeypatch trick PR-A's ecosystem tests use to read
    ``Static.update`` calls across Textual versions."""
    captured: list[str] = []
    real_update = widget.update

    def recording(content="", **kwargs):
        captured.append(str(content))
        return real_update(content, **kwargs)

    widget.update = recording  # type: ignore[assignment]
    return captured


# ---------------------------------------------------------------------------
# Ecosystem screen — coding_agent surfaces correctly
# ---------------------------------------------------------------------------


class _EcoHarness(App):
    captured_preload: list[RolePreloadMessage]

    def __init__(self) -> None:
        super().__init__()
        self.captured_preload = []

    def on_mount(self) -> None:
        self.push_screen(EcosystemScreen())

    def on_role_preload_message(self, message: RolePreloadMessage) -> None:
        self.captured_preload.append(message)


@pytest.mark.asyncio
async def test_ecosystem_role_library_row_for_coding_agent():
    """ROLE LIBRARY contains a row keyed ``coding_agent`` with the
    role's domain + persona + task count rendered."""
    app = _EcoHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EcosystemScreen)

        role_table = screen.query_one("#role-table", DataTable)
        keys = [
            getattr(k, "value", str(k)) for k in role_table.rows.keys()
        ]
        assert "coding_agent" in keys, (
            f"coding_agent missing from ROLE LIBRARY (saw {keys})"
        )


@pytest.mark.asyncio
async def test_ecosystem_role_detail_renders_coding_agent_seed():
    """Selecting the coding_agent row populates ROLE DETAIL with text
    pulled from the actual ``role.yaml`` (purpose / seed_context)."""
    app = _EcoHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        captured = _capture_static_updates(
            screen.query_one("#role-detail-panel", Static),
        )

        role_table = screen.query_one("#role-table", DataTable)
        # Find the row key for coding_agent — they're added with key=role_name
        # but the API exposes opaque RowKey objects.  Build a synthetic
        # RowSelected (same harness PR-A used).
        coding_row_key = next(
            k for k in role_table.rows.keys()
            if getattr(k, "value", str(k)) == "coding_agent"
        )
        screen.on_data_table_row_selected(
            DataTable.RowSelected(
                data_table=role_table,
                cursor_row=0,
                row_key=coding_row_key,
            )
        )
        await pilot.pause()

        rendered = "\n".join(captured)
        # The actual role.yaml carries this purpose phrase verbatim.
        assert "Generate, review, and test code artefacts" in rendered, rendered
        # And the persona is analytical (Tier A pins this).
        assert "analytical" in rendered


@pytest.mark.asyncio
async def test_ecosystem_skills_table_shows_echo_for_coding_agent_role():
    """SKILLS table contains the ``echo`` skill row — the only entry
    in coding_agent's allowed_skills.  Verifies the path-resolution
    fix from PR-A loads the repo skills/ correctly."""
    app = _EcoHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        skills_table = screen.query_one("#skills-table", DataTable)

        skill_keys = [
            getattr(k, "value", str(k)) for k in skills_table.rows.keys()
        ]
        assert "echo" in skill_keys, (
            f"echo missing from SKILLS table (saw {skill_keys})"
        )


@pytest.mark.asyncio
async def test_ecosystem_mcps_table_shows_echo_server_for_coding_agent_role():
    """MCP SERVERS table contains the ``echo_server`` row."""
    app = _EcoHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        mcps_table = screen.query_one("#mcps-table", DataTable)

        mcp_keys = [
            getattr(k, "value", str(k)) for k in mcps_table.rows.keys()
        ]
        assert "echo_server" in mcp_keys, (
            f"echo_server missing from MCP SERVERS table (saw {mcp_keys})"
        )


@pytest.mark.asyncio
async def test_schedule_infusion_button_dispatches_role_preload_for_coding_agent():
    """After selecting coding_agent + pressing Send, the App receives
    a ``RolePreloadMessage`` carrying ``role_name='coding_agent'``."""
    app = _EcoHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen._selected_role = "coding_agent"
        from textual.widgets import Button
        btn = screen.query_one("#btn-schedule-infusion", Button)
        btn.disabled = False
        btn.press()
        await pilot.pause()

        assert len(app.captured_preload) == 1
        assert app.captured_preload[0].role_name == "coding_agent"


# ---------------------------------------------------------------------------
# Prompt pane — defaults + send-payload routing
# ---------------------------------------------------------------------------


class _StubObserver:
    """Channel-shaped observer stand-in.  Mirrors
    ``acc.tui.client.NATSObserver``'s prompt-pane surface."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict]] = []
        self._listeners: dict[str, asyncio.Future] = {}
        self._progress_listeners: dict[str, list] = {}

    async def publish(self, subject, payload):
        self.published.append((subject, payload))

    def register_task_listener(self, task_id, future):
        self._listeners[task_id] = future

    def unregister_task_listener(self, task_id):
        self._listeners.pop(task_id, None)

    def register_task_progress_listener(self, task_id, callback):
        self._progress_listeners.setdefault(task_id, []).append(callback)

    def unregister_task_progress_listener(self, task_id):
        self._progress_listeners.pop(task_id, None)


class _PromptHarness(App):
    def __init__(self) -> None:
        super().__init__()
        self.observer = _StubObserver()
        self._observers = [self.observer]
        self._active_collective_idx = 0
        self._collective_ids = ["sol-test"]

    def on_mount(self) -> None:
        self.push_screen(PromptScreen())


@pytest.mark.asyncio
async def test_prompt_pane_target_role_defaults_to_coding_agent():
    """The Select widget's value at mount must be ``coding_agent``.

    coding_agent is the demonstrator role in the corpus + the default
    target the operator sees on screen 7.  A future "improve UX" PR
    that switches the default to ``ingester`` would be a regression.
    """
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        select = screen.query_one("#select-target-role", Select)
        assert str(select.value) == "coding_agent"


@pytest.mark.asyncio
async def test_prompt_send_routes_task_assign_to_coding_agent():
    """A Send with the default Select value publishes a TASK_ASSIGN
    whose ``target_role`` is ``coding_agent``."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen.query_one("#prompt-textarea", TextArea).text = (
            "Generate a unit test for FizzBuzz"
        )
        screen.action_send()
        for _ in range(8):
            await pilot.pause()
            if app.observer.published:
                break

        assert app.observer.published, "send worker never published"
        subject, payload = app.observer.published[0]
        assert subject == "acc.sol-test.task"
        assert payload["signal_type"] == "TASK_ASSIGN"
        assert payload["target_role"] == "coding_agent"
        assert payload["content"] == (
            "Generate a unit test for FizzBuzz"
        )
        # No agent-specific pin → broadcast to the whole role.
        assert "target_agent_id" not in payload


@pytest.mark.asyncio
async def test_prompt_send_with_target_agent_id_pins_to_specific_coding_agent():
    """Operator types a coding_agent-* id → published TASK_ASSIGN
    carries ``target_agent_id`` so PR-B's filter on the agent side
    only lets the named agent process the task."""
    app = _PromptHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        screen.query_one("#prompt-textarea", TextArea).text = "ping"
        screen.query_one("#input-target-agent-id", Input).value = (
            "coding_agent-deadbeef"
        )
        screen.action_send()
        for _ in range(8):
            await pilot.pause()
            if app.observer.published:
                break

        payload = app.observer.published[0][1]
        assert payload["target_role"] == "coding_agent"
        assert payload["target_agent_id"] == "coding_agent-deadbeef"


# ---------------------------------------------------------------------------
# Performance screen — capability telemetry from coding_agent
# ---------------------------------------------------------------------------


class _PerfHarness(App):
    def on_mount(self) -> None:
        self.push_screen(PerformanceScreen())


def _coding_agent_task_complete(
    *, agent_id: str = "coding_agent-deadbeef",
    invocations: list[dict],
) -> dict:
    """Build a TASK_COMPLETE payload sourced from a coding_agent.

    Mirrors the canonical wire shape from
    ``acc.agent._handle_task``'s publish call (PR 4.4 + PR-B).
    """
    return {
        "signal_type": "TASK_COMPLETE",
        "agent_id": agent_id,
        "task_id": "test-task",
        "ts": time.time(),
        "blocked": False,
        "block_reason": "",
        "latency_ms": 100.0,
        "output": "ok",
        "invocations": invocations,
    }


def _make_observer_with_snapshot() -> tuple[NATSObserver, CollectiveSnapshot]:
    """Build a real NATSObserver pointed at a test-only collective +
    return its empty CollectiveSnapshot for direct manipulation.

    Bypasses ``connect``/``subscribe`` — we exercise the routing
    methods directly with synthetic payloads so the test doesn't need
    a NATS server.
    """
    obs = NATSObserver(
        nats_url="nats://test.invalid:4222",
        collective_id="sol-test",
        update_queue=asyncio.Queue(),
    )
    return obs, obs._snapshot


@pytest.mark.asyncio
async def test_performance_telemetry_records_coding_agent_skill_invocation():
    """PR #15 telemetry routes coding_agent's [SKILL: echo] invocation
    into the CAPABILITY INVOCATIONS table on the Performance screen."""
    obs, snap = _make_observer_with_snapshot()
    obs._route_task_complete(
        "coding_agent-deadbeef",
        _coding_agent_task_complete(
            invocations=[
                {"kind": "skill", "target": "echo", "ok": True, "error": ""},
            ],
        ),
    )

    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        screen.snapshot = snap
        await pilot.pause()

        cap_table = screen.query_one(
            "#capability-invocations-table", DataTable,
        )
        # Should have exactly one row keyed ``skill:echo``.
        keys = [
            getattr(k, "value", str(k)) for k in cap_table.rows.keys()
        ]
        assert keys == ["skill:echo"], keys


@pytest.mark.asyncio
async def test_performance_failures_panel_renders_a_017_block_from_coding_agent():
    """A coding_agent task whose [SKILL: shell.exec] hit Cat-A A-017
    (skill not in allowed_skills) shows up in the RECENT FAILURES
    panel with the failure reason visible to the operator."""
    obs, snap = _make_observer_with_snapshot()
    obs._route_task_complete(
        "coding_agent-deadbeef",
        _coding_agent_task_complete(
            invocations=[
                {
                    "kind": "skill", "target": "shell.exec",
                    "ok": False,
                    "error": (
                        "A-017 blocked skill 'shell.exec': "
                        "skill 'shell.exec' not in role.allowed_skills "
                        "(allowed=['echo'])"
                    ),
                },
            ],
        ),
    )

    app = _PerfHarness()
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen

        failures_panel = screen.query_one(
            "#capability-failures-panel", Static,
        )
        captured = _capture_static_updates(failures_panel)
        screen.snapshot = snap
        await pilot.pause()

        rendered = "\n".join(captured)
        # Failed skill name + error reason appear.
        assert "shell.exec" in rendered
        assert "A-017" in rendered
        # Originating coding_agent id is visible (truncated to 12 chars).
        assert "coding_agent" in rendered
