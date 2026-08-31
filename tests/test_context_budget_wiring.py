"""The packer, as `cognitive_core` actually uses it.

`test_context_budget.py` covers the packer in isolation. This covers the seam:
that the budget is resolved from the environment the way a synthesized agent is
configured, that the stage event carries what an operator needs, that the kill
switch really restores the old behaviour, and -- the one that matters most --
that a deployment which was never overflowing sees a byte-identical prompt.

Change: ``openspec/changes/20260826-context-budget`` Phase 1.5.
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

import acc.cognitive_core as cc
from acc.cognitive_core import CognitiveCore, _resolve_context_budget
from acc.config import RoleDefinitionConfig
from acc.context_budget import ContextOverflow
from acc.thread_continuity import REPLAY_HEADING

_ENV_VARS = (
    "ACC_CONTEXT_BUDGET",
    "ACC_CONTEXT_WINDOW_DEFAULT",
    "ACC_LLM_CONTEXT_WINDOW",
    "ACC_DEPLOY_MODE",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Budgeting is env-driven; a leaked var would make these tests lie."""
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def stages(monkeypatch):
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        cc, "emit_stage", lambda name, attrs=None: captured.append((name, attrs or {}))
    )
    return captured


def _core() -> CognitiveCore:
    llm = MagicMock()
    llm.complete = AsyncMock(return_value={"content": "ok", "usage": {"total_tokens": 1}})
    llm.embed = AsyncMock(return_value=[0.0] * 384)
    return CognitiveCore(
        agent_id="a", collective_id="c", llm=llm, vector=MagicMock(),
        redis_client=None, role_label="analyst",
    )


def _episodes(n: int) -> list[dict]:
    return [
        {"ts_str": f"1{i}:00:00", "signal_type": "TASK_ASSIGN",
         "excerpt": f"episode {i} " + "detail " * 20}
        for i in range(n)
    ]


def _notes(n: int) -> list[str]:
    return [f"lesson {i} " + "wordy " * 20 for i in range(n)]


def _thread(n: int) -> str:
    return REPLAY_HEADING + "\n" + "\n".join(
        f"operator: turn {i} " + "chatter " * 20 for i in range(n)
    )


class TestResolution:
    def test_nothing_declared_falls_back_low_and_says_so(self):
        """Guessing small costs recall; guessing large costs integrity."""
        budget = _resolve_context_budget(system_tokens=0)
        assert budget.window == 8192
        assert budget.source == "default"

    def test_a_declared_window_is_used_and_labelled_declared(self, monkeypatch):
        monkeypatch.setenv("ACC_LLM_CONTEXT_WINDOW", "32768")
        budget = _resolve_context_budget(system_tokens=0)
        assert budget.window == 32768
        assert budget.source == "declared"

    def test_the_override_beats_the_declaration(self, monkeypatch):
        monkeypatch.setenv("ACC_LLM_CONTEXT_WINDOW", "32768")
        monkeypatch.setenv("ACC_CONTEXT_BUDGET", "4096")
        budget = _resolve_context_budget(system_tokens=0)
        assert budget.window == 4096
        assert budget.source == "override"

    def test_zero_is_the_kill_switch(self, monkeypatch):
        monkeypatch.setenv("ACC_CONTEXT_BUDGET", "0")
        assert _resolve_context_budget(system_tokens=0) is None

    @pytest.mark.parametrize("junk", ["", "  ", "eight thousand", "8192.5"])
    def test_junk_never_disables_budgeting_by_accident(self, monkeypatch, junk):
        """A typo in an env var must not silently switch off the ceiling."""
        monkeypatch.setenv("ACC_CONTEXT_BUDGET", junk)
        monkeypatch.setenv("ACC_LLM_CONTEXT_WINDOW", "16384")
        budget = _resolve_context_budget(system_tokens=0)
        assert budget is not None and budget.window == 16384

    def test_the_system_prompt_is_subtracted_from_the_ceiling(self, monkeypatch):
        monkeypatch.setenv("ACC_LLM_CONTEXT_WINDOW", "32768")
        roomy = _resolve_context_budget(system_tokens=0)
        cramped = _resolve_context_budget(system_tokens=4000)
        assert roomy.ceiling - cramped.ceiling == 4000

    def test_deploy_mode_selects_posture_but_not_the_window(self, monkeypatch):
        """REQ-CAP-004 at the seam: an edge box may serve a 128k model."""
        monkeypatch.setenv("ACC_LLM_CONTEXT_WINDOW", "131072")
        monkeypatch.setenv("ACC_DEPLOY_MODE", "edge")
        budget = _resolve_context_budget(system_tokens=0)
        assert budget.window == 131072
        assert budget.posture.name == "edge"


