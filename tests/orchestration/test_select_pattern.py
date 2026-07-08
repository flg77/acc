"""Unit tests for acc.orchestration.patterns (ACC Implementation 053, P0).

The selector is pure + deterministic, so these are plain table tests.  The last
group proves the describe-only wiring: the ``## Orchestration shapes`` guidance
block appears in a role's system prompt only when ``orchestration_hint`` is set,
and it adds no dispatch of its own.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from acc.cognitive_core import CognitiveCore
from acc.config import RoleDefinitionConfig
from acc.orchestration import (
    PatternSignals,
    SelectorThresholds,
    Shape,
    render_guidance_block,
    select_pattern,
)


# --- select_pattern: one branch at a time -----------------------------------

def test_default_is_single_agent():
    assert select_pattern(PatternSignals()).shape is Shape.SINGLE


def test_high_consequence_forces_adversarial():
    c = select_pattern(PatternSignals(consequence="HIGH"))
    assert c.shape is Shape.ADVERSARIAL
    assert c.est_agents == SelectorThresholds().verifier_n


def test_exhaustive_is_loop_until_done():
    assert select_pattern(PatternSignals(exhaustive=True)).shape is Shape.LOOP_UNTIL_DONE


def test_subjective_is_tournament():
    assert select_pattern(PatternSignals(subjective=True)).shape is Shape.TOURNAMENT


def test_open_ended_is_generate_filter():
    assert select_pattern(PatternSignals(open_ended=True)).shape is Shape.GENERATE_FILTER


def test_item_batch_fans_out():
    c = select_pattern(PatternSignals(item_count=20))
    assert c.shape is Shape.FAN_OUT
    assert c.est_agents == SelectorThresholds().max_fan_width  # capped at max_fan_width


def test_small_batch_does_not_fan_out():
    # below fan_out_min_items -> stays single
    assert select_pattern(PatternSignals(item_count=2)).shape is Shape.SINGLE


def test_triage_task_type_classifies():
    assert select_pattern(PatternSignals(task_type="TRIAGE")).shape is Shape.CLASSIFY_ACT


# --- precedence + thresholds --------------------------------------------------

def test_consequence_beats_item_count():
    # a high-consequence batch verifies rather than blindly fanning out
    assert select_pattern(PatternSignals(item_count=50, consequence="HIGH")).shape is Shape.ADVERSARIAL


def test_thresholds_override_fan_out_floor():
    thr = SelectorThresholds(fan_out_min_items=10)
    assert select_pattern(PatternSignals(item_count=5), thr).shape is Shape.SINGLE
    assert select_pattern(PatternSignals(item_count=12), thr).shape is Shape.FAN_OUT


# --- budget guard -------------------------------------------------------------

def test_budget_guard_downgrades_unaffordable_shape():
    # verifier_n(3) * min_budget_per_agent(2048) = 6144; a 3000 budget can't afford it
    c = select_pattern(PatternSignals(consequence="HIGH", token_budget=3000))
    assert c.shape is Shape.SINGLE
    assert c.downgraded_from is Shape.ADVERSARIAL


def test_generous_budget_keeps_shape():
    c = select_pattern(PatternSignals(consequence="HIGH", token_budget=100_000))
    assert c.shape is Shape.ADVERSARIAL
    assert c.downgraded_from is None


def test_unknown_budget_is_affordable():
    # budget 0 (unknown) must not block a shape
    assert select_pattern(PatternSignals(item_count=20, token_budget=0)).shape is Shape.FAN_OUT


# --- guidance block -----------------------------------------------------------

def test_guidance_block_names_every_shape():
    block = render_guidance_block()
    assert "## Orchestration shapes" in block
    for shape in Shape:
        assert shape.value in block
    assert "describe-only" in block.lower()


def test_guidance_block_reflects_thresholds():
    block = render_guidance_block(SelectorThresholds(fan_out_min_items=7, verifier_n=5))
    assert "7" in block and "5" in block


# --- describe-only wiring in the system prompt --------------------------------

def _core() -> CognitiveCore:
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


def _role(**kw) -> RoleDefinitionConfig:
    d = {
        "purpose": "Drive requests to outcomes.",
        "persona": "concise",
        "seed_context": "",
        "version": "0.1.0",
    }
    d.update(kw)
    return RoleDefinitionConfig.model_validate(d)


def test_prompt_includes_block_when_opted_in():
    prompt = _core().build_system_prompt(_role(orchestration_hint=True))
    assert "## Orchestration shapes" in prompt


def test_prompt_omits_block_by_default():
    prompt = _core().build_system_prompt(_role())
    assert "## Orchestration shapes" not in prompt
