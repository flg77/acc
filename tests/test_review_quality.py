"""Review-turn quality — found in the first live run (2026-10-05).

The security-report hand-off worked end to end, but the accepted report was
not trustworthy: the specialist's answer was silently cut at a hard-coded
4096 output tokens, the reviewing assistant "completed the missing sections"
with findings of its own, the specialist cited placeholder evidence, and the
review reply carried a wrong "I did not take a concrete action" banner.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from acc.review import MAX_RESULT_CHARS, handover_block, handover_content, review_content


def _message(stop_reason="end_turn", text="report"):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)], stop_reason=stop_reason,
        usage=SimpleNamespace(input_tokens=10, output_tokens=20,
                              cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )


def _backend(message):
    from acc.backends.llm_anthropic import AnthropicBackend

    be = AnthropicBackend(model="claude-x", embedding_model_path="/tmp/none")
    be._client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=message)))
    return be


# ---------------------------------------------------------------------------
# Output limit + truncation is visible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_output_limit_is_configurable_and_no_longer_4096(monkeypatch):
    monkeypatch.delenv("ACC_LLM_MAX_OUTPUT_TOKENS", raising=False)
    be = _backend(_message())
    await be.complete("sys", "user")
    assert be._client.messages.create.await_args.kwargs["max_tokens"] == 8192
    monkeypatch.setenv("ACC_LLM_MAX_OUTPUT_TOKENS", "16000")
    await be.complete("sys", "user")
    assert be._client.messages.create.await_args.kwargs["max_tokens"] == 16000


@pytest.mark.asyncio
async def test_a_cut_off_reply_is_flagged():
    out = await _backend(_message(stop_reason="max_tokens")).complete("sys", "user")
    assert out["truncated"] is True and out["stop_reason"] == "max_tokens"
    out = await _backend(_message()).complete("sys", "user")
    assert "truncated" not in out


def _core(llm, role_label):
    from tests.test_cognitive_core import _make_core

    return _make_core(llm=llm, role_label=role_label)


@pytest.mark.asyncio
async def test_a_truncated_answer_says_so_in_the_output():
    from tests.test_cognitive_core import _make_role

    from acc.cognitive_core import TRUNCATION_NOTE

    llm = SimpleNamespace(
        complete=AsyncMock(return_value={"content": "partial report", "truncated": True,
                                         "usage": {"total_tokens": 5}}),
        embed=AsyncMock(return_value=[0.0] * 384),
    )
    result = await _core(llm, "devops_engineer").process_task(
        {"content": "audit the server"}, role=_make_role())
    assert result.output.endswith(TRUNCATION_NOTE.strip())


# ---------------------------------------------------------------------------
# A review turn is not an act-intent task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_turn_gets_no_retry_and_no_banner():
    from tests.test_cognitive_core import _assistant_role, _seq_llm

    llm = _seq_llm(["The report covers the request. [REVIEW:accept]"])
    core = _core(llm, "assistant")
    result = await core.process_task(
        {"content": "[REVIEW TURN] You handed the operator's task to devops_engineer; route "
                    "and infuse were done. Act on this answer.",
         "review": {"id": "h-1", "role": "devops_engineer", "round": 0}},
        role=_assistant_role(),
    )
    assert llm.complete.call_count == 1
    assert "did not take a concrete action" not in result.output


# ---------------------------------------------------------------------------
# What the specialist and the reviewer are told
# ---------------------------------------------------------------------------


def _block():
    return handover_block(handover_id="h-1", origin_agent="assistant-1", role="devops_engineer",
                          task_id="t-1", goal="security report for the local server", brief="audit")


def test_specialist_must_cite_evidence_and_say_what_it_could_not_check():
    text = handover_content(_block())
    assert "Every finding must rest on output you actually got from a tool" in text
    assert "NOT CHECKED" in text and "/path/to/script" in text
    assert "inside your own container" in text


def test_reviewer_may_not_add_findings_or_finish_an_incomplete_answer():
    text = review_content(_block(), specialist_agent="worker-1", result="partial", notes=[])
    assert "Do NOT add findings, evidence or sections of your own" in text
    assert "do not complete it yourself" in text
    assert "inspected its own container" in text


def test_the_review_sees_as_much_as_the_bus_carries():
    from acc.agent import _task_output_max_chars

    assert MAX_RESULT_CHARS == _task_output_max_chars() == 16000
