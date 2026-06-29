"""PR-J (D-001) — worker-pool dormant boot + ROLE_ASSIGN promotion.

The arbiter's reconcile loop (deferred to J-2) will eventually drive
ROLE_ASSIGN automatically from `collective.yaml`.  For PR-J the path
under test is the agent-side primitive: a dormant worker boots
without a CognitiveCore, subscribes to ``acc.<cid>.role_assign``,
verifies the signed payload against the registered arbiter key, and
promotes itself in place.

These tests cover:

1. Signer / verifier round-trip — happy path.
2. Verifier rejects tampered / missing / wrong-key payloads.
3. ``_promote_from_dormant`` builds a CognitiveCore, flips state to
   ACTIVE, propagates cluster_id / purpose to env vars.
4. ``_handle_role_assign`` accepts a correctly-targeted signed
   payload, rejects target_agent_id mismatches, rejects bad
   signatures.

The agent-side handler is exercised directly (synchronous fixtures)
rather than through a live NATS loop — the same pattern the
existing ``test_agent_config_reload.py`` uses.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from acc.role_assign import (
    RoleAssignRejectedError,
    generate_keypair_b64,
    sign_role_assign,
    verify_role_assign,
)


# ---------------------------------------------------------------------------
# Signer / verifier round-trip
# ---------------------------------------------------------------------------


def _role_dict() -> dict:
    """Minimal RoleDefinitionConfig dict — all required fields filled
    with the safe Pydantic defaults plus a non-empty purpose so the
    promoted agent has something to act on."""
    return {
        "purpose": "Implement Python web scrapers and parse JSON.",
        "persona": "analytical",
        "task_types": ["CODE_GENERATE"],
        "seed_context": "Domain: software engineering.",
        "version": "0.1.0",
    }


class TestSignVerifyRoundTrip:
    def test_signs_and_verifies_clean_payload(self):
        priv, pub = generate_keypair_b64()
        payload = sign_role_assign(
            approver_id="arbiter-1",
            target_agent_id="worker-7",
            role_definition=_role_dict(),
            cluster_id="backend",
            purpose="Implement Fibonacci",
            private_key_b64=priv,
        )
        # No exception → verifies.
        verify_role_assign(payload, verify_key_b64=pub)

    def test_signature_bound_to_target_agent_id(self):
        """Tampering target_agent_id after signing must fail
        verification — a key invariant that prevents redirecting a
        signed assignment from worker-A onto worker-B."""
        priv, pub = generate_keypair_b64()
        payload = sign_role_assign(
            approver_id="arbiter-1",
            target_agent_id="worker-7",
            role_definition=_role_dict(),
            private_key_b64=priv,
        )
        payload["target_agent_id"] = "worker-99"  # tamper
        with pytest.raises(RoleAssignRejectedError) as exc:
            verify_role_assign(payload, verify_key_b64=pub)
        assert "signature" in str(exc.value).lower()

    def test_signature_bound_to_role_definition(self):
        """Tampering role_definition (e.g. broadening
        allowed_actions) must fail verification."""
        priv, pub = generate_keypair_b64()
        payload = sign_role_assign(
            approver_id="arbiter-1",
            target_agent_id="worker-7",
            role_definition=_role_dict(),
            private_key_b64=priv,
        )
        payload["role_definition"]["allowed_actions"] = ["drop_database"]
        with pytest.raises(RoleAssignRejectedError):
            verify_role_assign(payload, verify_key_b64=pub)

    def test_signature_bound_to_approver_id(self):
        priv, pub = generate_keypair_b64()
        payload = sign_role_assign(
            approver_id="arbiter-1",
            target_agent_id="worker-7",
            role_definition=_role_dict(),
            private_key_b64=priv,
        )
        payload["approver_id"] = "attacker-arbiter"
        with pytest.raises(RoleAssignRejectedError):
            verify_role_assign(payload, verify_key_b64=pub)

    def test_wrong_verify_key_rejected(self):
        priv, _ = generate_keypair_b64()
        _, wrong_pub = generate_keypair_b64()
        payload = sign_role_assign(
            approver_id="arbiter-1",
            target_agent_id="worker-7",
            role_definition=_role_dict(),
            private_key_b64=priv,
        )
        with pytest.raises(RoleAssignRejectedError):
            verify_role_assign(payload, verify_key_b64=wrong_pub)

    def test_missing_signature_rejected(self):
        _, pub = generate_keypair_b64()
        with pytest.raises(RoleAssignRejectedError) as exc:
            verify_role_assign(
                {
                    "approver_id": "arbiter-1",
                    "target_agent_id": "worker-7",
                    "role_definition": _role_dict(),
                },
                verify_key_b64=pub,
            )
        assert "signature" in str(exc.value).lower()

    def test_missing_verify_key_rejected(self):
        priv, _ = generate_keypair_b64()
        payload = sign_role_assign(
            approver_id="arbiter-1",
            target_agent_id="worker-7",
            role_definition=_role_dict(),
            private_key_b64=priv,
        )
        with pytest.raises(RoleAssignRejectedError) as exc:
            verify_role_assign(payload, verify_key_b64="")
        assert "verify_key" in str(exc.value).lower()

    def test_empty_role_definition_rejected(self):
        _, pub = generate_keypair_b64()
        with pytest.raises(RoleAssignRejectedError):
            verify_role_assign(
                {
                    "approver_id": "arbiter-1",
                    "target_agent_id": "worker-7",
                    "role_definition": {},
                    "signature": "AAAA",
                },
                verify_key_b64=pub,
            )

    def test_non_dict_payload_rejected(self):
        _, pub = generate_keypair_b64()
        with pytest.raises(RoleAssignRejectedError):
            verify_role_assign("not a dict", verify_key_b64=pub)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Agent-side promotion
# ---------------------------------------------------------------------------


def _make_agent_stub(agent_id: str = "worker-7", verify_key_b64: str = ""):
    """Build a minimal ACCAgent-shaped stub.  We don't actually call
    __init__ (it would try to connect to NATS); we hand-roll the
    attributes the methods under test read."""
    from acc.agent import (
        Agent,
        STATE_DORMANT,
        STATE_ACTIVE,  # noqa: F401  (re-exported for the asserts)
    )
    from acc.config import ACCConfig, AgentConfig
    from acc.cognitive_core import CognitiveCore  # noqa: F401  for typing

    agent = Agent.__new__(Agent)
    agent.agent_id = agent_id
    agent.state = STATE_DORMANT
    agent._cognitive_core = None
    agent._active_role = None
    agent._redis = None
    agent._skill_registry = MagicMock()
    agent._mcp_registry = MagicMock()

    backends = MagicMock()
    backends.llm = MagicMock()
    backends.vector = MagicMock()
    backends.signaling = MagicMock()
    agent.backends = backends

    # Minimal config — only the fields touched by _promote_from_dormant
    # and _resolve_role_assign_verify_key.
    cfg = MagicMock()
    cfg.agent = MagicMock()
    cfg.agent.role = "dormant"
    cfg.agent.collective_id = "sol-test"
    cfg.agent.peer_collectives = []
    cfg.agent.hub_collective_id = None
    cfg.agent.bridge_enabled = False
    cfg.security = MagicMock()
    cfg.security.ed25519 = MagicMock()
    cfg.security.ed25519.verify_key = verify_key_b64
    agent.config = cfg
    return agent


class TestPromoteFromDormant:
    def test_promotion_builds_cognitive_core(self):
        agent = _make_agent_stub()
        priv, pub = generate_keypair_b64()
        payload = sign_role_assign(
            approver_id="arbiter-1",
            target_agent_id=agent.agent_id,
            role_definition={**_role_dict(), "name": "coding_agent"},
            cluster_id="backend",
            purpose="Implement webscraper",
            private_key_b64=priv,
        )

        agent._promote_from_dormant(payload)

        assert agent._cognitive_core is not None
        assert agent.state == "ACTIVE"
        assert agent._active_role is not None
        assert agent._active_role.purpose.startswith("Implement Python")

    def test_promotion_sets_cluster_and_purpose_env(self, monkeypatch):
        """PR-J — promoted agents tag their heartbeat with cluster_id
        + purpose via env vars, matching PR-D's HEARTBEAT contract."""
        monkeypatch.delenv("ACC_CLUSTER_ID", raising=False)
        monkeypatch.delenv("ACC_AGENT_PURPOSE", raising=False)
        agent = _make_agent_stub()
        priv, _ = generate_keypair_b64()
        payload = sign_role_assign(
            approver_id="arbiter-1",
            target_agent_id=agent.agent_id,
            role_definition=_role_dict(),
            cluster_id="planner",
            purpose="Plan migration",
            private_key_b64=priv,
        )
        agent._promote_from_dormant(payload)
        assert os.environ.get("ACC_CLUSTER_ID") == "planner"
        assert os.environ.get("ACC_AGENT_PURPOSE") == "Plan migration"

    def test_promotion_updates_config_role(self):
        agent = _make_agent_stub()
        priv, _ = generate_keypair_b64()
        payload = sign_role_assign(
            approver_id="arbiter-1",
            target_agent_id=agent.agent_id,
            role_definition={**_role_dict(), "name": "coding_agent"},
            private_key_b64=priv,
        )
        agent._promote_from_dormant(payload)
        assert agent.config.agent.role == "coding_agent"


