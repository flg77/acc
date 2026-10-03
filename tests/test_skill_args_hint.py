"""The Available skills block names each skill's arguments.

2026-10-03: the block showed only ``purpose``, so the assistant guessed
``find_files`` arguments (max_count / max_depth) and the strict input schema
refused the call.  Listing the schema's property names, required ones starred,
lets the model emit a call that validates.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from acc.cognitive_core import CognitiveCore, _skill_args_hint
from acc.config import RoleDefinitionConfig
from acc.skills import SkillRegistry


def test_hint_marks_required_and_keeps_schema_order() -> None:
    schema = {
        "type": "object",
        "properties": {"root": {}, "pattern": {}, "max_results": {}},
        "required": ["root"],
    }
    assert _skill_args_hint(schema) == " (args: root*, pattern, max_results)"


def test_hint_empty_for_missing_or_argless_schema() -> None:
    assert _skill_args_hint(None) == ""
    assert _skill_args_hint({}) == ""
    assert _skill_args_hint({"type": "object", "properties": {}}) == ""


def test_prompt_lists_find_files_arguments() -> None:
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": "x", "usage": {"total_tokens": 1}})
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    registry = SkillRegistry()
    registry.load_from("skills")
    core = CognitiveCore(
        agent_id="a-1", collective_id="sol-01",
        llm=llm, vector=MagicMock(), redis_client=None, role_label="assistant",
        skill_registry=registry,
    )
    role = RoleDefinitionConfig(
        purpose="Help.", persona="concise", memory_retrieval=False,
        allowed_skills=["find_files"], default_skills=["find_files"],
    )
    prompt = core.build_system_prompt(role)
    assert "find_files: " in prompt
    assert "(args: root*, pattern, max_results, max_depth)" in prompt
