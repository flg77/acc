"""Role-keyed memory — `20261003-assistant-orchestrated-infusion` Phase 5.

A promoted pool worker used to remember into ``lancedb/worker-N``: stop it and
re-infuse the role on another worker, and the role started from nothing.  It
now opens ``lancedb/roles/<role>``, and episode retrieval accepts a sibling
worker's rows from that store -- while the scope filter, which decides WHO may
read a memory, is unchanged.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from acc.role_memory import role_memory_enabled, role_memory_path

lancedb = pytest.importorskip("lancedb")

from acc.backends.vector_lancedb import LanceDBBackend  # noqa: E402

EMBED = [0.1] * 384


def test_path_sits_beside_the_worker_store():
    assert role_memory_path("/app/data/lancedb/worker-3", "devops_engineer") == \
        "/app/data/lancedb/roles/devops_engineer"
    assert role_memory_path("/app/data/lancedb/worker-3/", "devops_engineer", "default") == \
        "/app/data/lancedb/roles/devops_engineer"
    assert role_memory_path("/app/data/lancedb/worker-3", "analyst", "backend") == \
        "/app/data/lancedb/roles/analyst--backend"


def test_names_cannot_escape_the_roles_directory():
    p = role_memory_path("/app/data/lancedb/worker-1", "../../etc", "../x")
    assert p.startswith("/app/data/lancedb/roles/") and "/../" not in p


def test_switch_off(monkeypatch):
    monkeypatch.setenv("ACC_ROLE_MEMORY", "0")
    assert role_memory_enabled() is False


def _worker(base, n):
    from acc.agent import Agent

    a = SimpleNamespace(
        agent_id=f"worker-{n}",
        backends=SimpleNamespace(vector=LanceDBBackend(str(base / f"worker-{n}"))),
        _cognitive_core=None,
    )
    a._switch_to_role_memory = Agent._switch_to_role_memory.__get__(a)
    return a


def _episode(agent_id, text, scope="local"):
    return {
        "id": f"{agent_id}-{time.time_ns()}", "agent_id": agent_id, "requester": "",
        "scope": scope, "ts": time.time(), "signal_type": "TASK_ASSIGN",
        "payload_json": json.dumps({"content": text}), "embedding": EMBED,
    }


def test_a_role_keeps_its_memory_across_workers(tmp_path, monkeypatch):
    monkeypatch.delenv("ACC_ROLE_MEMORY", raising=False)
    w1 = _worker(tmp_path, 1)
    path = w1._switch_to_role_memory("devops_engineer")
    assert path == str(tmp_path / "roles" / "devops_engineer")
    w1.backends.vector.insert("episodes", [_episode("worker-1", "sshd allows passwords")])

    # worker-1 is stopped; the role is re-infused on worker-2
    w2 = _worker(tmp_path, 2)
    assert w2._switch_to_role_memory("devops_engineer") == path
    rows = w2.backends.vector.rows("episodes")
    assert [json.loads(r["payload_json"])["content"] for r in rows] == ["sshd allows passwords"]
    # ...and another role does not see it
    w3 = _worker(tmp_path, 3)
    w3._switch_to_role_memory("analyst")
    assert w3.backends.vector.rows("episodes") == []


def test_nothing_changes_when_off_or_dormant(tmp_path, monkeypatch):
    w = _worker(tmp_path, 1)
    assert w._switch_to_role_memory("dormant") == ""
    monkeypatch.setenv("ACC_ROLE_MEMORY", "0")
    assert w._switch_to_role_memory("devops_engineer") == ""
    assert w.backends.vector._path == str(tmp_path / "worker-1")


def test_non_lancedb_backends_keep_per_worker_memory(monkeypatch):
    from acc.agent import Agent

    monkeypatch.delenv("ACC_ROLE_MEMORY", raising=False)
    other = object()
    a = SimpleNamespace(agent_id="worker-1", backends=SimpleNamespace(vector=other), _cognitive_core=None)
    assert Agent._switch_to_role_memory(a, "devops_engineer") == ""
    assert a.backends.vector is other


def _core(vector, agent_id, shared):
    from acc.cognitive_core import CognitiveCore

    llm = MagicMock()
    llm.embed = AsyncMock(return_value=EMBED)
    core = CognitiveCore(agent_id=agent_id, collective_id="sol-01", llm=llm,
                         vector=vector, redis_client=None, role_label="devops_engineer")
    core._shared_role_memory = shared
    return core


def _retrieved(core, scope="local"):
    from acc.config import RoleDefinitionConfig

    role = RoleDefinitionConfig(purpose="p", persona="concise")
    return asyncio.run(core._retrieve_episodes("ssh hardening", role, scope=scope))


def test_retrieval_reads_sibling_rows_only_from_a_role_store_and_scope_still_holds(tmp_path):
    store = LanceDBBackend(str(tmp_path / "roles" / "devops_engineer"))
    store.insert("episodes", [
        _episode("worker-1", "local fact from worker-1"),
        _episode("worker-1", "alice's private fact", scope="user:alice"),
    ])
    # worker-2 on the role store sees what the role learned on worker-1 ...
    shared = _retrieved(_core(store, "worker-2", shared=True))
    assert [e["excerpt"] for e in shared] == ["local fact from worker-1"]
    # ... but not another scope's memory: the scope filter is unchanged
    assert all("alice" not in e["excerpt"] for e in shared)
    assert [e["excerpt"] for e in _retrieved(_core(store, "worker-2", shared=True), scope="user:alice")] == \
        ["alice's private fact"]
    # a per-worker store keeps the old same-agent rule
    assert _retrieved(_core(store, "worker-2", shared=False)) == []