# ---------------------------------------------------------------------------
# _handle_role_assign — full dispatch (subscription + filter + verify)
# ---------------------------------------------------------------------------


class _MsgStub:
    """Match the wire-format the SignalingBackend hands to handlers
    (post Commit-7: just bytes via ``_payload_bytes``)."""

    def __init__(self, payload: dict) -> None:
        self.data = json.dumps(payload).encode()


def _inline_handler(agent):
    """Re-create the closure ``_subscribe_role_assign`` uses, but
    without the NATS subscribe call.  Same logic, exercised
    synchronously."""
    from acc.agent import _payload_bytes

    async def _handle_role_assign(msg):
        try:
            payload = json.loads(_payload_bytes(msg))
        except (json.JSONDecodeError, TypeError):
            return
        if payload.get("target_agent_id", "") != agent.agent_id:
            return
        try:
            key = agent._resolve_role_assign_verify_key()
            verify_role_assign(payload, verify_key_b64=key)
        except RoleAssignRejectedError:
            return
        agent._promote_from_dormant(payload)

    return _handle_role_assign


@pytest.mark.asyncio
async def test_handler_promotes_on_valid_targeted_signed_payload():
    priv, pub = generate_keypair_b64()
    agent = _make_agent_stub(verify_key_b64=pub)
    handler = _inline_handler(agent)

    payload = sign_role_assign(
        approver_id="arbiter-1",
        target_agent_id=agent.agent_id,
        role_definition=_role_dict(),
        cluster_id="ops",
        purpose="ops queue drain",
        private_key_b64=priv,
    )
    await handler(_MsgStub(payload))

    assert agent.state == "ACTIVE"
    assert agent._cognitive_core is not None


