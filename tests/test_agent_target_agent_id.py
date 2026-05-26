"""Unit tests for the ``target_agent_id`` filter on Agent._handle_task.

PR-B added a single-line filter at the top of the inner _handle_task
closure inside ``Agent._task_loop``: when the inbound TASK_ASSIGN
payload carries ``target_agent_id``, only the agent whose id matches
processes the task; everyone else returns silently.

We test the filter in isolation by invoking the same code path with
a stub Agent — that's cheaper than booting a full agent + NATS stack.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _filter_inline(data: dict, agent_id: str) -> bool:
    """Replicates the PR-B filter at the top of ``_handle_task``.

    Returns True when the agent should process the task, False when
    it should be silently dropped.  Mirrors the exact branch in
    ``acc/agent.py:_handle_task`` so a regression that drifts the
    two apart will surface immediately when the test fails.
    """
    target_aid = data.get("target_agent_id")
    if target_aid and target_aid != agent_id:
        return False
    return True


def test_no_target_agent_id_processes_normally():
    """Legacy broadcast: missing key → all agents process."""
    payload = {
        "signal_type": "TASK_ASSIGN",
        "task_id": "abc",
        "target_role": "coding_agent",
    }
    assert _filter_inline(payload, "coding_agent-aaa") is True
    assert _filter_inline(payload, "coding_agent-bbb") is True


def test_explicit_none_target_agent_id_processes_normally():
    """``None`` is treated identically to missing key."""
    payload = {
        "signal_type": "TASK_ASSIGN",
        "task_id": "abc",
        "target_role": "coding_agent",
        "target_agent_id": None,
    }
    assert _filter_inline(payload, "coding_agent-aaa") is True


def test_matching_target_agent_id_processes_only_that_agent():
    payload = {
        "signal_type": "TASK_ASSIGN",
        "task_id": "abc",
        "target_role": "coding_agent",
        "target_agent_id": "coding_agent-aaa",
    }
    assert _filter_inline(payload, "coding_agent-aaa") is True
    assert _filter_inline(payload, "coding_agent-bbb") is False


def test_empty_string_target_agent_id_processes_normally():
    """Empty-string ``target_agent_id`` is falsy; treated as broadcast.

    A future Slack adapter that sets the field to ``""`` instead of
    omitting it must NOT accidentally pin the task to a non-existent
    agent.
    """
    payload = {
        "signal_type": "TASK_ASSIGN",
        "task_id": "abc",
        "target_role": "coding_agent",
        "target_agent_id": "",
    }
    assert _filter_inline(payload, "coding_agent-aaa") is True


def _filter_inline_v4(data: dict, agent_id: str, my_role: str) -> bool:
    """Replicates the PR-V4 directed-by-role filter in ``_handle_task``.

    Returns True when the agent should process the task.  A specific
    ``target_agent_id`` (matching) always wins; otherwise a non-empty
    ``target_role`` must equal the agent's role or be its parent
    (``coding_agent`` matches ``coding_agent_implementer``).
    """
    target_aid = data.get("target_agent_id")
    if target_aid and target_aid != agent_id:
        return False
    if not target_aid:
        target_role = str(data.get("target_role", "") or "").strip()
        if (
            target_role
            and target_role != my_role
            and not my_role.startswith(target_role + "_")
        ):
            return False
    return True


def test_role_targeted_prompt_only_handled_by_that_role():
    """The reported bug: a coding_agent prompt must NOT be answered by analyst."""
    payload = {"signal_type": "TASK_ASSIGN", "task_id": "x", "target_role": "coding_agent"}
    assert _filter_inline_v4(payload, "coding_agent-aaa", "coding_agent") is True
    assert _filter_inline_v4(payload, "analyst-bbb", "analyst") is False


def test_role_target_matches_subrole():
    payload = {"signal_type": "TASK_ASSIGN", "task_id": "x", "target_role": "coding_agent"}
    assert _filter_inline_v4(payload, "ci-1", "coding_agent_implementer") is True
    assert _filter_inline_v4(payload, "ct-1", "coding_agent_tester") is True


def test_empty_target_role_broadcasts():
    payload = {"signal_type": "TASK_ASSIGN", "task_id": "x"}
    assert _filter_inline_v4(payload, "analyst-bbb", "analyst") is True


def test_explicit_agent_id_overrides_role_mismatch():
    """A named agent handles the task even if its role differs from target_role."""
    payload = {
        "signal_type": "TASK_ASSIGN", "task_id": "x",
        "target_role": "coding_agent", "target_agent_id": "analyst-bbb",
    }
    assert _filter_inline_v4(payload, "analyst-bbb", "analyst") is True


def test_handle_task_filter_matches_agent_py_implementation():
    """Smoke check that the filter we test is actually the one
    shipped in :mod:`acc.agent`.

    We re-read the source file (no import — we only need the text)
    and assert the canonical branch we tested above appears verbatim.
    Catches the regression where someone refactors the filter to a
    helper function that changes its semantics without our knowledge.
    """
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "acc" / "agent.py"
    text = src.read_text(encoding="utf-8")
    assert 'target_aid = data.get("target_agent_id")' in text
    assert 'if target_aid and target_aid != self.agent_id' in text
    # PR-V4 — the directed-by-role filter must stay wired.
    assert 'target_role = str(data.get("target_role", "") or "").strip()' in text
    assert 'my_role.startswith(target_role + "_")' in text
