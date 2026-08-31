"""The packer: what fits, what is dropped, and what is never touched.

The load-bearing test in this file is
``test_a_fitting_prompt_is_byte_identical_to_the_legacy_join``. Everything else
here is about behaviour under pressure, but that one is about behaviour under
*no* pressure -- and it is what lets this be wired into
``_compose_user_content`` without changing a single prompt on a deployment that
was never overflowing. Without it the change is a rewrite of every prompt ACC
sends, which is not what was proposed.

Change: ``openspec/changes/20260826-context-budget`` Phase 1.2.
"""

from __future__ import annotations

import pytest

from acc.context_budget import (
    KIND_EPISODES,
    KIND_NOTES,
    KIND_TASK,
    KIND_THREAD,
    Block,
    ContextOverflow,
    block_caps,
    estimate_tokens,
    pack,
    posture_for,
    resolve_ceiling,
    standard_blocks,
)

NOTES_H = "MEMORY_NOTES (durable lessons from your past work):"
EPS_H = "RECENT_RELEVANT_EPISODES (your past work, most-similar first):"
EPS_F = "(Use these to ground your answer.)"
THR_H = "EARLIER_TURNS_OF_THIS_CONVERSATION (context only):"


def _blocks(task="do the thing", notes=2, episodes=2, thread=2, item="x " * 5):
    return standard_blocks(
        task=task,
        notes_heading=NOTES_H,
        notes_items=[f"- note {i} {item}" for i in range(notes)],
        episodes_heading=EPS_H,
        episodes_items=[f"- [1{i}:00] [T] episode {i} {item}" for i in range(episodes)],
        episodes_footer=EPS_F,
        thread_heading=THR_H,
        thread_items=[f"operator: turn {i} {item}" for i in range(thread)],
    )


def _one_token_per_item(text: str) -> int:
    """A predictable estimator, so budgets can be reasoned about exactly.

    The real heuristic is deliberately approximate; using it here would make
    every threshold in these tests a guess about its calibration.
    """
    return max(1, len(text.split("\n")))


class TestTheInvariantThatMakesWiringSafe:
    def test_a_fitting_prompt_is_byte_identical_to_the_legacy_join(self):
        """No pressure, no change. The whole basis for a safe rollout."""
        legacy = "\n\n".join([
            "\n".join([NOTES_H, "- note 0 a", "- note 1 a"]),
            "\n".join([EPS_H, "- ep 0 a", EPS_F]),
            "\n".join([THR_H, "operator: t0", "operator: t1"]),
            "do the thing",
        ])
        blocks = standard_blocks(
            task="do the thing",
            notes_heading=NOTES_H, notes_items=["- note 0 a", "- note 1 a"],
            episodes_heading=EPS_H, episodes_items=["- ep 0 a"], episodes_footer=EPS_F,
            thread_heading=THR_H, thread_items=["operator: t0", "operator: t1"],
        )
        result = pack(blocks, ceiling=1_000_000)

        assert result.text == legacy
        assert result.degraded is False
        assert result.drops == ()

    def test_display_order_is_notes_episodes_thread_task(self):
        result = pack(_blocks(), ceiling=1_000_000)
        positions = [
            result.text.index(NOTES_H),
            result.text.index(EPS_H),
            result.text.index(THR_H),
            result.text.index("do the thing"),
        ]
        assert positions == sorted(positions)

    def test_an_empty_block_contributes_nothing(self):
        """Empty must be indistinguishable from absent.

        Otherwise a task that retrieved no episodes produces a different shape
        from one that never looked.
        """
        result = pack(standard_blocks(task="only the task"), ceiling=1000)
        assert result.text == "only the task"

    def test_packing_is_deterministic(self):
        a = pack(_blocks(), ceiling=40, estimator=_one_token_per_item)
        b = pack(_blocks(), ceiling=40, estimator=_one_token_per_item)
        assert a.text == b.text and a.drops == b.drops