@pytest.mark.asyncio
async def test_handler_drops_when_target_agent_mismatch():
    priv, pub = generate_keypair_b64()
    agent = _make_agent_stub(agent_id="worker-7", verify_key_b64=pub)
    handler = _inline_handler(agent)

    payload = sign_role_assign(
        approver_id="arbiter-1",
        target_agent_id="worker-99",  # not us
        role_definition=_role_dict(),
        private_key_b64=priv,
    )
    await handler(_MsgStub(payload))

    # No promotion happened.
    assert agent.state == "DORMANT"
    assert agent._cognitive_core is None


@pytest.mark.asyncio
async def test_handler_rejects_bad_signature():
    priv, pub = generate_keypair_b64()
    agent = _make_agent_stub(verify_key_b64=pub)
    handler = _inline_handler(agent)

    payload = sign_role_assign(
        approver_id="arbiter-1",
        target_agent_id=agent.agent_id,
        role_definition=_role_dict(),
        private_key_b64=priv,
    )
    # Tamper after signing.
    payload["role_definition"]["allowed_actions"] = ["delete_everything"]
    await handler(_MsgStub(payload))

    assert agent.state == "DORMANT"
    assert agent._cognitive_core is None


@pytest.mark.asyncio
async def test_handler_rejects_when_verify_key_unconfigured():
    priv, _ = generate_keypair_b64()
    agent = _make_agent_stub(verify_key_b64="")  # no key configured
    handler = _inline_handler(agent)

    payload = sign_role_assign(
        approver_id="arbiter-1",
        target_agent_id=agent.agent_id,
        role_definition=_role_dict(),
        private_key_b64=priv,
    )
    await handler(_MsgStub(payload))

    assert agent.state == "DORMANT"
    assert agent._cognitive_core is None


@pytest.mark.asyncio
async def test_handler_handles_invalid_json_silently():
    agent = _make_agent_stub()
    handler = _inline_handler(agent)

    class _Bad:
        data = b"not json {{{"

    await handler(_Bad())
    assert agent.state == "DORMANT"
    assert agent._cognitive_core is None


# ---------------------------------------------------------------------------
# Dormant boot state pins
# ---------------------------------------------------------------------------


def test_dormant_role_constants_present():
    """PR-J — STATE_DORMANT exists, and the no-cognitive set covers
    both 'dormant' and the empty string."""
    from acc.agent import STATE_DORMANT, _NO_COGNITIVE_ROLES
    assert STATE_DORMANT == "DORMANT"
    assert "dormant" in _NO_COGNITIVE_ROLES
    assert "" in _NO_COGNITIVE_ROLES


