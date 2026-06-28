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
    # B6 (proposal 044) — the ONE visible source of truth for which model
    # each role runs on.  Maps a role name → a ``model_id`` from ``models``
    # above.  Roles absent here fall back to the global ``ACC_LLM_*``
    # default.  A ``collective.yaml`` per-agent ``model:`` still OVERRIDES
    # this mapping (precedence: collective override > role_models > global
    # default) — see :func:`apply_role_model_env`.
    role_models: dict[str, str] = Field(default_factory=dict)


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


# ---------------------------------------------------------------------------
# B6 (proposal 044) — visible role→model mapping + runtime resolution
# ---------------------------------------------------------------------------


def load_role_models(path: Optional[Path] = None) -> dict[str, str]:
    """The ``role_models`` mapping from ``models.yaml`` (role → model_id).

    Best-effort: a missing/malformed file or absent block yields ``{}``,
    so every caller degrades to the global default model."""
    p = path or models_path()
    try:
        raw = yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("models: cannot read %s (%s)", p, exc)
        return {}
    block = raw.get("role_models") if isinstance(raw, dict) else None
    if not isinstance(block, dict):
        return {}
    # Coerce to str→str; drop empties so an explicit blank never shadows the
    # global default.
    return {
        str(k): str(v)
        for k, v in block.items()
        if str(k).strip() and str(v).strip()
    }


def model_for_role(
    role: Optional[str], path: Optional[Path] = None,
) -> Optional[str]:
    """The ``model_id`` mapped to ``role`` in ``models.yaml`` ``role_models``,
    or ``None`` when the role is unmapped (→ global default).  Pure lookup —
    does NOT consult the collective override; see :func:`resolve_role_model_id`."""
    if not role:
        return None
    return load_role_models(path).get(role) or None


def resolve_role_model_id(
    role: Optional[str],
    *,
    override_model_id: Optional[str] = None,
    path: Optional[Path] = None,
) -> Optional[str]:
    """Resolve which ``model_id`` a role should run on, applying the locked
    precedence: **collective override > models.yaml role_models > global
    default**.

    Returns the resolved ``model_id`` (collective override wins when set,
    else the ``role_models`` mapping), or ``None`` when neither applies (the
    caller then leaves the global ``ACC_LLM_*`` default in place)."""
    ov = (override_model_id or "").strip()
    if ov:
        return ov
    return model_for_role(role, path)


def apply_role_model_env(
    *,
    environ: Optional[dict] = None,
    path: Optional[Path] = None,
) -> dict[str, str]:
    """Overlay the resolved role's LLM env onto ``environ`` (default
    ``os.environ``) so ``load_config`` picks up the mapped model.

    Reads two env signals the deployment sets per agent:

    * ``ACC_AGENT_ROLE``     — this agent's role (the mapping key).
    * ``ACC_AGENT_MODEL_ID`` — a collective.yaml per-agent override marker
      (compose-gen sets it from ``AgentSpec.model``); when present it WINS,
      so ``role_models`` never clobbers an explicit per-agent choice.

    A ``role_models`` mapping OVERRIDES the global ``ACC_LLM_*`` default
    (keys are assigned, not ``setdefault``).  Returns the env dict applied
    (``{}`` when nothing resolved — global default stays).  Never raises on a
    missing/empty registry."""
    env = os.environ if environ is None else environ
    role = (env.get("ACC_AGENT_ROLE") or "").strip()
    override = (env.get("ACC_AGENT_MODEL_ID") or "").strip()
    model_id = resolve_role_model_id(role, override_model_id=override, path=path)
    if not model_id:
        return {}
    applied = model_env_for_id(model_id, path)
    for k, v in applied.items():
        env[k] = v
    return applied
