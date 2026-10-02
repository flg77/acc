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


def test_agent_core_containerfiles_bake_models_yaml():
    """B6 regression guard (proposal 044, v0.5.18): the agent-core images MUST
    bake the model registry at ``/app/models.yaml`` — agent services don't mount
    it, so without the COPY apply_role_model_env() reads an empty registry and
    role_models never applies (the 29.6 lighthouse symptom: assistant stuck on
    the global 3B default).  IN-11e: the source is the TRACKED
    ``models.yaml.example``; `models.yaml` itself is a per-host file git does not
    track, so copying it built only from a developer's tree and failed from a
    clean clone or a `git archive` of a tag."""
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    for cf in (
        repo / "container" / "production" / "Containerfile.agent-core",
        repo / "container" / "beta" / "Containerfile.agent-core",
    ):
        text = cf.read_text(encoding="utf-8")
        assert "COPY models.yaml.example /app/models.yaml" in text, (
            f"{cf} must bake the registry at /app/models.yaml (B6 role_models "
            f"needs it baked; agent services do not mount it)"
        )
        assert "COPY models.yaml /app/models.yaml" not in text, (
            f"{cf} copies the untracked models.yaml -- it cannot build from a "
            f"clean clone or a tag (IN-11e)"
        )


# ---------------------------------------------------------------------------
# Operator path — the AgentCollective CR is the model source, not a registry
# ---------------------------------------------------------------------------
# Found on bb3 (2026-09-28, v0.25.1): the agent image bakes models.yaml.example
# as /app/models.yaml, whose role_models maps in-tree roles (analyst ->
# maas-qwen3-14b).  apply_role_model_env overwrote the operator's per-agent
# extraEnv with it, so the analyst called a model the cluster has no key for
# (401).  Under the operator the CR (spec.llm + agents[].extraEnv) says which
# model a role runs on; a registry applies only when ACC_MODELS_PATH names one.

_OPERATOR_EXTRA_ENV = {
    "ACC_AGENT_ROLE": "analyst",
    "ACC_CORPUS_NAME": "mortgage-agents-corpus",   # set by the operator on every agent
    "ACC_LLM_BACKEND": "openai_compat",
    "ACC_LLM_BASE_URL": "https://maas.example/v1",
    "ACC_LLM_MODEL": "gpt-oss-120b",
    "ACC_LLM_API_KEY_ENV": "MAAS_OSS120B_API_KEY",
}


def test_operator_pod_ignores_the_baked_registry(monkeypatch):
    """No ACC_MODELS_PATH: the resolver falls back to the shipped example, whose
    role_models maps the analyst elsewhere; the operator's extraEnv must stand."""
    from acc.models import apply_role_model_env, model_for_role
    monkeypatch.delenv("ACC_MODELS_PATH", raising=False)
    assert model_for_role("analyst"), "precondition: the shipped registry maps analyst"
    env = dict(_OPERATOR_EXTRA_ENV)
    assert apply_role_model_env(environ=env) == {}
    assert env == _OPERATOR_EXTRA_ENV


def test_operator_pod_with_an_explicit_registry_applies_it(registry_roles):
    """A registry the deployment names via ACC_MODELS_PATH is a deliberate choice
    and still applies on the operator path."""
    from acc.models import apply_role_model_env
    env = dict(_OPERATOR_EXTRA_ENV, ACC_AGENT_ROLE="assistant")   # role_models -> groq-70b
    applied = apply_role_model_env(environ=env)
    assert applied["ACC_LLM_MODEL"] == "llama-3.3-70b-versatile"
    assert env["ACC_LLM_API_KEY_ENV"] == "GROQ_API_KEY"


def test_role_models_apply_rule(tmp_path, monkeypatch):
    from acc.models import role_models_apply
    monkeypatch.delenv("ACC_MODELS_PATH", raising=False)
    assert role_models_apply(environ={}) is True                                   # edge / checkout
    assert role_models_apply(environ={"ACC_CORPUS_NAME": "c"}) is False            # operator, no registry named
    assert role_models_apply(environ={"ACC_CORPUS_NAME": "c", "ACC_MODELS_PATH": "/r.yaml"}) is True
    assert role_models_apply(environ={"ACC_CORPUS_NAME": "c"}, path=tmp_path / "m.yaml") is True


_BAKED_WITH_CHAIN = _REGISTRY + """\
role_models:
  analyst: [groq-70b, claude-sonnet]
"""


@pytest.fixture
def operator_pod_with_baked_registry(tmp_path, monkeypatch):
    """An operator pod whose registry fallback resolves to a baked file that maps
    the analyst (with a failover chain) — the bb3 situation."""
    baked = tmp_path / "app-models.yaml"
    baked.write_text(_BAKED_WITH_CHAIN, encoding="utf-8")
    monkeypatch.delenv("ACC_MODELS_PATH", raising=False)
    monkeypatch.setattr("acc.models.models_path", lambda: baked)
    for k, v in _OPERATOR_EXTRA_ENV.items():
        monkeypatch.setenv(k, v)
    return baked


def test_failover_chain_not_built_from_the_baked_registry(operator_pod_with_baked_registry):
    from unittest.mock import MagicMock
    from acc.llm_failover import wrap_for_role
    base = MagicMock(name="operator-configured backend")
    assert wrap_for_role(base, "analyst", MagicMock()) is base


def test_promotion_rebind_keeps_the_operator_model(operator_pod_with_baked_registry):
    """_reresolve_role_model (DORMANT -> ACTIVE, ROLE_ASSIGN) must not rebind an
    operator pod onto the baked registry's model."""
    import os
    from acc.agent import Agent
    agent = Agent.__new__(Agent)          # the method only needs its guard here
    agent._reresolve_role_model("analyst")
    assert os.environ["ACC_LLM_MODEL"] == "gpt-oss-120b"
    assert os.environ["ACC_LLM_API_KEY_ENV"] == "MAAS_OSS120B_API_KEY"
