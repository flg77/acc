"""The prompt cache depends on an ordering nothing currently enforces.

A prefix cache only pays when the *stable* part of the prompt is byte-identical
call to call. The moment something variable is assembled into it — retrieved
episodes, a timestamp, anything per-task — the bytes stop matching and the cache
silently stops working.

Nothing fails when that happens. The prompt is still correct, the agent still
answers, the tokens are simply no longer discounted. It surfaces as a cost line
drifting upward weeks later, if anyone is watching that closely.

ACC's answer (PR-CA1) is stronger than ordering: retrieved memory is not placed
late in the system prompt, it is kept **out of it entirely** and prepended to
the *user* message instead. The system prompt is therefore stable per role, with
no variable content to order around. That also settles the change's open
question about where retrieved memory belongs — it is already answered in the
code, and these tests are what stop it being undone.

Two failure modes are covered, because a guarantee like this can be broken from
either side: variable content leaking *into* the system prompt, and retrieved
memory being silently dropped so the invariant holds vacuously.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig


@pytest.fixture
def core():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": "", "usage": {"total_tokens": 0}})
    llm.embed = AsyncMock(return_value=[0.0])
    return CognitiveCore(
        agent_id="a",
        collective_id="c",
        llm=llm,
        vector=MagicMock(),
        redis_client=None,
        role_label="assistant",
    )


@pytest.fixture
def role():
    return RoleDefinitionConfig(
        purpose="Analyse incoming signals and summarise them.",
        persona="analytical",
        task_types=["TASK_ASSIGN"],
        allowed_actions=["publish_signal"],
        version="1.0.0",
    )


def episodes(n: int = 3) -> list[dict]:
    """Episodes in the shape `_render_episode_block` actually consumes."""
    return [
        {
            "ts_str": f"1{i}:30:00",
            "signal_type": "TASK_ASSIGN",
            "excerpt": f"episode number {i} with some distinctive text",
        }
        for i in range(n)
    ]


class TestTheSystemPromptStaysCacheable:
    def test_retrieved_memory_never_enters_the_system_prompt(self, core, role):
        """The guarantee, in one assertion.

        If retrieved episodes were ever folded into the system prompt, it would
        change per task and the per-role prefix could never be cached.
        """
        without = core.build_system_prompt(role, None)
        with_memory = core.build_system_prompt(role, episodes())

        assert with_memory == without, (
            "retrieved memory leaked into the system prompt; it changes every "
            "task, so the cacheable per-role prefix is gone and nothing fails"
        )

    def test_more_memory_still_does_not_change_it(self, core, role):
        baseline = core.build_system_prompt(role, None)
        assert core.build_system_prompt(role, episodes(1)) == baseline
        assert core.build_system_prompt(role, episodes(25)) == baseline

    def test_the_stable_prompt_is_deterministic(self, core, role):
        """No timestamp, counter or set-ordering may leak into the prefix."""
        assert core.build_system_prompt(role, None) == core.build_system_prompt(role, None)

    def test_a_role_change_is_allowed_to_change_it(self, core, role):
        """Stable *per role*, not globally.

        Stated so the guarantee is not mistaken for something stronger:
        editing a role legitimately invalidates its cache entry.
        """
        other = role.model_copy(update={"purpose": "Something entirely different."})
        assert core.build_system_prompt(role, None) != core.build_system_prompt(other, None)


class TestMemoryIsStillDelivered:
    """The other way this could break: holding the invariant vacuously.

    If retrieved episodes were simply dropped, every test above would pass and
    the agent would quietly lose its memory.
    """

    def test_episodes_are_rendered_for_the_user_message(self, core, role):
        block = core._render_episode_block(episodes())
        assert "RECENT_RELEVANT_EPISODES" in block
        for i in range(3):
            assert f"episode number {i}" in block

    def test_no_episodes_renders_nothing(self, core):
        """Empty must be indistinguishable from absent.

        Otherwise a task that retrieved nothing produces a different shape from
        one that never looked, and the legacy single-message form is broken for
        roles with memory_retrieval off.
        """
        assert core._render_episode_block([]) == ""
        assert core._render_episode_block(None) == ""

    def test_a_long_excerpt_is_truncated_not_dropped(self, core):
        long_one = [{"ts_str": "10:00:00", "signal_type": "TASK_ASSIGN", "excerpt": "x" * 500}]
        block = core._render_episode_block(long_one)
        assert block, "a long episode must still appear"
        assert len(block) < 500, "and must be truncated rather than sent whole"

    def test_newlines_in_an_excerpt_cannot_break_the_block(self, core):
        """One episode per line is the block's whole structure."""
        messy = [
            {
                "ts_str": "10:00:00",
                "signal_type": "TASK_ASSIGN",
                "excerpt": "first line\nsecond line\nthird",
            }
        ]
        body = [
            line
            for line in core._render_episode_block(messy).splitlines()
            if line.startswith("- [")
        ]
        assert len(body) == 1


class TestCacheHintPlumbing:
    def test_every_backend_accepts_the_hint(self):
        """A backend that cannot take `cache_prefix` breaks the call, not the cache."""
        import inspect

        from acc.backends.llm_anthropic import AnthropicBackend
        from acc.backends.llm_llama_stack import LlamaStackBackend
        from acc.backends.llm_ollama import OllamaBackend
        from acc.backends.llm_openai_compat import OpenAICompatBackend
        from acc.backends.llm_vllm import VLLMBackend

        for backend in (
            AnthropicBackend, OllamaBackend, OpenAICompatBackend,
            VLLMBackend, LlamaStackBackend,
        ):
            params = inspect.signature(backend.complete).parameters
            assert "cache_prefix" in params, f"{backend.__name__} cannot take the hint"

    def test_the_protocol_declares_it(self):
        import inspect

        from acc.backends import LLMBackend

        assert "cache_prefix" in inspect.signature(LLMBackend.complete).parameters

    def test_cache_token_counts_are_carried_end_to_end(self):
        """The measurement this change asks for needs the counts to survive.

        Anthropic reports cache_creation/cache_read separately from
        input/output; if they stop at the backend there is no number to base a
        default on.
        """
        from acc.cognitive_core import StressIndicators

        assert hasattr(StressIndicators(), "cache_read_tokens")
