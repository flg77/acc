"""Agentset orchestration patterns — a task-adaptive shape vocabulary + selector.

The six "agentset patterns" are orchestration *shapes* — ways to spend compute in
a structured form to buy quality, coverage, or confidence.  This module names them
and provides a pure, rule-based ``select_pattern(signals) -> PatternChoice`` that
the Assistant uses to decide *what shape* a task's work should take (it already
decides *who* acts).

P0 scope (ACC Implementation 053): **describe-only**.  Nothing here dispatches —
the arbiter still signs every ``ROLE_ASSIGN``.  ``select_pattern`` is the
deterministic reference the Assistant is guided by (via ``render_guidance_block``)
and that later phases (N-verifier majority, fan-out, tournament) reuse to *size*
real dispatch against the role's Cat-B budget.

Design: ACC Roadmap -> *Agentset orchestration patterns — the assistant as a
task-adaptive pattern selector*; ACC Implementation 053.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Shape(str, Enum):
    """The orchestration shapes the Assistant can choose between."""

    SINGLE = "single-agent"
    CLASSIFY_ACT = "classify-and-act"
    FAN_OUT = "fan-out-and-synthesize"
    ADVERSARIAL = "adversarial-verification"
    GENERATE_FILTER = "generate-and-filter"
    TOURNAMENT = "tournament"
    LOOP_UNTIL_DONE = "loop-until-done"


# One-line intent per shape — the single source for the guidance block + docs, so
# the prompt text and the code can never drift.
SHAPE_INTENT: dict[Shape, str] = {
    Shape.SINGLE: "one well-scoped step -> one agent (don't fan out a one-liner)",
    Shape.CLASSIFY_ACT: "route each item to a specialist by type (triage / mixed inbox)",
    Shape.FAN_OUT: "same work across many homogeneous items in parallel, then synthesize",
    Shape.ADVERSARIAL: "find, then have N independent verifiers confirm; keep only survivors (high-stakes)",
    Shape.GENERATE_FILTER: "produce many options, score against a rubric, keep the best (open-ended)",
    Shape.TOURNAMENT: "head-to-head judging; winners advance (subjective 'which is best')",
    Shape.LOOP_UNTIL_DONE: "repeat discovery passes, dedupe, stop when nothing new (exhaustive)",
}


CONSEQUENCE_HIGH = "HIGH"
CONSEQUENCE_MEDIUM = "MEDIUM"
CONSEQUENCE_LOW = "LOW"


@dataclass(frozen=True)
class PatternSignals:
    """What the selector reasons over.  Every field is governance-sourced (role
    config) or task-derived — none of it is model output, so the choice is
    reproducible and auditable."""

    task_type: str = ""
    item_count: int = 0  # homogeneous items in the worklist (<=1 => not a batch)
    consequence: str = CONSEQUENCE_LOW  # LOW | MEDIUM | HIGH (HIGH ~ Cat-A HIGH_CONSEQUENCE)
    open_ended: bool = False  # "give me options" / brainstorm
    subjective: bool = False  # "which is best" / naming / taste
    exhaustive: bool = False  # "find ALL ..." unknown-size discovery
    token_budget: int = 0  # the role's Cat-B token budget; 0 => unknown


@dataclass(frozen=True)
class SelectorThresholds:
    """Cat-B tunable knobs (per-collective).  Defaults are conservative so an
    unconfigured collective never over-orchestrates."""

    fan_out_min_items: int = 3  # below this, a batch isn't worth fanning out
    verifier_n: int = 3  # independent verifiers for an adversarial pass
    max_fan_width: int = 8  # cap parallel workers regardless of item_count
    min_budget_per_agent: int = 2048  # per-agent budget a shape needs to be affordable


@dataclass(frozen=True)
class PatternChoice:
    shape: Shape
    why: str
    est_agents: int = 1  # rough parallel width the shape implies (for budget sizing)
    downgraded_from: "Shape | None" = None  # set when the budget guard shrank the shape


def _affordable(est_agents: int, signals: PatternSignals, thr: SelectorThresholds) -> bool:
    """A shape is affordable when the role's Cat-B budget covers its fan-width.
    Unknown budget (0) is treated as affordable — don't block on missing info."""
    if signals.token_budget <= 0:
        return True
    return signals.token_budget >= est_agents * thr.min_budget_per_agent


