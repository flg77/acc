"""Tests for the agent's `config.reload` handler (PR-4).

`Agent._on_config_reload` is the hot-swap path for the TUI's
.env write-back: it updates `os.environ`, re-reads the config, and
swaps the LLM backend on the running agent without restarting NATS,
LanceDB, or the heartbeat loop.
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from acc.agent import Agent
from acc.signals import subject_config_reload


# ---------------------------------------------------------------------------
# Subject helper
# ---------------------------------------------------------------------------


def test_subject_config_reload_format():
    assert subject_config_reload("sol-01") == "acc.sol-01.config.reload"


# ---------------------------------------------------------------------------
# _on_config_reload — pure handler logic
# ---------------------------------------------------------------------------


def _stub_self(tmp_path, old_llm):
    """Build a minimal duck-typed Agent stand-in for direct handler use."""
    backends = SimpleNamespace(llm=old_llm)
    config = SimpleNamespace(
        agent=SimpleNamespace(collective_id="sol-01"),
        llm=SimpleNamespace(backend="ollama", model="", base_url=""),
    )
    return SimpleNamespace(
        config=config,
        backends=backends,
        _config_path=str(tmp_path / "acc-config.yaml"),
        _cognitive_core=None,
        # The handler reads the hot-swap allow-list off ``self``.  In
        # production this is the Agent class attribute; here we attach
        # it to the stub so duck-typing still works.
        _CONFIG_RELOAD_HOT_KEYS=Agent._CONFIG_RELOAD_HOT_KEYS,
    )


def _msg(payload: dict):
    """Wrap a JSON payload in a NATS-msg-like object."""
    return SimpleNamespace(data=json.dumps(payload).encode())


def test_hot_swap_changes_backend(monkeypatch, tmp_path):
    """A reload payload with hot-swap keys rebuilds the LLM backend."""
    old_llm = MagicMock(name="old_llm")
    new_llm = MagicMock(name="new_llm")
    stub = _stub_self(tmp_path, old_llm)

    new_config = SimpleNamespace(
        agent=SimpleNamespace(collective_id="sol-01"),
        llm=SimpleNamespace(backend="anthropic",
                             model="claude-sonnet-4-5",
                             base_url="https://api.anthropic.com",
                             anthropic_model="claude-sonnet-4-5",
                             vllm_inference_url=""),
    )
    monkeypatch.setattr("acc.agent.load_config", lambda _path: new_config)
    monkeypatch.setattr("acc.agent.build_llm_backend", lambda _cfg: new_llm)

    asyncio.run(Agent._on_config_reload(stub, _msg({
        "v": 1,
        "source": "tui",
        "operator": "alice",
        "changes": {
            "ACC_LLM_BACKEND": "anthropic",
            "ACC_LLM_MODEL": "claude-sonnet-4-5",
            "ACC_LLM_BASE_URL": "https://api.anthropic.com",
            "ACC_LLM_TIMEOUT_S": "120",
        },
    })))

    assert stub.backends.llm is new_llm, "LLM backend was not swapped"
    # Env vars are now set so subsequent load_config() calls see them.
    assert os.environ["ACC_LLM_BACKEND"] == "anthropic"
    assert os.environ["ACC_LLM_MODEL"] == "claude-sonnet-4-5"


def test_ignores_non_hot_swappable_keys(monkeypatch, tmp_path):
    """A payload containing only restart-required keys does NOT swap."""
    old_llm = MagicMock(name="old_llm")
    stub = _stub_self(tmp_path, old_llm)

    rebuilt = [False]
    def _should_not_be_called(_cfg):
        rebuilt[0] = True
        return MagicMock()
    monkeypatch.setattr("acc.agent.build_llm_backend", _should_not_be_called)
    monkeypatch.setattr("acc.agent.load_config", lambda _path:
                        pytest.fail("load_config must not be called"))

    asyncio.run(Agent._on_config_reload(stub, _msg({
        "changes": {
            "ACC_NATS_URL": "nats://elsewhere:4222",   # restart required
            "ACC_AGENT_ROLE": "evil",                    # restart required
        },
    })))

    assert stub.backends.llm is old_llm, "should not have swapped"
    assert rebuilt[0] is False


def test_invalid_json_is_swallowed(monkeypatch, tmp_path):
    old_llm = MagicMock(name="old_llm")
    stub = _stub_self(tmp_path, old_llm)
    monkeypatch.setattr("acc.agent.load_config", lambda _path: pytest.fail(
        "load_config must not be called on bad JSON"))

    bad = SimpleNamespace(data=b"{not json")
    # Must not raise.
    asyncio.run(Agent._on_config_reload(stub, bad))
    assert stub.backends.llm is old_llm


def test_rebuild_failure_keeps_old_llm(monkeypatch, tmp_path):
    """If load_config/build_llm_backend raise, keep the old client."""
    old_llm = MagicMock(name="old_llm")
    stub = _stub_self(tmp_path, old_llm)

    def _boom(_path):
        raise RuntimeError("config file gone")
    monkeypatch.setattr("acc.agent.load_config", _boom)

    asyncio.run(Agent._on_config_reload(stub, _msg({
        "changes": {"ACC_LLM_BACKEND": "anthropic"},
    })))

    assert stub.backends.llm is old_llm, "failed rebuild must NOT swap"
