"""Pack-role boot-and-wait (proposal 031 G2).

A pack-role agent whose ``role.yaml`` is not yet resolvable from any real source
(no installed package, no in-tree definition) must NOT masquerade as the role
with the generic in-config default behaviour.  It boots DORMANT — with no
CognitiveCore, so :func:`acc.agent._handle_task` drops tasks — and self-promotes
the moment its package is installed (``try_resolve_real_role`` starts returning
a definition), reusing the same DORMANT->ACTIVE transition a signed ROLE_ASSIGN
takes.

These tests cover the two seams that make that work:
  * ``RoleStore.loaded_from_default`` + ``RoleStore.try_resolve_real_role``
  * ``Agent._maybe_promote_pending_pack``
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from acc.config import RoleDefinitionConfig
from tests.test_role_store import _make_store
from tests.test_worker_pool import _make_agent_stub, _role_dict


# ---------------------------------------------------------------------------
# RoleStore — fall-through detection + no-default re-poll
# ---------------------------------------------------------------------------


def test_loaded_from_default_true_when_no_real_source() -> None:
    """No package / in-tree / file / redis / lancedb source -> fell back to the
    generic in-config default; the flag records that so the agent boots dormant."""
    store = _make_store()  # roles_root nonexistent, no redis/vector
    store.load_at_startup()
    assert store.loaded_from_default is True


def test_loaded_from_default_false_when_a_real_source_resolves() -> None:
    fake = RoleDefinitionConfig()
    store = _make_store()
    with patch.object(store._role_loader, "load", return_value=fake):
        role = store.load_at_startup()
    assert role is fake
    assert store.loaded_from_default is False


def test_try_resolve_real_role_none_when_only_default_available() -> None:
    """The re-poll must NOT fall back to the config default — that is what keeps
    a pending-pack agent dormant instead of silently promoting on nothing."""
    store = _make_store()
    store.load_at_startup()
    assert store.loaded_from_default is True
    assert store.try_resolve_real_role() is None


def test_try_resolve_real_role_returns_role_and_clears_default_flag() -> None:
    store = _make_store()
    store.load_at_startup()
    assert store.loaded_from_default is True
    fake = RoleDefinitionConfig()
    with patch.object(store._role_loader, "load", return_value=fake):
        resolved = store.try_resolve_real_role()
    assert resolved is fake
    assert store.loaded_from_default is False


# ---------------------------------------------------------------------------
# Agent — dormant-pending-pack self-promotion
# ---------------------------------------------------------------------------


def test_pending_pack_self_promotes_when_package_lands() -> None:
    agent = _make_agent_stub()
    agent.config.agent.role = "financial_analyst"
    agent._dormant_pending_pack = True
    resolved = RoleDefinitionConfig.model_validate(_role_dict())
    agent._role_store = MagicMock()
    agent._role_store.try_resolve_real_role.return_value = resolved

    promoted = agent._maybe_promote_pending_pack()

    assert promoted is True
    assert agent._dormant_pending_pack is False
    assert agent._cognitive_core is not None  # deferred core now built
    assert agent.state == "ACTIVE"


def test_pending_pack_stays_dormant_until_package_arrives() -> None:
    agent = _make_agent_stub()
    agent.config.agent.role = "financial_analyst"
    agent._dormant_pending_pack = True
    agent._role_store = MagicMock()
    agent._role_store.try_resolve_real_role.return_value = None

    promoted = agent._maybe_promote_pending_pack()

    assert promoted is False
    assert agent._dormant_pending_pack is True
    assert agent._cognitive_core is None  # still no core -> tasks dropped
    assert agent.state == "DORMANT"


def test_non_pending_agent_never_polls() -> None:
    """An agent that booted normally (or a real dormant worker awaiting a
    ROLE_ASSIGN) never enters the pack poll, so it is a cheap no-op."""
    agent = _make_agent_stub()
    agent._dormant_pending_pack = False
    agent._role_store = MagicMock()
    assert agent._maybe_promote_pending_pack() is False
    agent._role_store.try_resolve_real_role.assert_not_called()