class TestTheDefaultPathIsUnchanged:
    def test_a_prompt_that_fits_is_byte_identical_to_the_unbudgeted_one(self):
        """The property the rollout rests on."""
        core = _core()
        args = ("do the thing", _episodes(3), _notes(2))
        kwargs = {"thread_block": _thread(3)}

        legacy = core._compose_user_content(*args, **kwargs)
        budgeted = core._compose_user_content(
            *args, **kwargs, budget=_resolve_context_budget(system_tokens=0)
        )
        assert budgeted == legacy

    def test_the_kill_switch_restores_the_legacy_assembly(self, monkeypatch):
        monkeypatch.setenv("ACC_CONTEXT_BUDGET", "0")
        core = _core()
        args = ("task", _episodes(2), _notes(2))

        assert core._compose_user_content(
            *args, budget=_resolve_context_budget(system_tokens=0)
        ) == core._compose_user_content(*args)

    def test_no_budget_argument_means_no_behaviour_change(self):
        """Every pre-existing caller passes positionally and gets the old path."""
        core = _core()
        out = core._compose_user_content("task", None, None)
        assert out == "task"


class TestUnderPressure:
    def _tight(self, core, window="1200"):
        import os

        os.environ["ACC_LLM_CONTEXT_WINDOW"] = window
        try:
            return core._compose_user_content(
                "the operator's actual request",
                _episodes(6), _notes(5),
                thread_block=_thread(6),
                budget=_resolve_context_budget(system_tokens=0),
            )
        finally:
            os.environ.pop("ACC_LLM_CONTEXT_WINDOW", None)

    def test_the_operators_request_always_survives(self, stages):
        text = self._tight(_core())
        assert "the operator's actual request" in text

    def test_something_is_actually_dropped(self, stages):
        text = self._tight(_core())
        assert len(text) < len(_thread(6))

    def test_the_drop_is_reported_as_a_stage_event(self, stages):
        self._tight(_core())
        events = [a for n, a in stages if n == "acc.pipeline.context_budget"]
        assert len(events) == 1

        event = events[0]
        assert event["degraded"] is True
        assert event["dropped"] > 0
        assert event["window"] == 1200
        assert event["window_source"] == "declared"
        assert set(event) >= {"ceiling", "est_tokens", "kept", "dropped_by_kind"}

    def test_a_healthy_turn_still_emits_the_event_undegraded(self, stages):
        """Silence would be indistinguishable from the check not running."""
        core = _core()
        core._compose_user_content(
            "task", _episodes(1), _notes(1),
            budget=_resolve_context_budget(system_tokens=0),
        )
        events = [a for n, a in stages if n == "acc.pipeline.context_budget"]
        assert len(events) == 1 and events[0]["degraded"] is False

    def test_a_window_smaller_than_its_own_reserve_fails_loudly(self, monkeypatch):
        """A 600-token window under the edge posture is unusable, and says so.

        reserve(512) + margin(120) already exceeds it, so the ceiling clamps to
        1 and every task overflows. That is the right outcome -- the
        configuration is nonsense and a loud failure beats an agent silently
        sent a prompt with nothing in it -- but it is surprising enough to be
        worth pinning.
        """
        monkeypatch.setenv("ACC_LLM_CONTEXT_WINDOW", "600")
        budget = _resolve_context_budget(system_tokens=0)
        assert budget.ceiling == 1

        with pytest.raises(ContextOverflow):
            _core()._compose_user_content("any task at all", None, None, budget=budget)

    def test_overflow_is_still_recorded_before_it_propagates(self, stages):
        core = _core()
        with pytest.raises(ContextOverflow):
            core._compose_user_content(
                "word " * 5000, None, None, budget=_tiny(_resolve_context_budget(0)),
            )
        events = [a for n, a in stages if n == "acc.pipeline.context_budget"]
        assert events and events[-1]["overflow"] is True


