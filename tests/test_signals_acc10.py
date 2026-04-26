"""Tests for ACC-10 signal type constants and subject helpers (acc.signals)."""

from __future__ import annotations

import pytest
from acc.signals import (
    SIG_TASK_PROGRESS,
    SIG_QUEUE_STATUS,
    SIG_BACKPRESSURE,
    SIG_PLAN,
    SIG_KNOWLEDGE_SHARE,
    SIG_EVAL_OUTCOME,
    SIG_CENTROID_UPDATE,
    SIG_EPISODE_NOMINATE,
    subject_task_progress,
    subject_queue_status,
    subject_queue_status_all,
    subject_backpressure,
    subject_backpressure_all,
    subject_plan,
    subject_plan_all,
    subject_knowledge_share,
    subject_knowledge_share_all,
    subject_eval_outcome,
    subject_eval_outcome_all,
    subject_centroid_update,
    subject_episode_nominate,
    redis_scratchpad_key,
    redis_knowledge_key,
    redis_queue_status_key,
)

CID = "sol-01"
AGENT = "analyst-9c1d"


class TestSignalConstants:
    def test_all_acc10_constants_defined(self):
        constants = [
            SIG_TASK_PROGRESS, SIG_QUEUE_STATUS, SIG_BACKPRESSURE,
            SIG_PLAN, SIG_KNOWLEDGE_SHARE, SIG_EVAL_OUTCOME,
            SIG_CENTROID_UPDATE, SIG_EPISODE_NOMINATE,
        ]
        for c in constants:
            assert isinstance(c, str) and len(c) > 0

    def test_constant_values(self):
        assert SIG_TASK_PROGRESS == "TASK_PROGRESS"
        assert SIG_QUEUE_STATUS == "QUEUE_STATUS"
        assert SIG_BACKPRESSURE == "BACKPRESSURE"
        assert SIG_PLAN == "PLAN"
        assert SIG_KNOWLEDGE_SHARE == "KNOWLEDGE_SHARE"
        assert SIG_EVAL_OUTCOME == "EVAL_OUTCOME"
        assert SIG_CENTROID_UPDATE == "CENTROID_UPDATE"
        assert SIG_EPISODE_NOMINATE == "EPISODE_NOMINATE"


class TestSubjectHelpers:
    def test_task_progress(self):
        assert subject_task_progress(CID) == "acc.sol-01.task.progress"

    def test_queue_status_per_agent(self):
        assert subject_queue_status(CID, AGENT) == "acc.sol-01.queue.analyst-9c1d"

    def test_queue_status_wildcard(self):
        assert subject_queue_status_all(CID) == "acc.sol-01.queue.*"

    def test_backpressure_per_agent(self):
        assert subject_backpressure(CID, AGENT) == "acc.sol-01.backpressure.analyst-9c1d"

    def test_backpressure_wildcard(self):
        assert subject_backpressure_all(CID) == "acc.sol-01.backpressure.*"

    def test_plan(self):
        assert subject_plan(CID, "plan-abc") == "acc.sol-01.plan.plan-abc"

    def test_plan_wildcard(self):
        assert subject_plan_all(CID) == "acc.sol-01.plan.*"

    def test_knowledge_share(self):
        assert subject_knowledge_share(CID, "code_patterns") == "acc.sol-01.knowledge.code_patterns"

    def test_knowledge_share_wildcard(self):
        assert subject_knowledge_share_all(CID) == "acc.sol-01.knowledge.*"

    def test_eval_outcome(self):
        assert subject_eval_outcome(CID, "task-xyz") == "acc.sol-01.eval.task-xyz"

    def test_eval_outcome_wildcard(self):
        assert subject_eval_outcome_all(CID) == "acc.sol-01.eval.*"

    def test_centroid_update(self):
        assert subject_centroid_update(CID) == "acc.sol-01.centroid"

    def test_episode_nominate(self):
        assert subject_episode_nominate(CID) == "acc.sol-01.episode.nominate"

    def test_subjects_start_with_acc_prefix(self):
        subjects = [
            subject_task_progress(CID),
            subject_queue_status(CID, AGENT),
            subject_backpressure(CID, AGENT),
            subject_plan(CID, "p1"),
            subject_knowledge_share(CID, "tag"),
            subject_eval_outcome(CID, "t1"),
            subject_centroid_update(CID),
            subject_episode_nominate(CID),
        ]
        for s in subjects:
            assert s.startswith("acc."), f"Subject does not start with 'acc.': {s}"

    def test_subjects_contain_collective_id(self):
        for s in [
            subject_task_progress("my-cid"),
            subject_queue_status("my-cid", "a"),
            subject_centroid_update("my-cid"),
            subject_episode_nominate("my-cid"),
        ]:
            assert "my-cid" in s


class TestRedisKeyHelpers:
    def test_scratchpad_key(self):
        k = redis_scratchpad_key("sol-01", "plan-abc", "analyst", "result")
        assert k == "acc:sol-01:scratchpad:plan-abc:analyst:result"

    def test_scratchpad_key_parts(self):
        k = redis_scratchpad_key("c", "p", "r", "k")
        parts = k.split(":")
        assert parts[0] == "acc"
        assert parts[1] == "c"
        assert parts[2] == "scratchpad"
        assert parts[3] == "p"
        assert parts[4] == "r"
        assert parts[5] == "k"

    def test_knowledge_key(self):
        assert redis_knowledge_key("sol-01", "code_patterns") == "acc:sol-01:knowledge:code_patterns"

    def test_queue_status_key(self):
        assert redis_queue_status_key("sol-01", "analyst-9c1d") == "acc:sol-01:queue_status:analyst-9c1d"
