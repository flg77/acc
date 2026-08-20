"""Objectives that persist, under limits the operator sets.

Two governance rules carry this, and both are tested as refusals rather than
as behaviours — because the failure mode in each case is silent.

**A ceiling is mandatory.** "Until met", judged by the agent, is not a
termination condition; it is the absence of one, and it is how a persistent
objective becomes an unbounded spend nobody authorised. Creation without a
ceiling is refused.

**An objective does not raise the autonomy level.** A gated action inside an
objective is still gated and the objective *waits*. If it could proceed,
"pursue this across turns" would be a way to obtain approval-free execution by
phrasing, and the governed path would be the one that was easy to avoid.

The ceiling is checked BEFORE a turn is counted. An objective that notices it
is over budget after spending has not been bounded — it has been observed.
"""

from __future__ import annotations

import json
import time

import pytest

from acc import objectives as O
from acc.objectives import State


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(O.STORE_PATH_VAR, str(tmp_path / "objectives.json"))
    return tmp_path


# --------------------------------------------------------------------------
# A ceiling is mandatory
# --------------------------------------------------------------------------


class TestCeilingIsMandatory:
    def test_creation_without_a_ceiling_is_refused(self, store):
        with pytest.raises(O.ObjectiveError, match="must declare a ceiling"):
            O.create("keep dependencies current")

    def test_the_refusal_explains_why(self, store):
        with pytest.raises(O.ObjectiveError, match="unbounded spend"):
            O.create("do a thing")

    @pytest.mark.parametrize(
        "kwargs",
        [{"max_turns": 5}, {"max_tokens": 1000}, {"max_seconds": 60}],
    )
    def test_any_single_limit_is_enough(self, store, kwargs):
        assert O.create("bounded", **kwargs).ceiling.declared()

    def test_an_empty_statement_is_refused(self, store):
        with pytest.raises(O.ObjectiveError, match="needs a statement"):
            O.create("   ", max_turns=1)


# --------------------------------------------------------------------------
# The ceiling actually stops it
# --------------------------------------------------------------------------


class TestCeilingStops:
    def test_turns_are_bounded_and_the_stop_is_recorded(self, store):
        objective = O.create("bounded", max_turns=2)
        O.claim_turn(objective.id)
        O.claim_turn(objective.id)

        with pytest.raises(O.ObjectiveError, match="turn ceiling"):
            O.claim_turn(objective.id)

        stored = O.load()[objective.id]
        assert stored.state == State.STOPPED
        assert "turn ceiling" in stored.stop_reason

    def test_the_ceiling_is_checked_before_the_turn_is_counted(self, store):
        """Noticing after the spend is observation, not a bound."""
        objective = O.create("bounded", max_turns=1)
        O.claim_turn(objective.id)
        assert O.load()[objective.id].consumption.turns == 1

        with pytest.raises(O.ObjectiveError):
            O.claim_turn(objective.id)
        assert O.load()[objective.id].consumption.turns == 1, (
            "a refused turn must not be counted"
        )

    def test_tokens_are_bounded(self, store):
        objective = O.create("bounded", max_tokens=100)
        O.record_usage(objective.id, 60)
        assert O.load()[objective.id].state == State.ACTIVE
        O.record_usage(objective.id, 60)

        stored = O.load()[objective.id]
        assert stored.state == State.STOPPED
        assert "token ceiling" in stored.stop_reason

    def test_wall_clock_is_bounded(self, store):
        objective = O.create("bounded", max_seconds=60)
        future = objective.consumption.started_at + 120
        assert "time ceiling" in objective.exhausted(now=future)

    def test_a_stopped_objective_cannot_resume(self, store):
        """Raising a ceiling must be a deliberate new decision."""
        objective = O.create("bounded", max_turns=1)
        O.claim_turn(objective.id)
        with pytest.raises(O.ObjectiveError):
            O.claim_turn(objective.id)

        with pytest.raises(O.ObjectiveError, match="only a paused objective resumes"):
            O.resume(objective.id)

    def test_a_paused_objective_past_its_ceiling_cannot_resume(self, store):
        """The other route back to running, closed for the same reason.

        Pausing before the ceiling and resuming after it would be a way to
        spend past a bound without anyone raising it.
        """
        objective = O.create("bounded", max_seconds=60)
        O.pause(objective.id)

        stored = O.load()
        stored[objective.id].consumption.started_at = time.time() - 3600
        O.save(stored)

        with pytest.raises(O.ObjectiveError, match="cannot resume"):
            O.resume(objective.id)