class TestTheTaskIsSacred:
    def test_an_oversized_task_raises_rather_than_truncating(self):
        blocks = standard_blocks(task="word " * 500)
        with pytest.raises(ContextOverflow) as exc:
            pack(blocks, ceiling=10)

        assert exc.value.ceiling == 10
        assert exc.value.need > 10
        assert "will not truncate" in str(exc.value)

    def test_the_task_survives_when_everything_else_is_evicted(self):
        result = pack(_blocks(), ceiling=3, estimator=_one_token_per_item)
        assert result.text == "do the thing"
        assert result.kept == {KIND_TASK: 1}
        assert result.degraded is True

    def test_the_task_text_is_never_altered(self):
        task = "  ragged\n  multiline\n  request  "
        result = pack(standard_blocks(task=task), ceiling=1000)
        assert result.text == task


class TestEvictionOrder:
    def test_episodes_go_before_notes_which_go_before_thread(self):
        """Losing the previous turn makes the current one incoherent.

        Losing an episode only makes the answer less informed, so episodes are
        the cheapest thing to give up and the thread the dearest.
        """
        survivors = []
        for ceiling in range(4, 16):
            kept = pack(
                _blocks(notes=1, episodes=1, thread=1),
                ceiling=ceiling,
                estimator=_one_token_per_item,
            ).kept
            survivors.append({k for k, v in kept.items() if v})

        # The thread must never be present while episodes are absent... and
        # episodes must never appear before the thread does.
        for kinds in survivors:
            if KIND_EPISODES in kinds:
                assert KIND_THREAD in kinds, "episodes kept while the thread was dropped"
                assert KIND_NOTES in kinds, "episodes kept while notes were dropped"
            if KIND_NOTES in kinds:
                assert KIND_THREAD in kinds, "notes kept while the thread was dropped"

    def test_eviction_order_is_independent_of_display_order(self):
        """Two orders, and the rendered one must not move under pressure."""
        result = pack(
            _blocks(notes=1, episodes=1, thread=1),
            ceiling=9,
            estimator=_one_token_per_item,
        )
        if NOTES_H in result.text and THR_H in result.text:
            assert result.text.index(NOTES_H) < result.text.index(THR_H)


class TestDivisibleEviction:
    def test_eviction_within_a_block_is_greedy_not_first_miss(self):
        """One huge item must not cost the ones behind it."""
        blocks = [
            Block(kind=KIND_TASK, items=("task",), priority=0, display_rank=3,
                  divisible=False),
            Block(
                kind=KIND_EPISODES,
                items=("giant " * 200, "small a", "small b"),
                priority=3, display_rank=1, heading=EPS_H,
            ),
        ]
        result = pack(blocks, ceiling=120)

        assert "small a" in result.text and "small b" in result.text
        assert "giant" not in result.text
        assert [d.index for d in result.drops] == [0]

    def test_items_are_taken_in_the_order_supplied(self):
        """Keep-preference order is the caller's contract: thread newest-first,
        episodes highest-similarity-first. The packer must not re-sort."""
        blocks = [
            Block(kind=KIND_TASK, items=("t",), priority=0, display_rank=3,
                  divisible=False),
            Block(
                kind=KIND_EPISODES, items=("best", "middle", "worst"),
                priority=3, display_rank=1, heading=EPS_H,
            ),
        ]
        result = pack(blocks, ceiling=4, estimator=_one_token_per_item)
        assert "best" in result.text
        assert "worst" not in result.text

    def test_an_indivisible_block_is_all_or_nothing(self):
        blocks = [
            Block(kind=KIND_TASK, items=("t",), priority=0, display_rank=3,
                  divisible=False),
            Block(
                kind=KIND_THREAD, items=("a", "b", "c"), priority=1,
                display_rank=2, heading=THR_H, divisible=False,
            ),
        ]
        tight = pack(blocks, ceiling=3, estimator=_one_token_per_item)
        assert THR_H not in tight.text
        assert len(tight.drops) == 3

        # ...and the other half: when it fits, it arrives whole.
        roomy = pack(blocks, ceiling=1000, estimator=_one_token_per_item)
        assert THR_H in roomy.text
        for item in ("a", "b", "c"):
            assert item in roomy.text
        assert roomy.drops == ()
        assert roomy.kept[KIND_THREAD] == 3


