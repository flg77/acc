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


# ---------------------------------------------------------------------------
# B6 (proposal 044) — visible role→model mapping + runtime resolution
# ---------------------------------------------------------------------------

_REGISTRY_WITH_ROLES = _REGISTRY + """\
role_models:
  assistant: groq-70b
  reviewer: claude-sonnet
"""


@pytest.fixture
def registry_roles(tmp_path, monkeypatch):
    p = tmp_path / "models.yaml"
    p.write_text(_REGISTRY_WITH_ROLES, encoding="utf-8")
    monkeypatch.setenv("ACC_MODELS_PATH", str(p))
    return p


def test_load_role_models(registry_roles):
    from acc.models import load_role_models
    assert load_role_models() == {"assistant": "groq-70b", "reviewer": "claude-sonnet"}


def test_load_role_models_absent_block_is_empty(registry):
    # The base _REGISTRY has no role_models block.
    from acc.models import load_role_models
    assert load_role_models() == {}


def test_model_for_role(registry_roles):
    from acc.models import model_for_role
    assert model_for_role("assistant") == "groq-70b"
    assert model_for_role("reviewer") == "claude-sonnet"
    assert model_for_role("analyst") is None          # unmapped → global default
    assert model_for_role("") is None
    assert model_for_role(None) is None


def test_resolve_role_model_id_precedence(registry_roles):
    from acc.models import resolve_role_model_id
    # collective override wins over role_models
    assert resolve_role_model_id(
        "assistant", override_model_id="claude-sonnet") == "claude-sonnet"
    # no override → role_models mapping
    assert resolve_role_model_id("assistant") == "groq-70b"
    # unmapped + no override → None (global default)
    assert resolve_role_model_id("analyst") is None
    # blank override is ignored → falls through to role_models
    assert resolve_role_model_id("assistant", override_model_id="  ") == "groq-70b"


def test_apply_role_model_env_role_models(registry_roles):
    """role_models OVERRIDES the global default in the target environ."""
    from acc.models import apply_role_model_env
    env = {
        "ACC_AGENT_ROLE": "assistant",
        # a pre-existing global default that role_models must override:
        "ACC_LLM_BACKEND": "ollama",
        "ACC_OLLAMA_MODEL": "llama3.2:3b",
    }
    applied = apply_role_model_env(environ=env)
    assert applied["ACC_LLM_BACKEND"] == "openai_compat"   # groq-70b
    assert applied["ACC_LLM_MODEL"] == "llama-3.3-70b-versatile"
    assert env["ACC_LLM_BACKEND"] == "openai_compat"       # overlaid in place
    assert env["ACC_LLM_API_KEY_ENV"] == "GROQ_API_KEY"


def test_apply_role_model_env_collective_override_wins(registry_roles):
    """ACC_AGENT_MODEL_ID (collective override) beats the role_models mapping."""
    from acc.models import apply_role_model_env
    env = {
        "ACC_AGENT_ROLE": "assistant",          # role_models → groq-70b
        "ACC_AGENT_MODEL_ID": "claude-sonnet",  # but collective pins sonnet
    }
    applied = apply_role_model_env(environ=env)
    assert applied["ACC_LLM_BACKEND"] == "anthropic"
    assert applied["ACC_ANTHROPIC_MODEL"] == "claude-sonnet-4-6"


def test_apply_role_model_env_unmapped_is_noop(registry_roles):
    """An unmapped role with no override leaves the global default untouched."""
    from acc.models import apply_role_model_env
    env = {"ACC_AGENT_ROLE": "analyst", "ACC_LLM_BACKEND": "ollama"}
    assert apply_role_model_env(environ=env) == {}
    assert env == {"ACC_AGENT_ROLE": "analyst", "ACC_LLM_BACKEND": "ollama"}


def test_role_models_resolves_in_compose_when_no_agent_model(registry_roles):
    """compose-gen applies role_models when AgentSpec.model is unset."""
    from acc.collective import AgentSpec, CollectiveSpec, roles_to_compose
    spec = CollectiveSpec(
        collective_id="sol-01",
        agents=[AgentSpec(role="reviewer", replicas=1)],  # no .model
    )
    out = roles_to_compose(spec, image="img")
    env = next(iter(out["services"].values()))["environment"]
    assert env["ACC_LLM_BACKEND"] == "anthropic"          # role_models → claude-sonnet
    assert env["ACC_ANTHROPIC_MODEL"] == "claude-sonnet-4-6"
    assert "ACC_AGENT_MODEL_ID" not in env                # no explicit override marker


def test_agent_model_marks_override_in_compose(registry_roles):
    """An explicit AgentSpec.model wins AND sets the ACC_AGENT_MODEL_ID marker."""
    from acc.collective import AgentSpec, CollectiveSpec, roles_to_compose
    spec = CollectiveSpec(
        collective_id="sol-01",
        # reviewer maps to claude-sonnet in role_models, but pin it to groq-70b:
        agents=[AgentSpec(role="reviewer", replicas=1, model="groq-70b")],
    )
    out = roles_to_compose(spec, image="img")
    env = next(iter(out["services"].values()))["environment"]
    assert env["ACC_AGENT_MODEL_ID"] == "groq-70b"        # override marker
    assert env["ACC_LLM_BACKEND"] == "openai_compat"      # groq-70b, not sonnet