def test_signal_constant_and_subject_helper_present():
    """PR-J — SIG_ROLE_ASSIGN + subject_role_assign exposed from
    acc.signals."""
    from acc.signals import SIG_ROLE_ASSIGN, subject_role_assign
    assert SIG_ROLE_ASSIGN == "ROLE_ASSIGN"
    assert subject_role_assign("sol-test") == "acc.sol-test.role_assign"


# ---------------------------------------------------------------------------
# B4 / O3-runtime (proposal 044) — promotion re-resolves the role's model
# ---------------------------------------------------------------------------

_REG_WITH_ROLE = """\
models:
  - model_id: maas-qwen3-14b
    backend: openai_compat
    model: qwen3-14b
    base_url: https://maas/v1
    api_key_env: MAAS_API_KEY
role_models:
  reviewer: maas-qwen3-14b
"""


class TestReresolveRoleModel:
    def _registry(self, tmp_path, monkeypatch):
        p = tmp_path / "models.yaml"
        p.write_text(_REG_WITH_ROLE, encoding="utf-8")
        monkeypatch.setenv("ACC_MODELS_PATH", str(p))
        # Track the keys the overlay writes so monkeypatch restores them.
        for k in ("ACC_LLM_BACKEND", "ACC_LLM_MODEL", "ACC_LLM_BASE_URL",
                  "ACC_LLM_API_KEY_ENV"):
            monkeypatch.setenv(k, "_baseline_")
        monkeypatch.delenv("ACC_AGENT_MODEL_ID", raising=False)

    def test_promotion_reresolves_mapped_model(self, tmp_path, monkeypatch):
        """A promoted-from-dormant role bound in role_models gets its model
        re-resolved + the LLM hot-swapped (so it isn't stuck on the global
        3B default)."""
        import os
        import acc.agent as agent_mod
        self._registry(tmp_path, monkeypatch)
        agent = _make_agent_stub()
        agent._config_path = "acc-config.yaml"
        sentinel = MagicMock(name="rebuilt_llm")
        monkeypatch.setattr(agent_mod, "load_config", lambda _p: agent.config)
        monkeypatch.setattr(agent_mod, "build_llm_backend", lambda _c: sentinel)

        agent._reresolve_role_model("reviewer")

        assert agent.backends.llm is sentinel              # hot-swapped
        assert os.environ["ACC_LLM_BACKEND"] == "openai_compat"
        assert os.environ["ACC_LLM_MODEL"] == "qwen3-14b"
        assert os.environ["ACC_LLM_API_KEY_ENV"] == "MAAS_API_KEY"

    def test_unmapped_role_is_noop(self, tmp_path, monkeypatch):
        """An unmapped role keeps the current backend and never rebuilds."""
        import acc.agent as agent_mod
        self._registry(tmp_path, monkeypatch)
        agent = _make_agent_stub()
        before = agent.backends.llm
        calls = {"n": 0}
        monkeypatch.setattr(
            agent_mod, "build_llm_backend",
            lambda _c: calls.__setitem__("n", calls["n"] + 1),
        )
        agent._reresolve_role_model("analyst")  # not in role_models
        assert agent.backends.llm is before
        assert calls["n"] == 0

    def test_collective_override_wins_over_role_models(self, tmp_path, monkeypatch):
        """ACC_AGENT_MODEL_ID (collective override) takes precedence over the
        role_models mapping — same locked B6 precedence at promotion time."""
        import os
        import acc.agent as agent_mod
        self._registry(tmp_path, monkeypatch)
        # reviewer maps to maas-qwen3-14b, but the per-agent override pins the
        # same registry's only other resolvable id — here reuse maas to keep
        # the registry minimal but assert the override PATH is taken.
        monkeypatch.setenv("ACC_AGENT_MODEL_ID", "maas-qwen3-14b")
        agent = _make_agent_stub()
        agent._config_path = "acc-config.yaml"
        sentinel = MagicMock(name="rebuilt_llm")
        monkeypatch.setattr(agent_mod, "load_config", lambda _p: agent.config)
        monkeypatch.setattr(agent_mod, "build_llm_backend", lambda _c: sentinel)
        # Even a role NOT in role_models resolves, because the override applies.
        agent._reresolve_role_model("analyst")
        assert agent.backends.llm is sentinel
        assert os.environ["ACC_LLM_MODEL"] == "qwen3-14b"
