"""CS-07 -- the anthropic backend reaches Claude through Google Vertex AI.

`20261030-anthropic-vertex`: `llm.anthropic_provider: vertex` (or `provider:
vertex` on a models.yaml entry) builds an ``AsyncAnthropicVertex`` client that
authenticates with application default credentials and sends no API key.
"""

from __future__ import annotations

import sys
import types

import anthropic
import pytest

from acc.backends.llm_anthropic import AnthropicBackend


@pytest.fixture
def google_auth(monkeypatch):
    """A stand-in google.auth: the client is built, nothing is called."""
    google = types.ModuleType("google")
    auth = types.ModuleType("google.auth")
    google.auth = auth
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.auth", auth)
    return auth


def test_vertex_builds_a_vertex_client_with_project_and_region(google_auth, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
    b = AnthropicBackend("claude-sonnet", "/m", provider="vertex",
                         vertex_project_id="acme-ai", vertex_region="europe-west1")
    assert isinstance(b._client, anthropic.AsyncAnthropicVertex)
    assert (b._client.project_id, b._client.region) == ("acme-ai", "europe-west1")
    assert not getattr(b._client, "api_key", None)      # no API key rides along


def test_vertex_without_settings_defers_to_the_sdk_environment(google_auth, monkeypatch):
    monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "from-env")
    b = AnthropicBackend("claude-sonnet", "/m", provider="vertex")
    assert (b._client.project_id, b._client.region) == ("from-env", "us-east5")


def test_vertex_without_google_auth_says_which_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.auth", None)
    with pytest.raises(ImportError, match=r"acc\[vertex\]") as info:
        AnthropicBackend("claude-sonnet", "/m", provider="vertex", vertex_region="us-east5")
    assert info.value.name == "google.auth"


def test_the_default_provider_is_anthropics_api(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    b = AnthropicBackend("claude-sonnet", "/m")
    assert type(b._client) is anthropic.AsyncAnthropic and b._client.api_key == "sk-test"
    assert type(AnthropicBackend("claude-sonnet", "/m", provider="")._client) is anthropic.AsyncAnthropic


def test_an_unknown_provider_is_refused():
    with pytest.raises(ValueError, match="unknown provider 'bedrock'"):
        AnthropicBackend("claude-sonnet", "/m", provider="bedrock")


def test_config_env_selects_vertex_and_both_build_sites_pass_it(google_auth, monkeypatch, tmp_path):
    from acc.cli.llm_cmd import _build_llm_only_unrecorded
    from acc.config import _build_llm_backend_unrecorded, load_config

    cfg_file = tmp_path / "acc-config.yaml"
    cfg_file.write_text("deploy_mode: standalone\nagent:\n  role: analyst\nllm:\n  backend: anthropic\n",
                        encoding="utf-8")
    monkeypatch.setenv("ACC_ANTHROPIC_PROVIDER", "vertex")
    monkeypatch.setenv("ACC_VERTEX_PROJECT_ID", "acme-ai")
    monkeypatch.setenv("ACC_VERTEX_REGION", "europe-west1")
    config = load_config(cfg_file)
    assert (config.llm.anthropic_provider, config.llm.vertex_project_id,
            config.llm.vertex_region) == ("vertex", "acme-ai", "europe-west1")
    for build in (_build_llm_backend_unrecorded, _build_llm_only_unrecorded):
        backend = build(config)
        assert isinstance(backend._client, anthropic.AsyncAnthropicVertex), build.__name__
        assert backend._client.region == "europe-west1"


def test_an_unknown_provider_value_fails_config_validation(monkeypatch, tmp_path):
    from pydantic import ValidationError

    from acc.config import load_config

    cfg_file = tmp_path / "acc-config.yaml"
    cfg_file.write_text("deploy_mode: standalone\nagent:\n  role: analyst\nllm:\n  backend: anthropic\n"
                        "  anthropic_provider: bedrock\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(cfg_file)


def test_a_models_yaml_entry_carries_the_provider_into_the_agent_env():
    from acc.models import ModelEntry, model_env

    vertex = ModelEntry(model_id="claude-vertex", backend="anthropic", model="claude-sonnet",
                        provider="vertex", vertex_project_id="acme-ai", vertex_region="europe-west1")
    assert model_env(vertex) == {
        "ACC_LLM_BACKEND": "anthropic", "ACC_ANTHROPIC_MODEL": "claude-sonnet",
        "ACC_ANTHROPIC_PROVIDER": "vertex", "ACC_VERTEX_PROJECT_ID": "acme-ai",
        "ACC_VERTEX_REGION": "europe-west1",
    }
    # an API-key entry resets the provider, so a role moved off Vertex does not keep it
    api = ModelEntry(model_id="claude", backend="anthropic", model="claude-sonnet")
    assert model_env(api)["ACC_ANTHROPIC_PROVIDER"] == "anthropic"


def test_the_vertex_feature_brings_the_extra():
    from acc.features import load_features

    assert load_features()["vertex"].extras == ["vertex"]


class TestCredentialCheck:
    @pytest.fixture
    def cfg(self, tmp_path, monkeypatch):
        from tests.test_configstore import ACC_CONFIG, MODELS

        (tmp_path / "acc-config.yaml").write_text(ACC_CONFIG, encoding="utf-8", newline="")
        (tmp_path / "models.yaml").write_text(MODELS, encoding="utf-8", newline="")
        monkeypatch.setenv("ACC_CONFIG_PATH", str(tmp_path / "acc-config.yaml"))
        monkeypatch.setenv("ACC_MODELS_PATH", str(tmp_path / "models.yaml"))
        monkeypatch.setenv("ACC_COLLECTIVE_PATH", str(tmp_path / "collective.yaml"))
        monkeypatch.setenv("ACC_CATALOGS_PATH", str(tmp_path / "catalogs.yaml"))
        monkeypatch.setenv("ACC_ENV_PATH", str(tmp_path / ".env"))
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ACC_ANTHROPIC_PROVIDER", raising=False)
        return tmp_path

    def _key_errors(self):
        from acc import configstore as st

        return [f for f in st.check() if "ANTHROPIC_API_KEY" in f.message]

    def test_vertex_in_the_config_needs_no_api_key(self, cfg):
        from acc import configstore as st

        st.set_key("llm.backend", "anthropic")
        assert self._key_errors()                       # the API route still needs it
        st.set_key("llm.anthropic_provider", "vertex")
        assert not self._key_errors()

    def test_vertex_from_the_environment_needs_no_api_key(self, cfg, monkeypatch):
        from acc import configstore as st

        st.set_key("llm.backend", "anthropic")
        monkeypatch.setenv("ACC_ANTHROPIC_PROVIDER", "vertex")
        assert not self._key_errors()
