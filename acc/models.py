"""Central model registry (PR-MM1).

A multimodel agentset assigns different LLM models to different
sub-agents (cheap/fast workers + a powerful reviewer).  Rather than
hand-editing per-agent env, the operator references a **model_id** from
a central ``models.yaml`` registry; :func:`model_env` turns that into
the LLM env vars the agent reads at boot, and the Agentset dropdown
(PR-MM2) picks from the registry.

Registry schema (``models.yaml`` at the repo root)::

    models:
      - model_id: claude-sonnet
        backend: anthropic
        model: claude-sonnet-4-6
        label: "Claude Sonnet (powerful — reviewer)"
      - model_id: ollama-llama32
        backend: ollama
        model: llama3.2:3b
        label: "Ollama Llama 3.2 3B (cheap — worker)"
      - model_id: groq-70b
        backend: openai_compat
        model: llama-3.3-70b-versatile
        base_url: https://api.groq.com/openai/v1
        api_key_env: GROQ_API_KEY
        label: "Groq Llama 3.3 70B"
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("acc.models")

_BACKENDS = {"anthropic", "ollama", "vllm", "openai_compat", "llama_stack"}


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(..., min_length=1)
    backend: str
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""
    label: str = ""
    notes: str = ""

    def display(self) -> str:
        return self.label or f"{self.model_id} ({self.backend})"


class ModelRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[ModelEntry] = Field(default_factory=list)


def models_path() -> Path:
    """Resolve the central ``models.yaml``.

    Precedence: ``ACC_MODELS_PATH`` env > ``<repo>/models.yaml`` >
    ``/app/models.yaml`` (the in-container mount).
    """
    raw = os.environ.get("ACC_MODELS_PATH", "").strip()
    if raw:
        return Path(raw)
    repo_root = Path(__file__).resolve().parent.parent
    candidate = repo_root / "models.yaml"
    if candidate.is_file():
        return candidate
    return Path("/app/models.yaml")


def load_models(path: Optional[Path] = None) -> list[ModelEntry]:
    """Load + validate the model registry.  Best-effort: a missing or
    malformed file yields an empty list (the Agentset dropdown then just
    offers the collective default)."""
    p = path or models_path()
    try:
        raw = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("models: cannot read %s (%s)", p, exc)
        return []
    try:
        registry = ModelRegistry.model_validate(raw)
    except Exception as exc:
        logger.warning("models: invalid registry %s (%s)", p, exc)
        return []
    return registry.models


def get_model(model_id: str, path: Optional[Path] = None) -> Optional[ModelEntry]:
    for entry in load_models(path):
        if entry.model_id == model_id:
            return entry
    return None


def model_env(entry: ModelEntry) -> dict[str, str]:
    """Translate a registry entry into the per-agent LLM env vars.

    Maps onto the env overrides ``acc.config`` already recognises
    (ACC_LLM_BACKEND + the backend-specific model/url/key vars), so a
    synthesized agent container boots on exactly this model.
    """
    env: dict[str, str] = {"ACC_LLM_BACKEND": entry.backend}
    b = entry.backend
    if b == "anthropic":
        if entry.model:
            env["ACC_ANTHROPIC_MODEL"] = entry.model
    elif b == "ollama":
        if entry.model:
            env["ACC_OLLAMA_MODEL"] = entry.model
        if entry.base_url:
            env["ACC_OLLAMA_BASE_URL"] = entry.base_url
    else:  # openai_compat / vllm / llama_stack — universal fields
        if entry.model:
            env["ACC_LLM_MODEL"] = entry.model
        if entry.base_url:
            env["ACC_LLM_BASE_URL"] = entry.base_url
        if entry.api_key_env:
            env["ACC_LLM_API_KEY_ENV"] = entry.api_key_env
    return env


def model_env_for_id(
    model_id: Optional[str], path: Optional[Path] = None,
) -> dict[str, str]:
    """Convenience: env vars for a model_id, or ``{}`` when unset/unknown
    (the agent then falls back to the collective default model)."""
    if not model_id:
        return {}
    entry = get_model(model_id, path)
    if entry is None:
        logger.warning("models: unknown model_id %r — using default", model_id)
        return {}
    return model_env(entry)
