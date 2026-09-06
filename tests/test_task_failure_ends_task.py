"""A lost LLM connection must end the task (lighthouse v0.12.0 fold smoke).

One coding_agent member of a fanned-out PLAN step got ``httpx.RemoteProtocolError``
("Server disconnected without sending a response") from the MaaS gateway. It
escaped ``Agent._handle_task`` through the bus callback: no ``TASK_COMPLETE``,
the step stayed RUNNING until cancelled by hand. Two layers fix it:

* the OpenAI-compatible backend retries a broken transport like a timeout and
  raises a typed ``LLMCallError`` when the attempts are gone;
* the task loop turns ANY ``process_task`` failure into a blocked completion
  with the reason, so the executor cascades and the operator sees why.

Also here: a member folded under its plan step takes the step's role when the
cluster topology row has none (the observer never learns ``target_role``).
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from acc.backends import LLMCallError
from acc.backends.llm_openai_compat import OpenAICompatBackend


def _backend(max_retries: int = 2) -> OpenAICompatBackend:
    return OpenAICompatBackend(base_url="http://gw:8022/v1", model="m", max_retries=max_retries)


def _client_raising(exc: Exception):
    client = MagicMock(post=AsyncMock(side_effect=exc))
    mock = MagicMock()
    mock.return_value.__aenter__ = AsyncMock(return_value=client)
    mock.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock, client


@pytest.mark.asyncio
async def test_transport_error_is_retried_then_typed(monkeypatch):
    monkeypatch.setattr("acc.backends.llm_openai_compat.asyncio.sleep", AsyncMock())
    mock, client = _client_raising(httpx.RemoteProtocolError("Server disconnected without sending a response"))
    with patch("httpx.AsyncClient", mock):
        with pytest.raises(LLMCallError) as info:
            await _backend(max_retries=2).complete("sys", "usr")
    assert client.post.await_count == 2                          # retried like a timeout
    assert info.value.retryable is True
    assert "RemoteProtocolError" in str(info.value)
    assert "Server disconnected" in str(info.value)


@pytest.mark.asyncio
async def test_transport_error_recovers_on_a_later_attempt(monkeypatch):
    monkeypatch.setattr("acc.backends.llm_openai_compat.asyncio.sleep", AsyncMock())
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"choices": [{"message": {"content": "fine"}}], "usage": {"total_tokens": 3}}
    client = MagicMock(post=AsyncMock(side_effect=[httpx.ReadError("boom"), ok]))
    mock = MagicMock()
    mock.return_value.__aenter__ = AsyncMock(return_value=client)
    mock.return_value.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", mock):
        result = await _backend(max_retries=3).complete("sys", "usr")
    assert result["content"] == "fine"
    assert client.post.await_count == 2


# ---------------------------------------------------------------------------
# the task loop ends the task
# ---------------------------------------------------------------------------


def test_failed_task_result_is_a_blocked_completion_with_the_reason():
    from acc.agent import failed_task_result

    r = failed_task_result(LLMCallError("openai_compat: transport error: RemoteProtocolError: gone", retryable=True))
    assert r.blocked is True
    assert r.block_reason.startswith("task_error: LLMCallError: openai_compat: transport error")
    assert r.output == "" and r.assistant_proposals_executed == []
    long = failed_task_result(RuntimeError("x" * 1000))
    assert len(long.block_reason) <= 400                          # rides the bus, not a stack trace


def test_handle_task_completes_blocked_instead_of_re_raising():
    """Pin the shape in ``agent.py`` the way the target-agent filter tests do:
    the ``process_task`` failure path must assign ``failed_task_result`` and
    must not ``raise``."""
    from acc import agent as agent_mod

    src = inspect.getsource(agent_mod)
    i = src.index("result = await self._cognitive_core.process_task(")
    block = src[i: src.index("# Proposal 20260530-role-proposal-assistant-agent-of-agents Phase 2b", i)]
    assert "except Exception as _task_exc" in block
    assert "result = failed_task_result(_task_exc)" in block
    assert "\n                raise\n" not in block


# ---------------------------------------------------------------------------
# board: the folded member's role
# ---------------------------------------------------------------------------


def test_folded_member_takes_the_steps_role_when_the_row_has_none():
    from acc.work_board import project_board

    plans = {"p-1": {"plan_id": "p-1", "received_ts": 1.0,
                     "steps": [{"step_id": "s2", "role": "coding_agent", "depends_on": []}],
                     "step_progress": {"s2": "RUNNING"}, "step_tasks": {"s2": "plan-p-1-s2-c0ffee11-m1"}}}
    topo = {"c-c0ffee11": {"cluster_id": "c-c0ffee11", "target_role": "",     # the observer never learns it
                           "members": {"coding-1": {"task_id": "plan-p-1-s2-c0ffee11-m1", "status": "running"}}},
            "c-direct": {"cluster_id": "c-direct", "target_role": "",
                         "members": {"res-1": {"task_id": "t-direct", "status": "running"}}}}
    items = {it.id: it for it in project_board(active_plans=plans, cluster_topology=topo)}
    assert items["c-c0ffee11:coding-1"].parent == "p-1:s2"
    assert items["c-c0ffee11:coding-1"].role == "coding_agent"
    assert items["c-direct:res-1"].role == ""                     # nothing to borrow from
