"""acc.orchestration — task-adaptive agentset orchestration shapes (P0).

A small library naming the six orchestration *shapes* the Assistant can choose
between per task, plus a pure ``select_pattern`` heuristic and the static prompt
``render_guidance_block`` that teaches a role the vocabulary.  P0 is describe-only
(no dispatch); see ``acc/orchestration/patterns.py`` and ACC Implementation 053.
"""
from acc.orchestration.patterns import (
    SHAPE_INTENT,
    PatternChoice,
    PatternSignals,
    SelectorThresholds,
    Shape,
    render_guidance_block,
    select_pattern,
)

__all__ = [
    "Shape",
    "PatternSignals",
    "PatternChoice",
    "SelectorThresholds",
    "select_pattern",
    "render_guidance_block",
    "SHAPE_INTENT",
]