def select_pattern(
    signals: PatternSignals,
    thresholds: SelectorThresholds | None = None,
) -> PatternChoice:
    """Pick the cheapest orchestration shape that meets the task's bar.

    Pure + deterministic.  Order matters: the most-specific signal wins, and the
    default is always a single agent (anti-over-orchestration).  When the chosen
    shape can't be afforded within the Cat-B budget it is downgraded to a single
    agent and ``downgraded_from`` records the original — the selector never
    proposes a shape the budget can't pay for (ACC Impl 053 risk mitigation).
    """
    thr = thresholds or SelectorThresholds()

    # 1. High-consequence -> adversarial verification (a governance instrument:
    #    high-stakes findings must be independently confirmed before they stand).
    if signals.consequence == CONSEQUENCE_HIGH:
        choice = PatternChoice(
            Shape.ADVERSARIAL,
            f"high-consequence -> {thr.verifier_n} independent verifiers, majority-confirm",
            est_agents=thr.verifier_n,
        )
    # 2. Exhaustive discovery -> loop until dry (dedupe passes, stop when nothing new).
    elif signals.exhaustive:
        choice = PatternChoice(
            Shape.LOOP_UNTIL_DONE,
            "unknown-size discovery -> repeat + dedupe until a dry pass",
            est_agents=1,
        )
    # 3. Subjective 'which is best' -> tournament.
    elif signals.subjective:
        choice = PatternChoice(
            Shape.TOURNAMENT,
            "subjective comparison -> head-to-head judging, winners advance",
            est_agents=min(4, thr.max_fan_width),
        )
    # 4. Open-ended 'give me options' -> generate & filter.
    elif signals.open_ended:
        choice = PatternChoice(
            Shape.GENERATE_FILTER,
            "open-ended -> generate several options, filter to the best",
            est_agents=min(4, thr.max_fan_width),
        )
    # 5. A real batch of homogeneous items -> fan out + synthesize.
    elif signals.item_count >= thr.fan_out_min_items:
        width = min(signals.item_count, thr.max_fan_width)
        choice = PatternChoice(
            Shape.FAN_OUT,
            f"{signals.item_count} homogeneous items -> fan out to {width} workers + synthesize",
            est_agents=width,
        )
    # 6. Explicit triage/dispatch task types -> classify & act.
    elif signals.task_type.upper() in {"TRIAGE", "DISPATCH", "CLASSIFY", "ROUTE"}:
        choice = PatternChoice(
            Shape.CLASSIFY_ACT,
            "mixed inbox -> classify each item and route to a specialist",
            est_agents=1,
        )
    # 7. Default: a single agent.
    else:
        choice = PatternChoice(
            Shape.SINGLE, "one well-scoped step -> a single agent", est_agents=1
        )

    # Budget guard — never propose a shape the Cat-B budget can't afford.
    if not _affordable(choice.est_agents, signals, thr):
        return PatternChoice(
            Shape.SINGLE,
            (
                f"{choice.shape.value} needs ~{choice.est_agents} agents "
                f"(> budget {signals.token_budget}); downgraded to single"
            ),
            est_agents=1,
            downgraded_from=choice.shape,
        )
    return choice


def render_guidance_block(thresholds: SelectorThresholds | None = None) -> str:
    """The static prompt block that teaches a role the shape vocabulary + when to
    pick each.  Appended to the system prompt when ``orchestration_hint: true``
    (describe-only; the role NAMES the shape it picks in its reasoning — it does
    NOT emit a new marker).  Single-sourced from ``SHAPE_INTENT`` so the block and
    the code never drift.
    """
    thr = thresholds or SelectorThresholds()
    lines = [
        "## Orchestration shapes",
        (
            "Before you act, decide *what shape* the work should take — the "
            "cheapest shape that meets the task's quality/confidence bar — and "
            "NAME it in your reasoning. This is describe-only: do NOT emit a new "
            "marker for it; keep using the normal PROPOSE_* markers to act."
        ),
        "",
    ]
    for shape in Shape:
        lines.append(f"- **{shape.value}** — {SHAPE_INTENT[shape]}")
    lines.append("")
    lines.append(
        f"Defaults: fan out only at >= {thr.fan_out_min_items} homogeneous items "
        f"(cap {thr.max_fan_width}); high-consequence findings get "
        f"{thr.verifier_n} independent verifiers; a one-liner stays a single "
        "agent. Size the shape to your Cat-B token budget — never pick a shape "
        "the budget can't pay for."
    )
    return "\n".join(lines)
