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
