"""
ACC Configuration loader and backend factory.

Flow:
    1. ``load_config()`` reads ``acc-config.yaml``, overlays env vars, and
       validates with Pydantic.
    2. ``build_backends()`` inspects ``config.deploy_mode`` and instantiates
       exactly the concrete backend classes for that mode, returning a
       ``BackendBundle``.

No if/else branching for deploy_mode exists outside this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from acc.backends import LLMBackend, MetricsBackend, SignalingBackend, VectorBackend

# ---------------------------------------------------------------------------
# Pydantic config model
# ---------------------------------------------------------------------------

DeployMode = Literal["standalone", "rhoai", "edge"]
AgentRole = Literal["ingester", "analyst", "synthesizer", "arbiter", "observer", "coding_agent"]
LLMBackendChoice = Literal["ollama", "anthropic", "vllm", "llama_stack"]
MetricsBackendChoice = Literal["log", "otel"]
VectorBackendChoice = Literal["lancedb", "milvus"]
SignalingBackendChoice = Literal["nats"]


class RoleDefinitionConfig(BaseModel):
    """Role definition injected into the agent's CognitiveCore system prompt.

    All fields have safe empty defaults so that agents without a role definition
    section in their config still start without error.
    """

    purpose: str = ""
    persona: Literal["concise", "formal", "exploratory", "analytical"] = "concise"
    task_types: list[str] = Field(default_factory=list)
    seed_context: str = ""
    allowed_actions: list[str] = Field(default_factory=list)
    category_b_overrides: dict[str, float] = Field(default_factory=dict)
    version: str = "0.1.0"


class AgentConfig(BaseModel):
    role: AgentRole = "ingester"
    collective_id: str = "sol-01"
    heartbeat_interval_s: int = 30

    # Cross-collective bridge (ACC-9)
    peer_collectives: list[str] = Field(
        default_factory=list,
        description=(
            "Collective IDs that this agent may delegate tasks to (A-010). "
            "Set via ACC_PEER_COLLECTIVES as a comma-separated list."
        ),
    )
    hub_collective_id: str = Field(
        default="",
        description=(
            "The authoritative hub collective ID (edge mode only). "
            "When set, this collective is automatically added to peer_collectives."
        ),
    )
    bridge_enabled: bool = Field(
        default=False,
        description=(
            "Enable cross-collective task delegation (A-010 gate). "
            "Must be True for delegation markers from the LLM to be honoured."
        ),
    )

    @field_validator("peer_collectives", mode="before")
    @classmethod
    def _parse_comma_separated(cls, v: object) -> list[str]:
        """Accept a comma-separated string (from env var) or a list."""
        if isinstance(v, str):
            return [cid.strip() for cid in v.split(",") if cid.strip()]
        return v  # type: ignore[return-value]


class SignalingConfig(BaseModel):
    backend: SignalingBackendChoice = "nats"
    nats_url: str = "nats://localhost:4222"
    hub_url: str = Field(
        default="",
        description=(
            "NATS leaf node hub URL (edge deployMode only). "
            "When set, the local NATS server connects to this remote as a leaf node, "
            "forwarding bridge subjects to the datacenter hub. "
            "Example: nats-leaf://hub.example.com:7422"
        ),
    )


class VectorConfig(BaseModel):
    backend: VectorBackendChoice = "lancedb"
    lancedb_path: str = "/app/data/lancedb"
    milvus_uri: str = ""
    milvus_collection_prefix: str = "acc_"


class LLMConfig(BaseModel):
    backend: LLMBackendChoice = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    anthropic_model: str = "claude-sonnet-4-6"
    vllm_inference_url: str = ""
    llama_stack_url: str = ""
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_model_path: str = "/app/models/all-MiniLM-L6-v2"


class ObservabilityConfig(BaseModel):
    backend: MetricsBackendChoice = "log"
    otel_service_name: str = "acc-agent"


class WorkingMemoryConfig(BaseModel):
    """Redis working-memory connection settings (Phase 0b).

    ``url`` uses the standard ``redis://[user:password@]host:port[/db]`` scheme.
    Leave empty to disable Redis working memory — the agent will operate with
    in-process state only (role centroid, stress indicators, and role history
    will not be persisted between restarts).

    ``password`` is kept separate from the URL so it can be supplied via an
    environment variable or a Kubernetes Secret without leaking into log lines
    that might record the full connection URL.  When non-empty it overrides any
    password embedded in ``url``.
    """

    url: str = ""       # e.g. redis://acc-redis:6379
    password: str = ""  # Redis AUTH password; empty = no authentication


class SecurityConfig(BaseModel):
    """Cryptographic security settings (Phase 0a onwards).

    ``arbiter_verify_key`` is the Base64-encoded raw 32-byte Ed25519 public key
    belonging to the collective's arbiter.  When non-empty, every incoming
    ROLE_UPDATE payload must carry a valid Ed25519 signature produced by the
    corresponding private key before it is applied.

    When empty the signature presence check is still enforced (``signature``
    field must be non-empty) but no cryptographic verification is performed.
    This preserves backward compatibility with test fixtures that use
    placeholder signatures and with environments where the arbiter key has not
    yet been provisioned.
    """

    arbiter_verify_key: str = ""


class ACCConfig(BaseModel):
    deploy_mode: DeployMode = "standalone"
    agent: AgentConfig = Field(default_factory=AgentConfig)
    signaling: SignalingConfig = Field(default_factory=SignalingConfig)
    vector_db: VectorConfig = Field(default_factory=VectorConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    role_definition: RoleDefinitionConfig = Field(default_factory=RoleDefinitionConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    working_memory: WorkingMemoryConfig = Field(default_factory=WorkingMemoryConfig)

    @model_validator(mode="after")
    def _validate_deploy_mode_fields(self) -> "ACCConfig":
        if self.deploy_mode == "rhoai":
            if not self.vector_db.milvus_uri:
                raise ValueError("vector_db.milvus_uri is required in rhoai deploy_mode")
            if not self.llm.vllm_inference_url and not self.llm.llama_stack_url:
                raise ValueError(
                    "llm.vllm_inference_url or llm.llama_stack_url is required in rhoai deploy_mode"
                )
        # edge: no required fields — hub_url and peer_collectives are optional
        # (agent operates locally when disconnected from hub).
        return self


# ---------------------------------------------------------------------------
# Environment variable overlay
# ---------------------------------------------------------------------------

_ENV_MAP: dict[str, tuple[str, ...]] = {
    "ACC_DEPLOY_MODE":              ("deploy_mode",),
    "ACC_AGENT_ROLE":               ("agent", "role"),
    "ACC_COLLECTIVE_ID":            ("agent", "collective_id"),
    "ACC_NATS_URL":                 ("signaling", "nats_url"),
    "ACC_NATS_HUB_URL":            ("signaling", "hub_url"),
    "ACC_LANCEDB_PATH":             ("vector_db", "lancedb_path"),
    "ACC_MILVUS_URI":               ("vector_db", "milvus_uri"),
    "ACC_MILVUS_COLLECTION_PREFIX": ("vector_db", "milvus_collection_prefix"),
    "ACC_LLM_BACKEND":              ("llm", "backend"),
    "ACC_OLLAMA_BASE_URL":          ("llm", "ollama_base_url"),
    "ACC_OLLAMA_MODEL":             ("llm", "ollama_model"),
    "ACC_ANTHROPIC_MODEL":          ("llm", "anthropic_model"),
    "ACC_VLLM_INFERENCE_URL":       ("llm", "vllm_inference_url"),
    "ACC_LLAMA_STACK_URL":          ("llm", "llama_stack_url"),
    "ACC_METRICS_BACKEND":          ("observability", "backend"),
    "ACC_OTEL_SERVICE_NAME":        ("observability", "otel_service_name"),
    # Role definition overrides (ACC-6a)
    "ACC_ROLE_PURPOSE":             ("role_definition", "purpose"),
    "ACC_ROLE_PERSONA":             ("role_definition", "persona"),
    "ACC_ROLE_VERSION":             ("role_definition", "version"),
    # ACC_ROLE_CONFIG_PATH is consumed by RoleStore.load_at_startup(), not here
    # Security (Phase 0a)
    "ACC_ARBITER_VERIFY_KEY":       ("security", "arbiter_verify_key"),
    # Working memory / Redis (Phase 0b)
    "ACC_REDIS_URL":                ("working_memory", "url"),
    "ACC_REDIS_PASSWORD":           ("working_memory", "password"),
    # Cross-collective bridge (ACC-9)
    "ACC_PEER_COLLECTIVES":         ("agent", "peer_collectives"),
    "ACC_HUB_COLLECTIVE_ID":        ("agent", "hub_collective_id"),
    "ACC_BRIDGE_ENABLED":           ("agent", "bridge_enabled"),
    # Intra-collective communication (ACC-10)
    # ACC_ROLES_ROOT is consumed by RoleStore/RoleLoader, not here
    # ACC_SCRATCHPAD_TTL_S, ACC_KNOWLEDGE_INDEX_MAX_ITEMS, ACC_EVAL_RETENTION_DAYS
    # are read from Cat-B setpoints at runtime; no config-layer field needed.
}


def _apply_env(data: dict) -> dict:
    """Overlay environment variables onto a config dict."""
    for env_var, path in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is None:
            continue
        node = data
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(path: str | Path = "acc-config.yaml") -> ACCConfig:
    """Load and validate ACC configuration.

    Args:
        path: Path to the YAML config file.  Defaults to ``acc-config.yaml``
              in the current working directory.

    Returns:
        Validated :class:`ACCConfig` instance.

    Raises:
        FileNotFoundError: If the config file does not exist.
        pydantic.ValidationError: If validation fails for the selected deploy_mode.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path.resolve()}")

    with config_path.open() as fh:
        raw: dict = yaml.safe_load(fh) or {}

    raw = _apply_env(raw)
    return ACCConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Backend bundle + factory
