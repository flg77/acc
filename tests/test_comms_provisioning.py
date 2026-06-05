"""P4 — derive NATS pub/sub permissions from a role manifest."""

from __future__ import annotations

from acc.comms_provisioning import (
    ARBITER_ONLY,
    derive_role_permissions,
    dynamic_provisioning_enabled,
)


def test_dynamic_off_by_default(monkeypatch):
    monkeypatch.delenv("ACC_NKEY_DYNAMIC", raising=False)
    assert dynamic_provisioning_enabled() is False
    monkeypatch.setenv("ACC_NKEY_DYNAMIC", "1")
    assert dynamic_provisioning_enabled() is True


def test_actions_and_domain_drive_permissions():
    role = {
        "allowed_actions": [
            "read_vector_db", "publish_eval_outcome",
            "publish_knowledge_share", "publish_episode_nominate",
        ],
        "domain_id": "capital_markets",
    }
    perms = derive_role_permissions(role)
    pub, sub = perms["publish"], perms["subscribe"]
    # action-gated publish subjects present
    assert "acc.*.eval.*" in pub
    assert "acc.*.knowledge.*" in pub
    assert "acc.*.episode.nominate" in pub
    # baseline
    assert "acc.*.heartbeat" in pub and "acc.*.task.assign" in sub
    # paracrine domain subscribe
    assert "acc.*.domain.capital_markets.>" in sub
    # never granted arbiter-only control subjects
    assert not (set(ARBITER_ONLY) & set(pub))


def test_no_eval_action_no_eval_subject():
    perms = derive_role_permissions({"allowed_actions": ["read_vector_db"], "domain_id": "x"})
    assert "acc.*.eval.*" not in perms["publish"]


def test_can_route_adds_bridge():
    perms = derive_role_permissions(
        {"allowed_actions": [], "domain_id": "x", "can_route": True})
    assert "acc.bridge.*.*.delegate" in perms["publish"]
    plain = derive_role_permissions({"allowed_actions": [], "domain_id": "x"})
    assert "acc.bridge.*.*.delegate" not in plain["publish"]
