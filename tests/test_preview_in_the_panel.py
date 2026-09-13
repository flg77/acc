"""UX-03 Phase 2 -- the declared dry run, shown in the decision panel.

Phase 1 shows the call (``runs: shell_exec {"cmd": "rm -rf build/"}``). That is
the call, not its effect. The operator answered UX-00 5.3 on 2026-09-13 --
a preview may run *anything with a real* ``--dry-run`` -- and accepted the risk
that comes with it: a dry-run flag is the **capability's** claim, not ACC's
guarantee.

So the rule these tests hold is: **declared, never inferred**. A capability
previews only what its own manifest declares; ACC never derives a dry run from
a command string. The preview is journalled as its own act, because it is an
execution, and a failure is rendered as a failure that resolves nothing.
"""

from __future__ import annotations

import asyncio

import pytest

from acc.capability_dispatch import (
    MAX_PREVIEW_OUTPUT,
    ParsedInvocation,
    preview_args_for,
    run_preview,
)


class _Manifest:
    def __init__(self, preview_args=None, **kw):
        self.preview_args = preview_args or {}
        for k, v in kw.items():
            setattr(self, k, v)


class _Core:
    """Records what the adapter path was asked to do."""

    def __init__(self, result=None, raises=None, delay=0.0):
        self.calls: list = []
        self._result = result if result is not None else {"would_remove": ["a", "b"]}
        self._raises = raises
        self._delay = delay

    async def invoke_skill(self, target, args, role):
        self.calls.append(("skill", target, dict(args)))
        return await self._answer()

    async def invoke_mcp_tool(self, server_id, tool, args, role):
        self.calls.append(("mcp", f"{server_id}.{tool}", dict(args)))
        return await self._answer()

    async def _answer(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._raises is not None:
            raise self._raises
        return self._result


def _inv(kind="skill", target="fs_clean", args=None):
    return ParsedInvocation(kind=kind, target=target, args=args or {"path": "build/"})


# ---------------------------------------------------------------------------
# declared, never inferred
# ---------------------------------------------------------------------------


def test_a_capability_that_declares_nothing_has_no_preview():
    assert preview_args_for(_Manifest()) == {}
    assert preview_args_for(object()) == {}


def test_a_declaration_is_read_from_the_manifest():
    assert preview_args_for(_Manifest({"dry_run": True})) == {"dry_run": True}


@pytest.mark.asyncio
async def test_nothing_runs_without_a_declaration():
    """The operator permitted executing a real dry run -- not guessing one."""
    core = _Core()
    assert await run_preview(core, _inv(), _Manifest(), None) == ()
    assert core.calls == [], "an undeclared capability must not be invoked"


@pytest.mark.asyncio
async def test_the_declared_arguments_are_merged_into_the_real_call():
    core = _Core()
    await run_preview(core, _inv(args={"path": "build/"}),
                      _Manifest({"dry_run": True}), None)
    assert core.calls == [("skill", "fs_clean", {"path": "build/", "dry_run": True})]


@pytest.mark.asyncio
async def test_the_declaration_wins_over_a_conflicting_argument():
    """A model that passed dry_run=False must not turn a preview into the act."""
    core = _Core()
    await run_preview(core, _inv(args={"path": "b/", "dry_run": False}),
                      _Manifest({"dry_run": True}), None)
    assert core.calls[0][2]["dry_run"] is True


@pytest.mark.asyncio
async def test_an_mcp_preview_splits_the_server_and_tool():
    core = _Core()
    await run_preview(core, _inv(kind="mcp", target="weather.send_alert", args={"city": "NY"}),
                      _Manifest({"dry_run": True}), None)
    assert core.calls == [("mcp", "weather.send_alert", {"city": "NY", "dry_run": True})]


@pytest.mark.asyncio
async def test_an_unknown_kind_previews_nothing():
    core = _Core()
    assert await run_preview(core, _inv(kind="wat"), _Manifest({"dry_run": True}), None) == ()
    assert core.calls == []


# ---------------------------------------------------------------------------
# what the panel is shown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_output_is_rendered_for_the_panel():
    lines = await run_preview(_Core(result={"would_remove": ["a", "b"]}),
                              _inv(), _Manifest({"dry_run": True}), None)
    assert lines[0].startswith("preview: ")
    assert "would_remove" in lines[0]


@pytest.mark.asyncio
async def test_a_preview_with_no_output_says_so():
    lines = await run_preview(_Core(result=""), _inv(), _Manifest({"dry_run": True}), None)
    assert lines == ("preview ran, no output",)


@pytest.mark.asyncio
async def test_a_long_preview_is_clipped():
    lines = await run_preview(_Core(result={"blob": "A" * 20000}), _inv(),
                              _Manifest({"dry_run": True}), None)
    assert sum(len(ln) for ln in lines) < MAX_PREVIEW_OUTPUT + 200


# ---------------------------------------------------------------------------
# a failure is a failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_preview_is_shown_as_a_failure():
    lines = await run_preview(_Core(raises=RuntimeError("server said no")),
                              _inv(), _Manifest({"dry_run": True}), None)
    assert lines == ("preview failed: RuntimeError: server said no",)


@pytest.mark.asyncio
async def test_a_slow_preview_gives_up_rather_than_wedging_the_gate():
    lines = await run_preview(_Core(delay=5), _inv(), _Manifest({"dry_run": True}),
                              None, timeout_s=0.05)
    assert lines and lines[0].startswith("preview failed: timed out")


@pytest.mark.asyncio
async def test_a_failure_never_reads_as_an_approval():
    """A preview resolves nothing: it returns lines, and only lines."""
    out = await run_preview(_Core(raises=RuntimeError("no")), _inv(),
                            _Manifest({"dry_run": True}), None)
    assert isinstance(out, tuple)
    assert all(isinstance(line, str) for line in out)


@pytest.mark.asyncio
async def test_a_guard_refusal_surfaces_as_a_failed_preview():
    """The preview goes through the adapter path, so A-017 / A-018 still apply."""
    lines = await run_preview(_Core(raises=PermissionError("A-018: tool not allowed")),
                              _inv(), _Manifest({"dry_run": True}), None)
    assert "A-018" in lines[0]


# ---------------------------------------------------------------------------
# it is journalled, because it is an execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_preview_is_journalled_with_its_own_kind(monkeypatch):
    recorded: list = []

    from acc import tracelog

    monkeypatch.setattr(tracelog, "tracelog_enabled", lambda: True)
    monkeypatch.setattr(
        tracelog, "log_tool_call",
        lambda session_id, **kw: recorded.append((session_id, kw)),
    )
    await run_preview(_Core(), _inv(), _Manifest({"dry_run": True}), None, task_id="t-1")
    assert recorded, "a preview is an execution and must be recorded"
    session_id, kw = recorded[0]
    assert session_id == "t-1" and kw["task_id"] == "t-1"
    assert kw["kind"] == "skill:preview"
    assert kw["preview"] is True
    assert kw["ok"] is True


@pytest.mark.asyncio
async def test_a_secret_argument_is_masked_in_the_journal(monkeypatch):
    recorded: list = []

    from acc import tracelog

    monkeypatch.setattr(tracelog, "tracelog_enabled", lambda: True)
    monkeypatch.setattr(
        tracelog, "log_tool_call",
        lambda session_id, **kw: recorded.append(kw),
    )
    await run_preview(_Core(), _inv(args={"api_key": "sk-live-abc"}),
                      _Manifest({"dry_run": True}), None, task_id="t-1")
    assert recorded[0]["args"]["api_key"] == "***"


@pytest.mark.asyncio
async def test_a_broken_journal_never_breaks_the_preview(monkeypatch):
    from acc import tracelog

    monkeypatch.setattr(tracelog, "tracelog_enabled", lambda: True)

    def _boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(tracelog, "log_tool_call", _boom)
    lines = await run_preview(_Core(), _inv(), _Manifest({"dry_run": True}), None)
    assert lines and lines[0].startswith("preview: ")
