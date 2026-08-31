"""Deciding what fits in the window, and recording what did not.

ACC assembled every prompt without knowing how large the context window is.
``_compose_user_content`` (``acc/cognitive_core.py``) joined four blocks --
MEMORY_NOTES, RECENT_RELEVANT_EPISODES, EARLIER_TURNS, the task -- with a bare
``"\\n\\n".join`` and no ceiling anywhere in the path, and the token count was
learned from ``usage`` *after* the call had been paid for.  What happened past
the ceiling was decided by the server: an OpenAI-compatible endpoint rejects
with a 400, Ollama trims to ``num_ctx``.  Neither is a policy ACC chose, and
one of them is silent.

This module is the ceiling and the choice.  It is **not** compaction: it drops
whole items and enumerates every one.  Compaction is a separate mechanism that
consumes the deficit this produces (``design.md`` Phase 5), and it is
deliberately second, because summarising a small model's history with that same
small model compounds its errors and puts an unreviewed transformation between
the durable log and the agent's view.  Dropping visibly beats compressing
invisibly.

Four properties carry the design.

**Pure.**  No I/O, no ``await``, no clock, no global state, no mutation of the
arguments -- the same discipline ``acc/estimator.py`` holds, and for the same
reason: this runs on the dispatch hot path.  It follows that :func:`pack` is
deterministic, which is what makes the byte-identity regression test possible.

**The task is never truncated.**  Silently trimming the operator's request is
the one failure this exists to prevent, so it is the one input the packer will
not touch.  If the task alone will not fit, :class:`ContextOverflow` is raised
naming both numbers.  Failing loudly is the correct direction here: the
alternative is a server quietly deleting the request while the tracelog records
it in full.

**Two orders, deliberately separate.**  ``priority`` decides what is evicted;
``display_rank`` decides where surviving blocks are rendered.  Conflating them
is the common implementation error and it produces a prompt whose order changes
under pressure.  Eviction runs task > thread > notes > episodes, because losing
the previous turn makes the current one *incoherent* -- "yes, do that" with no
antecedent is unanswerable -- while losing an episode only makes the answer less
informed.  Display order is unchanged from the pre-existing assembly, so a
prompt that fits is byte-identical to the one ACC sends today.

**Fitting is not the goal; signal density is.**  A global ceiling alone would
admit five 8k-character episodes on a 128k model purely *because they fit*,
which is a quality regression dressed as generosity -- and small models degrade
measurably on irrelevant context, so the cost is highest exactly where the
window is largest relative to need.  Hence per-block caps as well:
``min(share x ceiling, absolute_cap)``.  On edge the shares bind; in a
datacenter the absolute caps do.  That one expression is most of the
edge/datacenter difference.

The posture (shares, reserve, margin) comes from ``deploy_mode``.  The ceiling
comes from the model.  **Never infer the window from the mode** -- an edge box
can serve a 128k model and a datacenter can serve a 4k one, and collapsing the
two axes produces a budgeter that is confidently wrong in exactly the
deployments this was written for.

Change: ``openspec/changes/20260826-context-budget`` Phase 1.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Sequence

__all__ = [
    "Block",
    "ContextBudget",
    "BudgetResult",
    "ContextOverflow",
    "Drop",
    "Posture",
    "block_caps",
    "estimate_tokens",
    "pack",
    "posture_for",
    "resolve_ceiling",
    "standard_blocks",
    "KIND_EPISODES",
    "KIND_NOTES",
    "KIND_TASK",
    "KIND_THREAD",
]

KIND_TASK = "task"
KIND_THREAD = "thread"
KIND_NOTES = "notes"
KIND_EPISODES = "episodes"

#: Cost of the newline that joins two items inside a block.
_JOIN_COST = 1
#: Cost of the blank line that joins two blocks.
_BLOCK_JOIN_COST = 2

#: Characters that tend to become their own token.  Deliberately not every
#: punctuation mark -- this is a correction term, not a tokenizer.
_PUNCT_CLASS = set("{}[]()<>/\\|`\"'=+*_#@$%^&~:;,.!?-")


class ContextOverflow(RuntimeError):
    """The operator's task alone exceeds the ceiling.

    Raised rather than handled, because every alternative is worse: trimming
    the task deletes the request, and dropping it silently produces an agent
    answering a question nobody asked.  Carries both numbers so the message can
    say how much too large it was.
    """

    def __init__(self, need: int, ceiling: int) -> None:
        self.need = need
        self.ceiling = ceiling
        super().__init__(
            f"the task alone needs ~{need} tokens but the budget is {ceiling}; "
            "ACC will not truncate an operator's request"
        )


@dataclass(frozen=True)
class Block:
    """One labelled section of the user message.

    ``items`` are in **keep-preference order, best first** -- thread turns
    newest-first, episodes highest-similarity-first, notes as curated -- because
    eviction walks the list forwards and stops paying at the first item that
    does not fit.
    """

    kind: str
    items: tuple[str, ...]
    priority: int
    display_rank: int
    heading: str = ""
    footer: str = ""
    divisible: bool = True

    def render(self, keep: Sequence[str] | None = None) -> str:
        """The block as it appears in the prompt.

        A heading is emitted **iff at least one item survives**, and the footer
        with it.  A dangling ``RECENT_RELEVANT_EPISODES:`` with nothing under it
        invites a small model to invent the contents, and a trailing "use these
        to ground your answer" pointing at nothing is worse still.
        """
        items = list(self.items if keep is None else keep)
        if not items:
            return ""
        lines = ([self.heading] if self.heading else []) + items
        if self.footer:
            lines.append(self.footer)
        return "\n".join(lines)


@dataclass(frozen=True)
class Drop:
    """One item that did not fit, and why.  A drop is never silent."""

    kind: str
    index: int
    est_tokens: int
    reason: str  # "ceiling" | "block_cap" | "block_overhead"


@dataclass(frozen=True)
class BudgetResult:
    text: str
    est_tokens: int
    ceiling: int
    kept: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)
    drops: tuple[Drop, ...] = ()
    calibration: float = 1.0

    @property
    def degraded(self) -> bool:
        """True iff something was dropped.  The operator-visible signal that a
        deployment is running out of window -- invisible before this existed."""
        return bool(self.drops)

    def as_event(self, *, window: int, window_source: str) -> dict:
        """The payload for ``acc.pipeline.context_budget``."""
        return {
            "window": window,
            "window_source": window_source,
            "ceiling": self.ceiling,
            "est_tokens": self.est_tokens,
            "kept": dict(self.kept),
            "dropped": len(self.drops),
            "dropped_by_kind": dict(self.dropped),
            "degraded": self.degraded,
            "calibration": self.calibration,
        }


# ---------------------------------------------------------------------------
# Estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str, calibration: float = 1.0) -> int:
    """Approximate the token count of *text* without a tokenizer.

    ``chars / 4`` is the usual shortcut and it is wrong for what ACC actually
    sends, which is largely code, YAML and command output.  This weights
    whitespace-delimited words and adds a surcharge for the two classes that
    fragment: punctuation runs, and non-ASCII (where a single character is
    routinely two or three tokens).

    It is an estimate, and the safety margin in :func:`resolve_ceiling` is what
    pays for that.  ``calibration`` is where a measured correction goes -- the
    ratio of the backend's reported ``usage.prompt_tokens`` to what this
    returned -- so the estimate can be tuned per model without a tokenizer
    dependency on an edge box.  Wiring that feedback is Phase 1.3; the hook
    exists here so the shape does not change when it lands.

    No tokenizer means no dependency, no model download, and no drift between
    the tokenizer ACC loaded and the one the server is actually using.
    """
    if not text:
        return 0
    words = len(text.split())
    punct = 0
    non_ascii = 0
    for char in text:
        if char in _PUNCT_CLASS:
            punct += 1
        elif ord(char) > 0x7F:
            non_ascii += 1
    raw = words * 1.3 + punct * 0.5 + non_ascii * 0.8
    return max(1, int(raw * calibration) + 1)


Estimator = Callable[[str], int]


# ---------------------------------------------------------------------------
# Posture and the ceiling
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Posture:
    """What is scarce here, which is not the same as how big the window is.

    Selected by ``deploy_mode``.  The *ceiling* still comes from the model --
    see the module docstring on why the two axes must not be collapsed.
    """

    name: str
    reserve_output: int
    margin_pct: float
    shares: dict[str, float]
    caps: dict[str, int]


#: Edge is the constrained case in a way the numbers alone do not show: the KV
#: cache is a **server-wide** resource shared by every concurrent agent, and
#: there is no frontier model to fail over to.  Distraction also costs more --
#: a 3B model degrades measurably on irrelevant context -- so the shares are
#: tighter than the window alone would justify.
_POSTURES: dict[str, Posture] = {
    "edge": Posture(
        name="edge",
        reserve_output=512,
        margin_pct=0.20,
        shares={KIND_THREAD: 0.30, KIND_EPISODES: 0.15, KIND_NOTES: 0.10},
        caps={KIND_THREAD: 4000, KIND_EPISODES: 1200, KIND_NOTES: 600},
    ),
    "standalone": Posture(
        name="standalone",
        reserve_output=1024,
        margin_pct=0.15,
        shares={KIND_THREAD: 0.35, KIND_EPISODES: 0.25, KIND_NOTES: 0.10},
        caps={KIND_THREAD: 8000, KIND_EPISODES: 3000, KIND_NOTES: 1200},
    ),
    "rhoai": Posture(
        name="rhoai",
        reserve_output=2048,
        margin_pct=0.10,
        shares={KIND_THREAD: 0.35, KIND_EPISODES: 0.25, KIND_NOTES: 0.10},
        caps={KIND_THREAD: 16000, KIND_EPISODES: 6000, KIND_NOTES: 2400},
    ),
}


@dataclass(frozen=True)
class ContextBudget:
    """A resolved budget for one deployment: how big, and how that was known.

    ``source`` is carried so a report can distinguish a number the operator
    declared from one ACC fell back to.  An 8192 that came from
    ``ACC_CONTEXT_WINDOW_DEFAULT`` because nothing was configured is a very
    different fact from an 8192 the operator measured and wrote down, and a
    stage event that blurred them would be describing a decision nobody made.
    """

    window: int
    ceiling: int
    posture: Posture
    source: str  # "declared" | "override" | "default"


def posture_for(
    deploy_mode: str,
    *,
    reserve_output: int | None = None,
    margin_pct: float | None = None,
) -> Posture:
    """The posture for *deploy_mode*, defaulting to the most conservative.

    An unrecognised mode gets ``edge`` rather than a permissive default: being
    too tight costs recall, being too loose costs integrity, and those are not
    symmetric.

    The two overrides are the site-specific halves of the table -- how much is
    held back for the answer, and how much for estimator error -- supplied by
    ``ContextBudgetConfig`` / ``ACC_CONTEXT_RESERVE_OUTPUT`` /
    ``ACC_CONTEXT_SAFETY_MARGIN``.  They are applied **here**, to the returned
    posture, rather than at :func:`resolve_ceiling`, so that the posture a
    stage event reports is the one the ceiling was actually computed from.  A
    record naming ``reserve_output=512`` while 256 was used would be describing
    a decision nobody made -- the same reason :class:`ContextBudget` carries
    ``source``.

    ``None`` means "not overridden" and is distinct from ``0``: the config
    layer maps its own ``0`` sentinel to ``None`` before calling, so an
    unset field and a deliberate zero never collapse into each other here.
    """
    base = _POSTURES.get((deploy_mode or "").strip().lower(), _POSTURES["edge"])
    if reserve_output is None and margin_pct is None:
        return base
    return replace(
        base,
        name=f"{base.name}+custom",
        reserve_output=base.reserve_output if reserve_output is None else reserve_output,
        margin_pct=base.margin_pct if margin_pct is None else margin_pct,
    )


def resolve_ceiling(
    window: int,
    *,
    posture: Posture,
    system_tokens: int = 0,
    reserve_output: int | None = None,
    margin_pct: float | None = None,
) -> int:
    """How many tokens the user message may occupy.

    ``ceiling = window - reserve_output - system_tokens - safety_margin``

    ``reserve_output`` is the half everyone forgets: a budgeter that fills the
    window leaves the model nothing to answer with.

    ``system_tokens`` is a **measured constant**, not an estimate, because
    PR-CA1 made the system prompt stable per role -- so it can be counted once
    and memoised.  The prefix-cache discipline pays for itself twice: once in
    cache hits, once in making the largest term of this subtraction exact.

    Always at least 1, so a misconfigured window produces a
    :class:`ContextOverflow` naming real numbers rather than a negative budget
    that silently drops everything.
    """
    reserve = posture.reserve_output if reserve_output is None else reserve_output
    pct = posture.margin_pct if margin_pct is None else margin_pct
    margin = max(64, int(window * pct))
    return max(1, window - reserve - system_tokens - margin)


def block_caps(ceiling: int, posture: Posture) -> dict[str, int]:
    """Per-block ceilings: ``min(share x ceiling, absolute_cap)``.

    The global ceiling alone is not enough.  See the module docstring: fitting
    is not the goal.
    """
    return {
        kind: max(0, min(int(ceiling * share), posture.caps.get(kind, ceiling)))
        for kind, share in posture.shares.items()
    }


# ---------------------------------------------------------------------------
# Block construction
# ---------------------------------------------------------------------------

def standard_blocks(
    *,
    task: str,
    notes_heading: str = "",
    notes_items: Sequence[str] = (),
    episodes_heading: str = "",
    episodes_items: Sequence[str] = (),
    episodes_footer: str = "",
    thread_heading: str = "",
    thread_items: Sequence[str] = (),
) -> list[Block]:
    """The four blocks ACC actually assembles, with the orders already set.

    The priority and display constants live here rather than at the call site
    so there is one place that knows the answer, and so a caller cannot get the
    eviction order subtly wrong while looking correct.
    """
    return [
        Block(
            kind=KIND_TASK, items=(task,), priority=0, display_rank=3,
            divisible=False,
        ),
        Block(
            kind=KIND_THREAD, items=tuple(thread_items), priority=1,
            display_rank=2, heading=thread_heading,
        ),
        Block(
            kind=KIND_NOTES, items=tuple(notes_items), priority=2,
            display_rank=0, heading=notes_heading,
        ),
        Block(
            kind=KIND_EPISODES, items=tuple(episodes_items), priority=3,
            display_rank=1, heading=episodes_heading, footer=episodes_footer,
        ),
    ]


# ---------------------------------------------------------------------------
# The packer
# ---------------------------------------------------------------------------

def pack(
    blocks: Iterable[Block],
    ceiling: int,
    *,
    caps: dict[str, int] | None = None,
    estimator: Estimator | None = None,
    calibration: float = 1.0,
) -> BudgetResult:
    """Fit *blocks* under *ceiling*, dropping in priority order.

    Raises:
        ContextOverflow: when the task alone exceeds *ceiling*.
    """
    est: Estimator = estimator or (lambda t: estimate_tokens(t, calibration))
    caps = dict(caps or {})
    block_list = list(blocks)

    task_blocks = [b for b in block_list if b.kind == KIND_TASK]
    if not task_blocks:
        raise ValueError("pack() requires a task block")
    task = task_blocks[0]

    task_text = task.render()
    task_tokens = est(task_text)
    if task_tokens > ceiling:
        raise ContextOverflow(need=task_tokens, ceiling=ceiling)

    kept: dict[str, int] = {KIND_TASK: 1}
    dropped: dict[str, int] = {}
    drops: list[Drop] = []
    rendered: list[tuple[int, str]] = [(task.display_rank, task_text)]

    remaining = ceiling - task_tokens

    for block in sorted(
        (b for b in block_list if b.kind != KIND_TASK), key=lambda b: b.priority
    ):
        if not block.items:
            continue

        cap = caps.get(block.kind, remaining)
        budget = min(remaining, cap)
        # Which of the two bound this block decides the drop reason, and the
        # reason is the difference between "this deployment is out of window"
        # and "this block is being held to its share on purpose".
        limited_by_cap = cap < remaining

        # The heading and footer are structural: they cost tokens and they are
        # meaningless without at least one item, so they are charged before any
        # item is admitted and refunded (by dropping the block) if none is.
        overhead = est(block.heading) if block.heading else 0
        overhead += est(block.footer) if block.footer else 0

        if not block.divisible:
            whole = block.render()
            cost = est(whole)
            if cost <= budget:
                rendered.append((block.display_rank, whole))
                kept[block.kind] = len(block.items)
                remaining -= cost + _BLOCK_JOIN_COST
            else:
                for index, item in enumerate(block.items):
                    drops.append(Drop(block.kind, index, est(item), "ceiling"))
                dropped[block.kind] = len(block.items)
            continue

        if overhead >= budget:
            # No item could survive alongside its own heading, so the whole
            # block goes rather than leaving a heading over nothing.
            for index, item in enumerate(block.items):
                drops.append(
                    Drop(block.kind, index, est(item), "block_overhead")
                )
            dropped[block.kind] = len(block.items)
            continue

        spent = overhead
        survivors: list[str] = []
        for index, item in enumerate(block.items):
            cost = est(item) + _JOIN_COST
            if spent + cost <= budget:
                survivors.append(item)
                spent += cost
                continue
            # Greedy, not first-miss-abandon: one very long episode at rank 1
            # must not silently cost the four relevant ones behind it.
            drops.append(
                Drop(
                    block.kind,
                    index,
                    cost,
                    "block_cap" if limited_by_cap else "ceiling",
                )
            )
            dropped[block.kind] = dropped.get(block.kind, 0) + 1

        if survivors:
            rendered.append((block.display_rank, block.render(survivors)))
            kept[block.kind] = len(survivors)
            remaining -= spent + _BLOCK_JOIN_COST
        else:
            # Every item lost; the overhead is not spent because nothing is
            # rendered.  Already accounted in `drops` above.
            pass

    text = "\n\n".join(part for _, part in sorted(rendered, key=lambda p: p[0]))
    return BudgetResult(
        text=text,
        est_tokens=est(text),
        ceiling=ceiling,
        kept=kept,
        dropped=dropped,
        drops=tuple(drops),
        calibration=calibration,
    )