# ---------------------------------------------------------------------------


@dataclass
class BackendBundle:
    """Container for all resolved backend instances."""

    signaling: SignalingBackend
    vector: VectorBackend
    llm: LLMBackend
    metrics: MetricsBackend


def build_backends(config: ACCConfig) -> BackendBundle:
    """Instantiate concrete backends from *config*.

    The selection logic is entirely here; all other modules receive the
    ``BackendBundle`` and are agnostic to the underlying implementation.

    Args:
        config: Validated :class:`ACCConfig`.

    Returns:
        :class:`BackendBundle` with all four backends instantiated.
    """
    # --- Signaling ---
    signaling: SignalingBackend
    if config.signaling.backend == "nats":
        from acc.backends.signaling_nats import NATSBackend
        signaling = NATSBackend(config.signaling.nats_url)
    else:
        raise ValueError(f"Unknown signaling backend: {config.signaling.backend}")

    # --- Vector ---
    # edge mode: LanceDB on local NVMe (same as standalone — different storage
    # path / PVC size is an operator concern, not a Python backend concern).
    vector: VectorBackend
    if config.vector_db.backend == "lancedb":
        from acc.backends.vector_lancedb import LanceDBBackend
        vector = LanceDBBackend(config.vector_db.lancedb_path)
    elif config.vector_db.backend == "milvus":
        from acc.backends.vector_milvus import MilvusBackend
        vector = MilvusBackend(
            uri=config.vector_db.milvus_uri,
            collection_prefix=config.vector_db.milvus_collection_prefix,
        )
    else:
        raise ValueError(f"Unknown vector backend: {config.vector_db.backend}")

    # --- LLM ---
    llm: LLMBackend
    if config.llm.backend == "ollama":
        from acc.backends.llm_ollama import OllamaBackend
        llm = OllamaBackend(
            base_url=config.llm.ollama_base_url,
            model=config.llm.ollama_model,
        )
    elif config.llm.backend == "anthropic":
        from acc.backends.llm_anthropic import AnthropicBackend
        llm = AnthropicBackend(
            model=config.llm.anthropic_model,
            embedding_model_path=config.llm.embedding_model_path,
        )
    elif config.llm.backend == "vllm":
        from acc.backends.llm_vllm import VLLMBackend
        llm = VLLMBackend(
            inference_url=config.llm.vllm_inference_url,
            model=config.llm.ollama_model,
        )
    elif config.llm.backend == "llama_stack":
        from acc.backends.llm_llama_stack import LlamaStackBackend
        llm = LlamaStackBackend(
            base_url=config.llm.llama_stack_url,
            embedding_model_path=config.llm.embedding_model_path,
        )
    else:
        raise ValueError(f"Unknown LLM backend: {config.llm.backend}")

    # --- Metrics ---
    metrics: MetricsBackend
    if config.observability.backend == "log":
        from acc.backends.metrics_log import LogMetricsBackend
        metrics = LogMetricsBackend()
    elif config.observability.backend == "otel":
        from acc.backends.metrics_otel import OTelMetricsBackend
        metrics = OTelMetricsBackend(service_name=config.observability.otel_service_name)
    else:
        raise ValueError(f"Unknown metrics backend: {config.observability.backend}")

    return BackendBundle(
        signaling=signaling,
        vector=vector,
        llm=llm,
        metrics=metrics,
    )