# --------------------------------------------------------------------------
# It does not raise the autonomy level
# --------------------------------------------------------------------------


class TestGatedActionsStayGated:
    def test_an_objective_waiting_on_oversight_cannot_take_a_turn(self, store):
        """Otherwise 'pursue across turns' would be approval-free by phrasing."""
        objective = O.create("bounded", max_turns=10)
        O.block_on_oversight(objective.id, "ov-123")

        with pytest.raises(O.ObjectiveError, match="waiting on oversight"):
            O.claim_turn(objective.id)

    def test_a_blocked_objective_is_not_runnable(self, store):
        objective = O.create("bounded", max_turns=10)
        O.block_on_oversight(objective.id, "ov-123")
        assert O.runnable() == []

    def test_blocking_does_not_stop_or_cancel_it(self, store):
        """It waits. The work is still wanted once a human decides."""
        objective = O.create("bounded", max_turns=10)
        O.block_on_oversight(objective.id, "ov-123")
        assert O.load()[objective.id].state == State.ACTIVE

    def test_it_proceeds_once_the_decision_is_made(self, store):
        objective = O.create("bounded", max_turns=10)
        O.block_on_oversight(objective.id, "ov-123")
        O.unblock(objective.id)
        assert O.claim_turn(objective.id).consumption.turns == 1

    def test_nothing_here_can_clear_a_gate_by_itself(self, store):
        """`unblock` records that a decision happened; it does not make one.

        The objective module has no path to approving its own oversight item.
        """
        assert not hasattr(O, "approve")
        assert not hasattr(O, "escalate")


# --------------------------------------------------------------------------
# Lifecycle and persistence
# --------------------------------------------------------------------------


class TestLifecycle:
    def test_an_objective_survives_a_reload(self, store):
        objective = O.create("persist me", max_turns=3)
        O.claim_turn(objective.id)

        reloaded = O.load()[objective.id]
        assert reloaded.statement == "persist me"
        assert reloaded.consumption.turns == 1

    def test_pause_and_resume(self, store):
        objective = O.create("bounded", max_turns=5)
        assert O.pause(objective.id).state == State.PAUSED
        with pytest.raises(O.ObjectiveError, match="waiting|paused|active"):
            O.claim_turn(objective.id)
        assert O.resume(objective.id).state == State.ACTIVE

    def test_a_paused_objective_is_not_runnable(self, store):
        objective = O.create("bounded", max_turns=5)
        O.pause(objective.id)
        assert O.runnable() == []

    def test_cancel_records_a_reason(self, store):
        objective = O.create("bounded", max_turns=5)
        cancelled = O.cancel(objective.id, "no longer needed")
        assert cancelled.state == State.STOPPED
        assert cancelled.stop_reason == "no longer needed"

    def test_complete_records_that_it_was_met(self, store):
        objective = O.create("bounded", max_turns=5)
        assert O.complete(objective.id).stop_reason == "objective met"

    def test_an_unknown_objective_is_refused(self, store):
        with pytest.raises(O.ObjectiveError, match="no objective"):
            O.claim_turn("obj-nope")

    def test_a_malformed_store_runs_nothing(self, store, tmp_path):
        (tmp_path / "objectives.json").write_text("not json", encoding="utf-8")
        assert O.load() == {}
        assert O.runnable() == []


# --------------------------------------------------------------------------
# Attribution
# --------------------------------------------------------------------------


class TestAttribution:
    def test_consumption_is_attributable(self, store):
        objective = O.create("measure me", max_tokens=10_000)
        O.claim_turn(objective.id)
        O.record_usage(objective.id, 1234)

        stored = O.load()[objective.id]
        assert stored.consumption.turns == 1
        assert stored.consumption.tokens == 1234

    def test_the_record_serialises_for_reporting(self, store):
        objective = O.create("measure me", max_turns=2)
        data = json.loads(json.dumps(objective.as_dict()))
        assert data["ceiling"]["max_turns"] == 2
        assert data["exhausted"] is False

    def test_owner_and_role_are_carried(self, store):
        objective = O.create("x", max_turns=1, owner="flg", role="assistant")
        stored = O.load()[objective.id]
        assert (stored.owner, stored.role) == ("flg", "assistant")

    def test_active_lists_only_active_objectives(self, store):
        first = O.create("a", max_turns=1)
        second = O.create("b", max_turns=1)
        O.pause(second.id)
        assert [o.id for o in O.active()] == [first.id]