class TestHeadingsAndFooters:
    def test_a_heading_appears_only_when_an_item_survives(self):
        """A dangling heading invites a small model to invent the contents."""
        result = pack(_blocks(), ceiling=3, estimator=_one_token_per_item)
        for heading in (NOTES_H, EPS_H, THR_H):
            assert heading not in result.text

    def test_the_footer_goes_with_the_block(self):
        """'Use these to ground your answer' pointing at nothing is worse than
        no footer at all."""
        result = pack(_blocks(), ceiling=3, estimator=_one_token_per_item)
        assert EPS_F not in result.text

    def test_a_block_whose_overhead_alone_exceeds_its_budget_is_dropped_whole(self):
        blocks = [
            Block(kind=KIND_TASK, items=("t",), priority=0, display_rank=3,
                  divisible=False),
            Block(
                kind=KIND_EPISODES, items=("a", "b"), priority=3, display_rank=1,
                heading="H " * 100, footer="F " * 100,
            ),
        ]
        result = pack(blocks, ceiling=60)
        assert result.text == "t"
        assert {d.reason for d in result.drops} == {"block_overhead"}

    def test_render_itself_refuses_to_emit_an_empty_block(self):
        """The guard `pack` currently short-circuits past.

        `pack` never calls `render` with an empty survivor list, so the
        contract inside `Block.render` is defensive and was untested until a
        mutation run pointed it out: removing the guard broke nothing. It is
        the only protection if the caller ever changes, so it is pinned here
        directly rather than through `pack`.
        """
        block = Block(
            kind=KIND_EPISODES, items=("a", "b"), priority=3, display_rank=1,
            heading=EPS_H, footer=EPS_F,
        )
        assert block.render([]) == ""
        assert block.render(["a"]).splitlines() == [EPS_H, "a", EPS_F]

    def test_a_block_with_no_items_at_all_renders_empty(self):
        block = Block(
            kind=KIND_NOTES, items=(), priority=2, display_rank=0, heading=NOTES_H,
        )
        assert block.render() == ""

    def test_the_footer_survives_alongside_a_kept_item(self):
        result = pack(_blocks(episodes=1), ceiling=1_000_000)
        assert EPS_H in result.text and EPS_F in result.text


class TestDropAccounting:
    def test_every_drop_is_enumerated(self):
        result = pack(_blocks(notes=3, episodes=3, thread=3), ceiling=8,
                      estimator=_one_token_per_item)
        assert result.drops
        assert sum(result.dropped.values()) == len(result.drops)
        for drop in result.drops:
            assert drop.kind and drop.est_tokens > 0 and drop.reason

    def test_degraded_is_true_exactly_when_something_was_dropped(self):
        assert pack(_blocks(), ceiling=1_000_000).degraded is False
        assert pack(_blocks(), ceiling=8, estimator=_one_token_per_item).degraded is True

    def test_a_block_cap_drop_is_distinguishable_from_a_ceiling_drop(self):
        """The difference between "this deployment is out of window" and "this
        block is being held to its share on purpose"."""
        blocks = _blocks(episodes=6)
        capped = pack(blocks, ceiling=1_000_000, caps={KIND_EPISODES: 12},
                      estimator=_one_token_per_item)
        assert capped.drops
        assert {d.reason for d in capped.drops} == {"block_cap"}

    def test_the_event_payload_carries_what_an_operator_needs(self):
        result = pack(_blocks(), ceiling=8, estimator=_one_token_per_item)
        event = result.as_event(window=8192, window_source="declared")

        assert event["window"] == 8192
        assert event["window_source"] == "declared"
        assert event["degraded"] is True
        assert event["dropped"] == len(result.drops)
        assert set(event) >= {
            "ceiling", "est_tokens", "kept", "dropped_by_kind", "calibration",
        }


class TestNothingIsCompressed:
    def test_kept_items_appear_verbatim(self):
        """The packer drops. It does not summarise, elide or ellipsise."""
        item = "- [10:00] [TASK_ASSIGN] a very specific episode excerpt"
        blocks = standard_blocks(
            task="t", episodes_heading=EPS_H, episodes_items=[item],
        )
        assert item in pack(blocks, ceiling=1_000_000).text

    def test_no_ellipsis_is_introduced(self):
        result = pack(_blocks(notes=4, episodes=4, thread=4), ceiling=10,
                      estimator=_one_token_per_item)
        assert "..." not in result.text and "…" not in result.text


