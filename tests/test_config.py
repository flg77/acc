"""Tests for acc/config.py — loader, env overlay, validation, factory."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from acc.config import (
    ACCConfig,
    NKeyConfig,
    RoleDefinitionConfig,
    RoleSyncConfig,
    SecurityConfig,
    SpiffeConfig,
    WorkingMemoryConfig,
    _apply_env,
    _ROLE_SOURCE_BY_DEPLOY_MODE,
    _SIGNING_MODE_BY_DEPLOY_MODE,
    load_config,
)


# ---------------------------------------------------------------------------
# ACCConfig validation
# ---------------------------------------------------------------------------


class TestACCConfigValidation:
    def test_standalone_defaults(self):
        config = ACCConfig()
        assert config.deploy_mode == "standalone"
        assert config.agent.role == "ingester"
        assert config.signaling.nats_url == "nats://localhost:4222"

    def test_rhoai_requires_milvus_uri(self):
        with pytest.raises(ValidationError, match="milvus_uri"):
            ACCConfig.model_validate({
                "deploy_mode": "rhoai",
                "vector_db": {"milvus_uri": ""},
                "llm": {"vllm_inference_url": "http://vllm:8000"},
            })

    def test_rhoai_requires_llm_url(self):
        with pytest.raises(ValidationError, match="vllm_inference_url.*llama_stack_url"):
            ACCConfig.model_validate({
                "deploy_mode": "rhoai",
                "vector_db": {"milvus_uri": "http://milvus:19530"},
                "llm": {"vllm_inference_url": "", "llama_stack_url": ""},
            })

    def test_rhoai_valid(self):
        config = ACCConfig.model_validate({
            "deploy_mode": "rhoai",
            "vector_db": {"milvus_uri": "http://milvus:19530"},
            "llm": {"vllm_inference_url": "http://vllm:8000"},
        })
        assert config.deploy_mode == "rhoai"

    def test_invalid_role(self):
        with pytest.raises(ValidationError):
            ACCConfig.model_validate({"agent": {"role": "overlord"}})


# ---------------------------------------------------------------------------
# load_config()
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_load_valid_yaml(self, tmp_path: Path):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text(
            "deploy_mode: standalone\n"
            "agent:\n  role: arbiter\n"
        )
        config = load_config(cfg_file)
        assert config.agent.role == "arbiter"

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nonexistent.yaml")

    def test_env_overlay(self, tmp_path: Path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text("deploy_mode: standalone\n")
        monkeypatch.setenv("ACC_AGENT_ROLE", "analyst")
        config = load_config(cfg_file)
        assert config.agent.role == "analyst"

    def test_env_overlay_nats_url(self, tmp_path: Path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text("deploy_mode: standalone\n")
        monkeypatch.setenv("ACC_NATS_URL", "nats://custom:4222")
        config = load_config(cfg_file)
        assert config.signaling.nats_url == "nats://custom:4222"


# ---------------------------------------------------------------------------
# _apply_env()
# ---------------------------------------------------------------------------


class TestApplyEnv:
    def test_applies_deploy_mode(self, monkeypatch):
        monkeypatch.setenv("ACC_DEPLOY_MODE", "rhoai")
        data = _apply_env({})
        assert data["deploy_mode"] == "rhoai"

    def test_nested_key(self, monkeypatch):
        monkeypatch.setenv("ACC_MILVUS_URI", "http://milvus:19530")
        data = _apply_env({})
        assert data["vector_db"]["milvus_uri"] == "http://milvus:19530"

    def test_no_env_vars_leaves_data_unchanged(self, monkeypatch):
        for k in list(os.environ.keys()):
            if k.startswith("ACC_"):
                monkeypatch.delenv(k, raising=False)
        data = _apply_env({"deploy_mode": "standalone"})
        assert data == {"deploy_mode": "standalone"}

    def test_role_purpose_env_var(self, monkeypatch):
        monkeypatch.setenv("ACC_ROLE_PURPOSE", "custom purpose")
        data = _apply_env({})
        assert data["role_definition"]["purpose"] == "custom purpose"

    def test_role_persona_env_var(self, monkeypatch):
        monkeypatch.setenv("ACC_ROLE_PERSONA", "formal")
        data = _apply_env({})
        assert data["role_definition"]["persona"] == "formal"

    def test_role_version_env_var(self, monkeypatch):
        monkeypatch.setenv("ACC_ROLE_VERSION", "1.2.3")
        data = _apply_env({})
        assert data["role_definition"]["version"] == "1.2.3"

    def test_role_source_env_var(self, monkeypatch):
        monkeypatch.setenv("ACC_ROLE_SOURCE", "mirror")
        data = _apply_env({})
        assert data["role_sync"]["role_source"] == "mirror"

    def test_role_sync_conflict_window_env_var(self, monkeypatch):
        monkeypatch.setenv("ACC_ROLE_SYNC_CONFLICT_WINDOW_S", "5.0")
        data = _apply_env({})
        # _apply_env stores the raw string; Pydantic coerces to float
        # at model_validate time.
        assert data["role_sync"]["conflict_window_s"] == "5.0"


# ---------------------------------------------------------------------------
# RoleSyncConfig + role_source resolution (proposal 010)
# ---------------------------------------------------------------------------


class TestRoleSyncDefaults:
    """Resolver: `role_source: auto` becomes the deploy-mode default."""

    def test_default_is_auto(self):
        rs = RoleSyncConfig()
        assert rs.role_source == "auto"
        assert rs.conflict_window_s == 2.0
        assert rs.events_subject == "acc.role.sync"

    def test_standalone_resolves_to_files(self):
        config = ACCConfig()  # deploy_mode defaults to standalone
        assert config.role_sync.role_source == "files"

    def test_edge_resolves_to_mirror(self):
        config = ACCConfig.model_validate({"deploy_mode": "edge"})
        assert config.role_sync.role_source == "mirror"

    def test_rhoai_resolves_to_crd(self):
        config = ACCConfig.model_validate({
            "deploy_mode": "rhoai",
            "vector_db": {"milvus_uri": "http://milvus:19530"},
            "llm": {"vllm_inference_url": "http://vllm:8000"},
        })
        assert config.role_sync.role_source == "crd"

    def test_explicit_value_preserved(self):
        """Operator's explicit override beats the deploy-mode default."""
        config = ACCConfig.model_validate({
            "deploy_mode": "standalone",
            "role_sync": {"role_source": "crd"},
        })
        assert config.role_sync.role_source == "crd"

    def test_explicit_mirror_in_rhoai(self):
        """Operators can pick `mirror` in any deploy mode."""
        config = ACCConfig.model_validate({
            "deploy_mode": "rhoai",
            "vector_db": {"milvus_uri": "http://milvus:19530"},
            "llm": {"vllm_inference_url": "http://vllm:8000"},
            "role_sync": {"role_source": "mirror"},
        })
        assert config.role_sync.role_source == "mirror"

    def test_invalid_role_source_rejected(self):
        with pytest.raises(ValidationError, match="role_source"):
            ACCConfig.model_validate({
                "role_sync": {"role_source": "configmap"},
            })

    def test_resolution_table_covers_all_deploy_modes(self):
        """If a new deploy_mode is added, this test fails until the
        resolver table is updated."""
        from acc.config import DeployMode
        from typing import get_args
        for mode in get_args(DeployMode):
            assert mode in _ROLE_SOURCE_BY_DEPLOY_MODE, (
                f"deploy_mode {mode!r} missing from "
                "_ROLE_SOURCE_BY_DEPLOY_MODE — update proposal 010 §4 table"
            )

    def test_env_var_overrides_yaml(self, tmp_path: Path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text(
            "deploy_mode: standalone\n"
            "role_sync:\n  role_source: files\n"
        )
        monkeypatch.setenv("ACC_ROLE_SOURCE", "crd")
        config = load_config(cfg_file)
        assert config.role_sync.role_source == "crd"

    def test_conflict_window_is_float(self):
        config = ACCConfig.model_validate({
            "role_sync": {"conflict_window_s": "3.5"},  # string coerced
        })
        assert config.role_sync.conflict_window_s == 3.5

    def test_events_subject_customisable(self):
        config = ACCConfig.model_validate({
            "role_sync": {"events_subject": "custom.role.sync"},
        })
        assert config.role_sync.events_subject == "custom.role.sync"


# ---------------------------------------------------------------------------
# RoleDefinitionConfig validation (ACC-6a)
# ---------------------------------------------------------------------------


class TestRoleDefinitionConfig:
    def test_default_is_valid(self):
        role = RoleDefinitionConfig()
        assert role.purpose == ""
        assert role.persona == "concise"
        assert role.version == "0.1.0"
        assert role.task_types == []
        assert role.allowed_actions == []
        assert role.category_b_overrides == {}

    def test_config_without_role_definition_validates(self):
        config = ACCConfig.model_validate({})
        assert config.role_definition is not None
        assert isinstance(config.role_definition, RoleDefinitionConfig)

    def test_invalid_persona_raises_validation_error(self):
        with pytest.raises(ValidationError):
            RoleDefinitionConfig.model_validate({"persona": "aggressive"})

    def test_all_valid_personas(self):
        for persona in ("concise", "formal", "exploratory", "analytical"):
            role = RoleDefinitionConfig.model_validate({"persona": persona})
            assert role.persona == persona

    def test_role_definition_section_in_acc_config(self):
        config = ACCConfig.model_validate({
            "role_definition": {
                "purpose": "test purpose",
                "persona": "formal",
                "version": "0.2.0",
            }
        })
        assert config.role_definition.purpose == "test purpose"
        assert config.role_definition.persona == "formal"
        assert config.role_definition.version == "0.2.0"

    def test_category_b_overrides_accepts_floats(self):
        role = RoleDefinitionConfig.model_validate({
            "category_b_overrides": {"token_budget": 2048.0, "rate_limit_rpm": 60.0}
        })
        assert role.category_b_overrides["token_budget"] == 2048.0

    def test_new_agent_roles_valid(self):
        """synthesizer and observer roles added in ACC-6a must validate."""
        from acc.config import ACCConfig
        for role in ("synthesizer", "observer"):
            config = ACCConfig.model_validate({"agent": {"role": role}})
            assert config.agent.role == role


# ---------------------------------------------------------------------------
# WorkingMemoryConfig validation (Phase 0b)
# ---------------------------------------------------------------------------


class TestWorkingMemoryConfig:
    def test_defaults_to_empty_url_and_password(self):
        cfg = WorkingMemoryConfig()
        assert cfg.url == ""
        assert cfg.password == ""

    def test_acc_config_has_working_memory_field(self):
        config = ACCConfig()
        assert isinstance(config.working_memory, WorkingMemoryConfig)

    def test_working_memory_section_parsed_from_dict(self):
        config = ACCConfig.model_validate({
            "working_memory": {"url": "redis://localhost:6379", "password": "s3cr3t"},
        })
        assert config.working_memory.url == "redis://localhost:6379"
        assert config.working_memory.password == "s3cr3t"

    def test_acc_redis_url_env_var_applied(self, monkeypatch):
        monkeypatch.setenv("ACC_REDIS_URL", "redis://acc-redis:6379")
        data = _apply_env({})
        assert data["working_memory"]["url"] == "redis://acc-redis:6379"

    def test_acc_redis_password_env_var_applied(self, monkeypatch):
        monkeypatch.setenv("ACC_REDIS_PASSWORD", "hunter2")
        data = _apply_env({})
        assert data["working_memory"]["password"] == "hunter2"

    def test_empty_url_keeps_password_empty_by_default(self):
        config = ACCConfig.model_validate({"working_memory": {"url": ""}})
        assert config.working_memory.password == ""

    def test_password_without_url_is_valid(self):
        """Password can be set even if URL is empty (both handled gracefully)."""
        config = ACCConfig.model_validate({
            "working_memory": {"url": "", "password": "somepass"},
        })
        assert config.working_memory.password == "somepass"


# ---------------------------------------------------------------------------
# SpiffeConfig + signing_mode resolver (proposal 011 PR-1)
# ---------------------------------------------------------------------------


class TestSpiffeDefaults:
    """SpiffeConfig defaults + signing_mode auto-resolution."""

    def test_spiffe_defaults_are_inert(self):
        sp = SpiffeConfig()
        assert sp.enabled is False
        assert sp.trust_domain == ""
        assert sp.svid_mount_path == "/run/spire/sockets"
        assert sp.jwt_audience == "acc-role-update"
        assert sp.allow_ed25519_fallback is True

    def test_security_default_signing_mode_is_auto(self):
        sec = SecurityConfig()
        assert sec.signing_mode == "auto"
        # Spiffe block is present + inert.
        assert isinstance(sec.spiffe, SpiffeConfig)
        assert sec.spiffe.enabled is False

    def test_standalone_resolves_to_ed25519(self):
        cfg = ACCConfig()  # deploy_mode defaults to standalone
        assert cfg.security.signing_mode == "ed25519"

    def test_edge_resolves_to_ed25519(self):
        cfg = ACCConfig.model_validate({"deploy_mode": "edge"})
        assert cfg.security.signing_mode == "ed25519"

    def test_rhoai_resolves_to_ed25519_in_v04x(self):
        """v0.4.x: every deploy_mode still defaults to ed25519.
        v0.5.0 flips the rhoai row to 'spiffe' (proposal 011 §2 G6)."""
        cfg = ACCConfig.model_validate({
            "deploy_mode": "rhoai",
            "vector_db": {"milvus_uri": "http://milvus:19530"},
            "llm": {"vllm_inference_url": "http://vllm:8000"},
        })
        assert cfg.security.signing_mode == "ed25519"

    def test_explicit_spiffe_preserved(self):
        """Operator's explicit override beats the deploy-mode default."""
        cfg = ACCConfig.model_validate({
            "security": {"signing_mode": "spiffe"},
        })
        assert cfg.security.signing_mode == "spiffe"

    def test_explicit_ed25519_preserved(self):
        """Even when rhoai eventually defaults to spiffe, operators
        who pick ed25519 explicitly stay on it."""
        cfg = ACCConfig.model_validate({
            "deploy_mode": "rhoai",
            "vector_db": {"milvus_uri": "http://milvus:19530"},
            "llm": {"vllm_inference_url": "http://vllm:8000"},
            "security": {"signing_mode": "ed25519"},
        })
        assert cfg.security.signing_mode == "ed25519"

    def test_invalid_signing_mode_rejected(self):
        with pytest.raises(ValidationError, match="signing_mode"):
            ACCConfig.model_validate({
                "security": {"signing_mode": "x509"},
            })

    def test_spiffe_block_propagates(self):
        """All five SpiffeConfig fields round-trip through ACCConfig."""
        cfg = ACCConfig.model_validate({
            "security": {
                "signing_mode": "spiffe",
                "spiffe": {
                    "enabled": True,
                    "trust_domain": "acc-prod.example.com",
                    "svid_mount_path": "/var/run/spire",
                    "jwt_audience": "custom-audience",
                    "allow_ed25519_fallback": False,
                },
            },
        })
        assert cfg.security.spiffe.enabled is True
        assert cfg.security.spiffe.trust_domain == "acc-prod.example.com"
        assert cfg.security.spiffe.svid_mount_path == "/var/run/spire"
        assert cfg.security.spiffe.jwt_audience == "custom-audience"
        assert cfg.security.spiffe.allow_ed25519_fallback is False

    def test_arbiter_verify_key_still_works(self):
        """Existing v0.3.x deployments with arbiter_verify_key set
        but no SPIFFE config must keep working unchanged."""
        cfg = ACCConfig.model_validate({
            "security": {"arbiter_verify_key": "BASE64ED25519KEY="},
        })
        assert cfg.security.arbiter_verify_key == "BASE64ED25519KEY="
        assert cfg.security.signing_mode == "ed25519"

    def test_signing_mode_resolver_covers_all_deploy_modes(self):
        """Meta-test: if a new deploy_mode is added, this fails until
        _SIGNING_MODE_BY_DEPLOY_MODE is updated (proposal 011 §5)."""
        from acc.config import DeployMode
        from typing import get_args
        for mode in get_args(DeployMode):
            assert mode in _SIGNING_MODE_BY_DEPLOY_MODE, (
                f"deploy_mode {mode!r} missing from "
                "_SIGNING_MODE_BY_DEPLOY_MODE — update proposal 011 plan"
            )

    def test_env_var_overrides_signing_mode(self, tmp_path: Path, monkeypatch):
        cfg_file = tmp_path / "acc-config.yaml"
        cfg_file.write_text("deploy_mode: standalone\n")
        monkeypatch.setenv("ACC_SIGNING_MODE", "spiffe")
        cfg = load_config(cfg_file)
        assert cfg.security.signing_mode == "spiffe"

    def test_env_var_overrides_spiffe_enabled(self, monkeypatch):
        monkeypatch.setenv("ACC_SPIFFE_ENABLED", "true")
        data = _apply_env({})
        assert data["security"]["spiffe"]["enabled"] == "true"
        # Pydantic coerces the string to bool at validate time.
        cfg = ACCConfig.model_validate(data)
        assert cfg.security.spiffe.enabled is True

    def test_env_var_overrides_trust_domain(self, monkeypatch):
        monkeypatch.setenv("ACC_SPIFFE_TRUST_DOMAIN", "acc-prod.example.com")
        data = _apply_env({})
        assert data["security"]["spiffe"]["trust_domain"] == "acc-prod.example.com"

    def test_env_var_overrides_allow_ed25519_fallback(self, monkeypatch):
        monkeypatch.setenv("ACC_SPIFFE_ALLOW_ED25519_FALLBACK", "false")
        data = _apply_env({})
        cfg = ACCConfig.model_validate(data)
        assert cfg.security.spiffe.allow_ed25519_fallback is False


# ---------------------------------------------------------------------------
# NATS NKey authentication (proposal 013 PR-2)
# ---------------------------------------------------------------------------


class TestNKeyDefaults:
    """NKeyConfig defaults + env overlay.  PR-2 is inert: every field
    defaults to the connection-unchanged posture."""

    def test_nkey_defaults_are_inert(self):
        nk = NKeyConfig()
        assert nk.enabled is False
        assert nk.seed_path == "/run/acc/nkeys/seed"
        assert nk.role == ""
        assert nk.leaf_seed_path == ""

    def test_security_carries_inert_nkey_block(self):
        sec = SecurityConfig()
        assert isinstance(sec.nkey, NKeyConfig)
        assert sec.nkey.enabled is False

    def test_nkey_block_propagates(self):
        cfg = ACCConfig.model_validate({
            "security": {
                "nkey": {
                    "enabled": True,
                    "seed_path": "/etc/acc/seed-arbiter",
                    "role": "arbiter",
                    "leaf_seed_path": "/etc/acc/seed-leaf",
                },
            },
        })
        assert cfg.security.nkey.enabled is True
        assert cfg.security.nkey.seed_path == "/etc/acc/seed-arbiter"
        assert cfg.security.nkey.role == "arbiter"
        assert cfg.security.nkey.leaf_seed_path == "/etc/acc/seed-leaf"

    def test_nkey_env_overlay(self):
        # _apply_env reads os.environ directly; exercise it explicitly.
        env = {
            "ACC_NKEY_ENABLED": "true",
            "ACC_NKEY_SEED_PATH": "/run/x/seed",
            "ACC_NKEY_ROLE": "analyst",
            "ACC_NKEY_LEAF_SEED_PATH": "/run/x/leaf",
        }
        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            overlaid = _apply_env({})
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        nk = overlaid["security"]["nkey"]
        assert nk["enabled"] == "true"
        assert nk["seed_path"] == "/run/x/seed"
        assert nk["role"] == "analyst"
        assert nk["leaf_seed_path"] == "/run/x/leaf"


# ---------------------------------------------------------------------------
# Edge SPIFFE topology + cross-field validators (proposal 012 PR-1)
# ---------------------------------------------------------------------------


class TestSpiffeEdgeDefaults:
    """Edge-specific fields on SpiffeConfig + cross-field validators."""

    def test_edge_field_defaults(self):
        sp = SpiffeConfig()
        assert sp.edge_topology == "nested"
        assert sp.edge_site_id == ""
        assert sp.parent_spire_url == ""
        assert sp.federation_peers == []
        assert sp.offline_bundle_cache_path == "/run/spire/cache/bundle.pem"
        assert sp.offline_max_age_h == 72.0
        assert sp.bundle_refresh_h == 6.0
        assert sp.offline_action == "rotate"
        assert sp.parent_unreachable_action == "degrade"
        assert sp.nats_mtls_cert_path == ""
        assert sp.nats_mtls_key_path == ""

    def test_inert_spiffe_on_edge_no_topology_check(self):
        """SPIFFE off in edge mode: no edge_topology validation fires."""
        cfg = ACCConfig.model_validate({"deploy_mode": "edge"})
        assert cfg.security.spiffe.enabled is False

    def test_signing_mode_ed25519_on_edge_skips_topology_check(self):
        """spiffe.enabled=True but signing_mode=ed25519: the
        edge_topology constraints do not fire because the operator
        explicitly chose not to consume SPIFFE."""
        cfg = ACCConfig.model_validate({
            "deploy_mode": "edge",
            "security": {
                "signing_mode": "ed25519",
                "spiffe": {"enabled": True, "edge_topology": "nested"},
            },
        })
        assert cfg.security.signing_mode == "ed25519"

    def test_nested_requires_parent_spire_url(self):
        with pytest.raises(ValidationError, match="parent_spire_url"):
            ACCConfig.model_validate({
                "deploy_mode": "edge",
                "security": {
                    "signing_mode": "spiffe",
                    "spiffe": {
                        "enabled": True,
                        "edge_topology": "nested",
                        "edge_site_id": "factory-a",
                    },
                },
            })

    def test_nested_requires_edge_site_id(self):
        with pytest.raises(ValidationError, match="edge_site_id"):
            ACCConfig.model_validate({
                "deploy_mode": "edge",
                "security": {
                    "signing_mode": "spiffe",
                    "spiffe": {
                        "enabled": True,
                        "edge_topology": "nested",
                        "parent_spire_url": "spire:8081",
                    },
                },
            })

    def test_nested_happy_path(self):
        cfg = ACCConfig.model_validate({
            "deploy_mode": "edge",
            "security": {
                "signing_mode": "spiffe",
                "spiffe": {
                    "enabled": True,
                    "edge_topology": "nested",
                    "edge_site_id": "factory-a",
                    "parent_spire_url": "spire-server.acc-system:8081",
                },
            },
        })
        assert cfg.security.spiffe.edge_topology == "nested"
        assert cfg.security.spiffe.edge_site_id == "factory-a"

    def test_federated_requires_peers(self):
        with pytest.raises(ValidationError, match="federation_peers"):
            ACCConfig.model_validate({
                "deploy_mode": "edge",
                "security": {
                    "signing_mode": "spiffe",
                    "spiffe": {
                        "enabled": True,
                        "edge_topology": "federated",
                    },
                },
            })

    def test_federated_happy_path(self):
        # Federated topology + rotate is rejected (see test_rotate_requires_nested);
        # use degrade instead.
        cfg = ACCConfig.model_validate({
            "deploy_mode": "edge",
            "security": {
                "signing_mode": "spiffe",
                "spiffe": {
                    "enabled": True,
                    "edge_topology": "federated",
                    "federation_peers": ["https://factory-b.example.com/bundle"],
                    "offline_action": "degrade",
                },
            },
        })
        assert cfg.security.spiffe.federation_peers == [
            "https://factory-b.example.com/bundle",
        ]

    def test_rotate_requires_nested(self):
        """offline_action=rotate is only meaningful with a local edge
        SPIRE server, i.e. nested topology."""
        with pytest.raises(ValidationError, match="rotate.*nested"):
            ACCConfig.model_validate({
                "deploy_mode": "edge",
                "security": {
                    "signing_mode": "spiffe",
                    "spiffe": {
                        "enabled": True,
                        "edge_topology": "federated",
                        "federation_peers": ["https://x/bundle"],
                        "offline_action": "rotate",
                    },
                },
            })

    def test_degrade_works_with_federated(self):
        """offline_action=degrade is topology-agnostic."""
        cfg = ACCConfig.model_validate({
            "deploy_mode": "edge",
            "security": {
                "signing_mode": "spiffe",
                "spiffe": {
                    "enabled": True,
                    "edge_topology": "federated",
                    "federation_peers": ["https://x/bundle"],
                    "offline_action": "degrade",
                },
            },
        })
        assert cfg.security.spiffe.offline_action == "degrade"

    def test_non_edge_deploy_mode_skips_topology_check(self):
        """Standalone + rhoai deployments do not care about
        edge_topology constraints even when they enable SPIFFE."""
        for mode_kwargs in [
            {"deploy_mode": "standalone"},
            {
                "deploy_mode": "rhoai",
                "vector_db": {"milvus_uri": "http://milvus:19530"},
                "llm": {"vllm_inference_url": "http://vllm:8000"},
            },
        ]:
            cfg = ACCConfig.model_validate({
                **mode_kwargs,
                "security": {
                    "signing_mode": "spiffe",
                    "spiffe": {
                        "enabled": True,
                        "edge_topology": "nested",
                    },
                },
            })
            assert cfg.security.spiffe.enabled is True

    def test_invalid_edge_topology_rejected(self):
        with pytest.raises(ValidationError, match="edge_topology"):
            SpiffeConfig.model_validate({"edge_topology": "lightning_grid"})

    def test_invalid_offline_action_rejected(self):
        with pytest.raises(ValidationError, match="offline_action"):
            SpiffeConfig.model_validate({"offline_action": "implode"})

    def test_env_var_overrides_edge_topology(self, monkeypatch):
        monkeypatch.setenv("ACC_SPIFFE_EDGE_TOPOLOGY", "federated")
        data = _apply_env({})
        assert data["security"]["spiffe"]["edge_topology"] == "federated"

    def test_env_var_overrides_edge_site_id(self, monkeypatch):
        monkeypatch.setenv("ACC_SPIFFE_EDGE_SITE_ID", "plant-mke")
        data = _apply_env({})
        assert data["security"]["spiffe"]["edge_site_id"] == "plant-mke"

    def test_env_var_overrides_offline_action(self, monkeypatch):
        monkeypatch.setenv("ACC_SPIFFE_OFFLINE_ACTION", "shutdown")
        data = _apply_env({})
        assert data["security"]["spiffe"]["offline_action"] == "shutdown"

    def test_env_var_overrides_parent_unreachable_action(self, monkeypatch):
        monkeypatch.setenv("ACC_SPIFFE_PARENT_UNREACHABLE_ACTION", "block")
        data = _apply_env({})
        assert data["security"]["spiffe"]["parent_unreachable_action"] == "block"

    def test_env_var_overrides_nats_mtls_paths(self, monkeypatch):
        monkeypatch.setenv("ACC_NATS_MTLS_CERT_PATH", "/etc/acc/nats.crt")
        monkeypatch.setenv("ACC_NATS_MTLS_KEY_PATH", "/etc/acc/nats.key")
        data = _apply_env({})
        assert data["security"]["spiffe"]["nats_mtls_cert_path"] == "/etc/acc/nats.crt"
        assert data["security"]["spiffe"]["nats_mtls_key_path"] == "/etc/acc/nats.key"

    def test_env_var_overrides_offline_max_age_h(self, monkeypatch):
        monkeypatch.setenv("ACC_SPIFFE_OFFLINE_MAX_AGE_H", "168.0")
        data = _apply_env({})
        cfg = ACCConfig.model_validate(data)
        assert cfg.security.spiffe.offline_max_age_h == 168.0
