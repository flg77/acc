"""Tests for acc/signals.py — constants, subject helpers, Redis key helpers."""

from __future__ import annotations

from acc.signals import (
    SIG_REGISTER,
    SIG_HEARTBEAT,
    SIG_TASK_ASSIGN,
    SIG_TASK_COMPLETE,
    SIG_ROLE_UPDATE,
    SIG_ROLE_APPROVAL,
    SIG_ALERT_ESCALATE,
    subject_register,
    subject_heartbeat,
    subject_task,
    subject_role_update,
    subject_role_approval,
    subject_alert,
    redis_role_key,
    redis_centroid_key,
    redis_stress_key,
    redis_collective_key,
)


class TestSignalConstants:
    def test_register(self):
        assert SIG_REGISTER == "REGISTER"

    def test_heartbeat(self):
        assert SIG_HEARTBEAT == "HEARTBEAT"

    def test_task_assign(self):
        assert SIG_TASK_ASSIGN == "TASK_ASSIGN"

    def test_task_complete(self):
        assert SIG_TASK_COMPLETE == "TASK_COMPLETE"

    def test_role_update(self):
        assert SIG_ROLE_UPDATE == "ROLE_UPDATE"

    def test_role_approval(self):
        assert SIG_ROLE_APPROVAL == "ROLE_APPROVAL"

    def test_alert_escalate(self):
        assert SIG_ALERT_ESCALATE == "ALERT_ESCALATE"


class TestSubjectHelpers:
    COLL = "sol-01"

    def test_subject_register(self):
        assert subject_register(self.COLL) == "acc.sol-01.register"

    def test_subject_heartbeat(self):
        assert subject_heartbeat(self.COLL) == "acc.sol-01.heartbeat"

    def test_subject_task(self):
        assert subject_task(self.COLL) == "acc.sol-01.task"

    def test_subject_role_update(self):
        assert subject_role_update(self.COLL) == "acc.sol-01.role_update"

    def test_subject_role_approval(self):
        assert subject_role_approval(self.COLL) == "acc.sol-01.role_approval"

    def test_subject_alert(self):
        assert subject_alert(self.COLL) == "acc.sol-01.alert"

    def test_subjects_start_with_acc_prefix(self):
        for fn in (
            subject_register,
            subject_heartbeat,
            subject_task,
            subject_role_update,
            subject_role_approval,
            subject_alert,
        ):
            assert fn(self.COLL).startswith("acc."), f"{fn.__name__} does not start with 'acc.'"

    def test_collective_id_embedded_in_subject(self):
        coll = "alpha-7"
        assert "alpha-7" in subject_heartbeat(coll)
        assert "alpha-7" in subject_role_update(coll)


class TestRedisKeyHelpers:
    COLL = "sol-01"
    AGENT = "analyst-9c1d"

    def test_role_key(self):
        assert redis_role_key(self.COLL, self.AGENT) == "acc:sol-01:analyst-9c1d:role"

    def test_centroid_key(self):
        assert redis_centroid_key(self.COLL, self.AGENT) == "acc:sol-01:analyst-9c1d:centroid"

    def test_stress_key(self):
        assert redis_stress_key(self.COLL, self.AGENT) == "acc:sol-01:analyst-9c1d:stress"

    def test_collective_key(self):
        assert redis_collective_key(self.COLL) == "acc:sol-01:registry"

    def test_keys_start_with_acc_prefix(self):
        for fn, args in [
            (redis_role_key, (self.COLL, self.AGENT)),
            (redis_centroid_key, (self.COLL, self.AGENT)),
            (redis_stress_key, (self.COLL, self.AGENT)),
        ]:
            assert fn(*args).startswith("acc:"), f"{fn.__name__} does not start with 'acc:'"

    def test_agent_id_embedded_in_key(self):
        agent = "ingester-4a2f"
        assert agent in redis_role_key(self.COLL, agent)
        assert agent in redis_centroid_key(self.COLL, agent)

    def test_collective_id_embedded_in_key(self):
        coll = "gamma-99"
        assert coll in redis_role_key(coll, self.AGENT)
        assert coll in redis_collective_key(coll)