class TestTheCeiling:
    def test_the_output_reserve_is_subtracted(self):
        """A budgeter that fills the window leaves nothing to answer with."""
        posture = posture_for("edge")
        ceiling = resolve_ceiling(8192, posture=posture)
        assert ceiling < 8192 - posture.reserve_output

    def test_the_measured_system_prompt_is_subtracted(self):
        posture = posture_for("rhoai")
        without = resolve_ceiling(32768, posture=posture)
        with_system = resolve_ceiling(32768, posture=posture, system_tokens=1500)
        assert without - with_system == 1500

    def test_the_margin_never_falls_below_a_floor(self):
        """A percentage of a small window rounds to nothing."""
        tiny = resolve_ceiling(100, posture=posture_for("edge"))
        assert tiny >= 1

    def test_a_nonsensical_window_still_yields_a_positive_ceiling(self):
        """So an overflow reports real numbers instead of a negative budget
        that silently drops everything."""
        assert resolve_ceiling(10, posture=posture_for("rhoai")) >= 1


class TestPosture:
    def test_edge_is_stricter_than_the_datacenter_on_every_axis(self):
        edge, dc = posture_for("edge"), posture_for("rhoai")
        assert edge.margin_pct > dc.margin_pct
        assert edge.reserve_output < dc.reserve_output
        assert edge.caps[KIND_EPISODES] < dc.caps[KIND_EPISODES]
        assert edge.shares[KIND_EPISODES] < dc.shares[KIND_EPISODES]

    def test_an_unknown_mode_gets_the_conservative_posture(self):
        """Too tight costs recall; too loose costs integrity. Not symmetric."""
        assert posture_for("nonsense").name == "edge"
        assert posture_for("").name == "edge"

    def test_posture_does_not_influence_the_window(self):
        """The rule the whole design rests on (REQ-CAP-004).

        An edge box can serve a 128k model and a datacenter can serve a 4k one.
        Only the *derived* budget may differ.
        """
        for mode in ("edge", "standalone", "rhoai"):
            assert resolve_ceiling(8192, posture=posture_for(mode)) < 8192

    def test_shares_bind_on_a_small_window_and_caps_on_a_large_one(self):
        """One expression, most of the edge/datacenter difference."""
        posture = posture_for("rhoai")
        small = block_caps(4096, posture)
        large = block_caps(400_000, posture)

        assert small[KIND_EPISODES] == int(4096 * posture.shares[KIND_EPISODES])
        assert large[KIND_EPISODES] == posture.caps[KIND_EPISODES]


class TestTheEstimator:
    def test_it_needs_no_tokenizer_and_no_network(self):
        assert estimate_tokens("hello world") > 0

    def test_empty_text_costs_nothing(self):
        assert estimate_tokens("") == 0

    def test_it_grows_with_length(self):
        assert estimate_tokens("word " * 100) > estimate_tokens("word " * 10)

    def test_punctuation_heavy_text_costs_more_than_prose_of_equal_length(self):
        """ACC sends code and YAML, not prose; chars/4 under-counts both."""
        prose = "the quick brown fox jumps over it"
        code = 'x={"a":[1,2],"b":(3,4)}; y=x["a"]'
        assert estimate_tokens(code) > estimate_tokens(prose)

    def test_non_ascii_costs_more_than_ascii(self):
        assert estimate_tokens("日本語のテキストです") > estimate_tokens("abcdefghij")

    def test_calibration_scales_the_estimate(self):
        base = estimate_tokens("word " * 100)
        assert estimate_tokens("word " * 100, calibration=2.0) > base
        assert estimate_tokens("word " * 100, calibration=0.5) < base

    def test_a_custom_estimator_is_honoured(self):
        result = pack(_blocks(), ceiling=6, estimator=_one_token_per_item)
        assert result.est_tokens == _one_token_per_item(result.text)


class TestPurity:
    def test_the_input_blocks_are_not_mutated(self):
        blocks = _blocks()
        before = [(b.kind, b.items) for b in blocks]
        pack(blocks, ceiling=6, estimator=_one_token_per_item)
        assert [(b.kind, b.items) for b in blocks] == before

    def test_a_missing_task_block_is_a_programming_error(self):
        with pytest.raises(ValueError, match="task block"):
            pack([Block(kind=KIND_NOTES, items=("a",), priority=2, display_rank=0)],
                 ceiling=100)