def _tiny(budget):
    """The same budget with a ceiling nothing can fit under."""
    import dataclasses

    return dataclasses.replace(budget, ceiling=5)


class TestTheThreadIsSplitNotDropped:
    def test_a_known_replay_block_is_divisible(self):
        heading, items = CognitiveCore._split_thread_block(_thread(4))
        assert heading == REPLAY_HEADING
        assert len(items) == 4

    def test_the_newest_turns_are_the_ones_kept(self):
        """`replay_block` renders chronologically, so 'newest last' is what
        arrives; the packer keeps from the front, so the block must be handed
        over in keep-preference order."""
        heading, items = CognitiveCore._split_thread_block(_thread(4))
        assert "turn 0" in items[0]

    def test_an_unrecognised_shape_degrades_to_one_indivisible_item(self):
        """Better a whole-block drop than a guess about where turns begin."""
        heading, items = CognitiveCore._split_thread_block("something else entirely")
        assert heading == ""
        assert items == ["something else entirely"]

    def test_an_empty_thread_contributes_nothing(self):
        core = _core()
        out = core._compose_user_content(
            "task", None, None, thread_block="",
            budget=_resolve_context_budget(system_tokens=0),
        )
        assert out == "task"


class TestSystemPromptMeasurement:
    def test_it_is_measured_once_per_role(self):
        core = _core()
        role = RoleDefinitionConfig(purpose="p", persona="concise", version="1.0.0")
        prompt = core.build_system_prompt(role)

        first = core._system_prompt_tokens(role, prompt)
        assert first > 0
        assert core._system_prompt_tokens(role, prompt) == first
        assert len(core._system_token_cache) == 1

    def test_a_changed_prompt_invalidates_the_measurement(self):
        """Stable per role, not forever: editing a role legitimately changes it."""
        core = _core()
        role = RoleDefinitionConfig(purpose="p", persona="concise", version="1.0.0")
        core._system_prompt_tokens(role, "short")
        core._system_prompt_tokens(role, "a considerably longer system prompt")
        assert len(core._system_token_cache) == 2

    def test_the_renderers_still_agree_with_their_parts(self):
        """`_render_*_block` is rewritten in terms of `_*_parts`; if the two
        drift, the packed and unpacked prompts stop matching."""
        eps = _episodes(3)
        heading, items, footer = CognitiveCore._episode_parts(eps)
        assert CognitiveCore._render_episode_block(eps) == "\n".join(
            [heading, *items, footer]
        )

        notes = _notes(2)
        n_heading, n_items, _ = CognitiveCore._notes_parts(notes)
        assert CognitiveCore._render_memory_notes_block(notes) == "\n".join(
            [n_heading, *n_items]
        )

    def test_empty_inputs_render_empty_through_both_paths(self):
        assert CognitiveCore._render_episode_block([]) == ""
        assert CognitiveCore._render_episode_block(None) == ""
        assert CognitiveCore._render_memory_notes_block([]) == ""
        assert CognitiveCore._episode_parts(None) == ("", [], "")
        assert CognitiveCore._notes_parts(None) == ("", [], "")


class TestTheShimContractStillHolds:
    def test_compose_works_on_a_bare_namespace(self):
        """`test_thread_continuity` calls this unbound with a two-method shim.

        Keeping the budget an explicit argument rather than reading it off
        `self` is what preserves that, and it is the better design anyway:
        the seam stays pure and the environment is read in one place.
        """
        shim = types.SimpleNamespace(
            _render_memory_notes_block=CognitiveCore._render_memory_notes_block,
            _render_episode_block=CognitiveCore._render_episode_block,
        )
        out = CognitiveCore._compose_user_content(
            shim, "the request", None, None, thread_block=_thread(2),
        )
        assert out.endswith("the request")
        assert REPLAY_HEADING in out
