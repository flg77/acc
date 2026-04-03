"""Tests for acc/config.py — loader, env overlay, validation, factory."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from acc.config import ACCConfig, load_config, _apply_env


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
