"""Empty-content task handling — the guard + the content-resolution fallback.

Regression coverage for the "messages.0: user messages must have non-empty
content" 400: a TASK_ASSIGN whose instruction lives only in ``task_description``
(the wakeup scan, Slack/schedule channels, plan steps) must still reach the LLM
with a non-empty user message, and a task with nothing actionable in any field
must be dropped before the LLM call.
"""

from __future__ import annotations

import pytest

from acc.agent import _task_has_content
from acc.cognitive_core import resolve_user_content


@pytest.mark.parametrize("field", ["content", "task_description", "prompt", "text"])
def test_task_has_content_true_for_each_recognised_field(field):
    assert _task_has_content({field: "do the thing"}) is True


def test_task_has_content_false_when_all_empty_or_missing():
    assert _task_has_content({}) is False
    assert _task_has_content({"content": "", "task_description": "   "}) is False
    assert _task_has_content({"content": None, "prompt": ""}) is False
    # Non-content fields don't count as actionable.
    assert _task_has_content({"task_id": "abc", "target_role": "assistant"}) is False


def test_resolve_user_content_prefers_content():
    p = {"content": "canonical", "task_description": "legacy"}
    assert resolve_user_content(p) == "canonical"


def test_resolve_user_content_falls_back_to_task_description():
    # The wakeup scan / Slack / schedule / plan set only task_description — the
    # fallback keeps them from reaching the LLM as an empty user message.
    assert resolve_user_content({"task_description": "scan for work"}) == "scan for work"


def test_resolve_user_content_empty_when_neither_present():
    assert resolve_user_content({}) == ""
    assert resolve_user_content({"prompt": "ignored-here"}) == ""
