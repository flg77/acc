"""Tests for the central model registry + per-agent model env (PR-MM1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from acc.models import (
    ModelEntry,
    get_model,
    load_models,
    model_env,
    model_env_for_id,
)

_REGISTRY = """\
models:
  - model_id: claude-sonnet
    backend: anthropic
    model: claude-sonnet-4-6
    label: "Sonnet (reviewer)"
  - model_id: ollama-small
    backend: ollama
    model: "llama3.2:3b"
    base_url: "http://localhost:11434"
    label: "Ollama small (worker)"
  - model_id: groq-70b
    backend: openai_compat
    model: "llama-3.3-70b-versatile"
    base_url: "https://api.groq.com/openai/v1"
    api_key_env: "GROQ_API_KEY"
"""


@pytest.fixture
def registry(tmp_path, monkeypatch):
    p = tmp_path / "models.yaml"
    p.write_text(_REGISTRY, encoding="utf-8")
    monkeypatch.setenv("ACC_MODELS_PATH", str(p))
    return p


def test_load_and_get(registry):
    models = load_models()
    assert {m.model_id for m in models} == {"claude-sonnet", "ollama-small", "groq-70b"}
    assert get_model("claude-sonnet").model == "claude-sonnet-4-6"
    assert get_model("nope") is None


def test_missing_registry_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ACC_MODELS_PATH", str(tmp_path / "absent.yaml"))
    assert load_models() == []


def test_invalid_registry_is_empty(tmp_path, monkeypatch):
    p = tmp_path / "models.yaml"
    p.write_text("models:\n  - model_id: x\n    bogus: 1\n", encoding="utf-8")
    monkeypatch.setenv("ACC_MODELS_PATH", str(p))
    assert load_models() == []  # extra='forbid' rejects → best-effort empty


def test_model_env_anthropic():
    env = model_env(ModelEntry(model_id="x", backend="anthropic", model="claude-sonnet-4-6"))
    assert env == {"ACC_LLM_BACKEND": "anthropic", "ACC_ANTHROPIC_MODEL": "claude-sonnet-4-6"}


def test_model_env_ollama():
    env = model_env(ModelEntry(
        model_id="x", backend="ollama", model="llama3.2:3b",
        base_url="http://h:11434",
    ))
    assert env["ACC_LLM_BACKEND"] == "ollama"
    assert env["ACC_OLLAMA_MODEL"] == "llama3.2:3b"
    assert env["ACC_OLLAMA_BASE_URL"] == "http://h:11434"


def test_model_env_openai_compat():
    env = model_env(ModelEntry(
        model_id="x", backend="openai_compat", model="m",
        base_url="https://api/v1", api_key_env="GROQ_API_KEY",
    ))
    assert env["ACC_LLM_BACKEND"] == "openai_compat"
    assert env["ACC_LLM_MODEL"] == "m"
    assert env["ACC_LLM_BASE_URL"] == "https://api/v1"
    assert env["ACC_LLM_API_KEY_ENV"] == "GROQ_API_KEY"


def test_model_env_for_id(registry):
    env = model_env_for_id("ollama-small")
    assert env["ACC_LLM_BACKEND"] == "ollama"
    assert env["ACC_OLLAMA_MODEL"] == "llama3.2:3b"
    # Unknown / None → empty (fall back to collective default).
    assert model_env_for_id("unknown") == {}
    assert model_env_for_id(None) == {}


def test_shipped_registry_loads():
    """The repo ships a models.yaml with known anchors."""
    import os
    os.environ.pop("ACC_MODELS_PATH", None)
    ids = {m.model_id for m in load_models()}
    assert "claude-sonnet" in ids


# ---------------------------------------------------------------------------
# collective integration — AgentSpec.model → per-agent env
# ---------------------------------------------------------------------------


def test_roles_to_compose_emits_model_env(registry):
    from acc.collective import AgentSpec, CollectiveSpec, roles_to_compose

    spec = CollectiveSpec(
        collective_id="sol-01",
        agents=[
            AgentSpec(role="coding_agent_implementer", replicas=1,
                      model="ollama-small"),
            AgentSpec(role="reviewer", replicas=1, model="claude-sonnet"),
        ],
    )
    out = roles_to_compose(spec, image="localhost/acc-agent-core:0.2.0")
    services = out["services"]
    # Find each agent's environment.
    envs = {name: svc["environment"] for name, svc in services.items()}
    worker = next(e for e in envs.values() if e["ACC_AGENT_ROLE"] == "coding_agent_implementer")
    reviewer = next(e for e in envs.values() if e["ACC_AGENT_ROLE"] == "reviewer")
    assert worker["ACC_LLM_BACKEND"] == "ollama"
    assert worker["ACC_OLLAMA_MODEL"] == "llama3.2:3b"
    assert reviewer["ACC_LLM_BACKEND"] == "anthropic"
    assert reviewer["ACC_ANTHROPIC_MODEL"] == "claude-sonnet-4-6"


def test_extra_env_overrides_model(registry):
    from acc.collective import AgentSpec, CollectiveSpec, roles_to_compose

    spec = CollectiveSpec(
        collective_id="sol-01",
        agents=[AgentSpec(
            role="reviewer", replicas=1, model="claude-sonnet",
            extra_env={"ACC_ANTHROPIC_MODEL": "claude-opus-override"},
        )],
    )
    out = roles_to_compose(spec, image="img")
    env = next(iter(out["services"].values()))["environment"]
    # extra_env applied after model_env → wins.
    assert env["ACC_ANTHROPIC_MODEL"] == "claude-opus-override"


def test_no_model_means_no_llm_env(registry):
    from acc.collective import AgentSpec, CollectiveSpec, roles_to_compose

    spec = CollectiveSpec(
        collective_id="sol-01",
        agents=[AgentSpec(role="analyst", replicas=1)],
    )
    out = roles_to_compose(spec, image="img")
    env = next(iter(out["services"].values()))["environment"]
    assert "ACC_LLM_BACKEND" not in env  # uses collective default
