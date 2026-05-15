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
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from acc.backends import LLMBackend, MetricsBackend, SignalingBackend, VectorBackend

# ---------------------------------------------------------------------------
# Pydantic config model
# ---------------------------------------------------------------------------

DeployMode = Literal["standalone", "rhoai", "edge"]
AgentRole = Literal["ingester", "analyst", "synthesizer", "arbiter", "observer", "coding_agent"]
LLMBackendChoice = Literal["ollama", "anthropic", "vllm", "llama_stack", "openai_compat"]
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

    # Proposal 004 — first-class subrole hierarchy.
    # ``parent_role`` declares the role's logical parent in a flat
    # 2-level tree: a top-level role (``coding_agent``) is the
    # parent of several persona subroles (``coding_agent_architect``,
    # ``coding_agent_implementer``, …).  Default ``None`` keeps
    # existing roles working with no migration required.
    #
    # Spiffe-style spiffe_id paths (multi-level nesting) are an
    # explicit future option tracked separately — slot 008+.
    parent_role: Optional[str] = None
    """Logical parent role.  ``None`` for top-level roles.

    Used by the TUI Ecosystem screen's subrole listing (proposal
    003 PR-6) to prefer declared hierarchy over directory-name glob.
    The arbiter does NOT enforce parent existence — a subrole can
    be infused without its parent being currently loaded."""

    # ACC-11: Grandmother cell domain identity
    domain_id: str = ""
    """Knowledge domain this role inhabits.

    Example: ``'software_engineering'``, ``'data_analysis'``, ``'security_audit'``.
    Empty string = uncategorised (receives all paracrine domain_tags; no domain centroid
    tracking until a domain_id is assigned via DOMAIN_DIFFERENTIATION)."""

    domain_receptors: list[str] = Field(default_factory=list)
    """Domain tags this role will respond to in PARACRINE signals.

    Empty list = universal receptor (responds to all domain_tags).
    A role with ``domain_receptors=['software_engineering']`` silently drops
    KNOWLEDGE_SHARE signals tagged ``'data_analysis'``.

    Biological analog: the membrane receptor set of a specialised cell — only
    cells with the matching receptor can detect and respond to a ligand."""

    eval_rubric_hash: str = ""
    """SHA-256 hex digest of the canonical eval_rubric.yaml for this role.

    Computed by RoleLoader at load time from the canonical YAML serialisation
    (PyYAML dump with sort_keys=True). EVAL_OUTCOME payloads whose rubric
    criteria are not in the registered set for this domain are rejected by
    Cat-A rule A-015.

    Empty string = no rubric file present (role accepts any criteria)."""

    # ------------------------------------------------------------------
    # Phase 4.3 — Skills + MCP whitelists
    # ------------------------------------------------------------------

    allowed_skills: list[str] = Field(default_factory=list)
    """Skill ids (matching ``skills/<skill_id>/skill.yaml``) this role may invoke.

    Empty list = role cannot invoke ANY skill (default — fail-closed).  This
    is the inverse default of ``allowed_actions`` (where empty = unconstrained
    legacy behaviour) because skills are a new capability surface and we
    want fail-closed semantics out of the box.

    Cat-A rule A-017 enforces this list: ``CognitiveCore.invoke_skill()``
    raises :class:`acc.skills.SkillForbiddenError` if the requested
    ``skill_id`` is not present.

    Biological framing: the membrane receptor set — only organelles the
    cell expresses are reachable from inside the cell."""

    default_skills: list[str] = Field(default_factory=list)
    """Subset of ``allowed_skills`` advertised in the LLM system prompt.

    Skills in this list appear in the prompt's "Available skills" block so
    the LLM knows it can call them; skills only in ``allowed_skills`` (and
    not here) are reachable but the LLM has to be told about them
    out-of-band.  Useful for: keeping the prompt small when many skills
    are licit, or hiding sensitive skills behind explicit operator
    instructions."""

    max_skill_risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    """Maximum :class:`acc.skills.SkillRiskLevel` this role may invoke.

    Cat-A rule A-017 raises ``SkillForbiddenError`` if the manifest's
    ``risk_level`` ranks above this ceiling (LOW < MEDIUM < HIGH < CRITICAL).
    Default ``MEDIUM`` allows LOW + MEDIUM skills; explicit upgrade required
    for HIGH or CRITICAL.

    CRITICAL invocations always additionally enqueue an oversight request
    (EU AI Act Art. 14) regardless of this ceiling."""

    allowed_mcps: list[str] = Field(default_factory=list)
    """MCP server ids (matching ``mcps/<server_id>/mcp.yaml``) this role may
    consume.  Same fail-closed semantics as ``allowed_skills``: empty list
    = no MCP servers reachable.  Cat-A rule A-018 enforces."""

    default_mcps: list[str] = Field(default_factory=list)
    """Subset of ``allowed_mcps`` advertised in the system prompt."""

    max_mcp_risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    """Risk ceiling applied by Cat-A A-018 to MCP tool invocations."""

    # ------------------------------------------------------------------
    # PR-2 — Sub-cluster estimator + parallelism cap
    # ------------------------------------------------------------------

    max_parallel_tasks: int = 1
    """Hard ceiling on sub-agents spawned for one PLAN step.

    Read by :func:`acc.estimator.default_estimator` to clamp the
    heuristic output, and enforced again by Cat-A rule A-019 so a
    misconfigured estimator override cannot exceed the role's declared
    parallelism budget.

    Default 1 = no parallelisation (legacy single-agent dispatch).
    Bump explicitly per role when the workload benefits from
    parallel sub-agents (e.g. coding_agent typically runs at 3)."""

    estimator: dict[str, Any] = Field(default_factory=dict)
    """Operator-supplied estimator configuration block.

    Schema (every field optional — missing block falls back to the
    default heuristic with role-defaults)::

        estimator:
          strategy: "heuristic"          # or "fixed", "module:dotted.path"
          heuristic:
            base: 1
            per_n_tokens: 2000
            skill_per_subagent: 2
            cap: 5
          fixed:
            count: 3                     # only used when strategy=="fixed"
          difficulty_signals:
            - keyword: "security"
              bump: 1
            - keyword: "concurrency"
              bump: 2

    Stored as a free-form dict (not a nested BaseModel) on purpose:
    the estimator strategy is dispatched at runtime via
    :func:`acc.estimator.build_estimator`, and operators may register
    custom strategies in the future via the ``module:`` form without
    needing to touch this config schema."""


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
    # --- Provider-specific fields (legacy; kept for backward compatibility) ---
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    anthropic_model: str = "claude-sonnet-4-6"
    vllm_inference_url: str = ""
    llama_stack_url: str = ""
    embedding_model: str = "all-MiniLM-L6-v2"
    embedding_model_path: str = "/app/models/all-MiniLM-L6-v2"
    # --- Universal fields (ACC-LLM-Independence) ---
    model: str = ""
    """Model identifier for openai_compat and future universal backends.

    Takes precedence over backend-specific model fields when set.
    Examples: ``'gpt-4o'``, ``'llama-3.3-70b-versatile'`` (Groq),
    ``'gemini-2.0-flash'``, ``'mistralai/Mixtral-8x7B-Instruct-v0.1'`` (OpenRouter),
    ``'meta-llama/Llama-3.1-70B-Instruct'`` (HuggingFace TGI).
    """
    base_url: str = ""
    """Base URL for the inference endpoint (openai_compat and vllm).

    Examples:
    - OpenAI:       ``https://api.openai.com/v1``
    - Groq:         ``https://api.groq.com/openai/v1``
    - Gemini:       ``https://generativelanguage.googleapis.com/v1beta/openai``
    - OpenRouter:   ``https://openrouter.ai/api/v1``
    - HuggingFace:  ``https://api-inference.huggingface.co/v1``
    - Together AI:  ``https://api.together.xyz/v1``
    - Fireworks:    ``https://api.fireworks.ai/inference/v1``
    - vLLM local:   ``http://localhost:8000/v1``
    - LM Studio:    ``http://localhost:1234/v1``

    Falls back to ``vllm_inference_url`` when empty and backend is ``vllm``.
    """
    api_key_env: str = ""
    """Name of the environment variable that holds the API key.

    The variable is read at backend instantiation time so that the key itself
    never appears in config files or logs.  Leave empty for unauthenticated
    endpoints (e.g. local vLLM without ``--api-key``).
    Examples: ``'OPENAI_API_KEY'``, ``'GROQ_API_KEY'``, ``'GEMINI_API_KEY'``,
    ``'OPENROUTER_API_KEY'``, ``'HF_TOKEN'``.
    """
    request_timeout_s: int = 120
    """HTTP request timeout in seconds for inference calls (openai_compat)."""
    max_retries: int = 3
    """Maximum retry attempts on retryable errors — 429, 5xx (openai_compat)."""


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


SigningMode = Literal["ed25519", "spiffe", "auto"]
"""ROLE_UPDATE signing model (proposal 011).

- ``ed25519``: legacy static-keypair via
  :class:`SecurityConfig.arbiter_verify_key`.  Default for every
  ``deploy_mode`` in v0.4.x; stays the supported path for laptop dev
  forever — SPIRE is not a sensible laptop dependency.
- ``spiffe``: SPIFFE workload identity (JWT-SVID, audience
  ``acc-role-update``).  Opt-in throughout v0.4.x; ``rhoai`` default
  flips here in v0.5.0 (proposal 011 §2 G6).
- ``auto``: placeholder requesting the deploy-mode default.  After
  ``ACCConfig`` validation the resolved value is one of ``ed25519``
  / ``spiffe``.  Operators never write ``auto`` — it's the implicit
  value when no ``security.signing_mode`` is specified.
"""


# Default ``signing_mode`` per ``deploy_mode``.  Centralised so the
# resolution rule is observable + testable (mirrors proposal 010's
# ``_ROLE_SOURCE_BY_DEPLOY_MODE`` pattern).
#
# Every default is ``ed25519`` in v0.4.x.  v0.5.0 flips the ``rhoai``
# row to ``spiffe`` once 011 PR-2..PR-5 land.
_SIGNING_MODE_BY_DEPLOY_MODE: dict[str, str] = {
    "standalone": "ed25519",
    "edge":       "ed25519",
    "rhoai":      "ed25519",
}


class SpiffeConfig(BaseModel):
    """SPIFFE workload identity settings (proposal 011 PR-1).

    Inert in PR-1 — this PR ships the config surface only.  Proposal
    011's PR-2 (operator integration), PR-3 (sidecar injection),
    PR-4 (agent-side verifier) consume these fields.  Proposal 012's
    PR-1 extends the model with edge-specific fields.

    All fields default to safe values that disable SPIFFE entirely
    (``enabled: False``).  Operators who haven't read proposal 011
    see no behaviour change.

    Example::

        security:
          signing_mode: spiffe          # opt in
          spiffe:
            enabled: true
            trust_domain: acc-prod.example.com
            allow_ed25519_fallback: true   # belt-and-braces during migration
    """

    enabled: bool = False
    """Master switch.  When False every other field is ignored."""

    trust_domain: str = ""
    """SPIFFE trust domain, e.g. ``acc-prod.example.com``.  Empty
    means "use the deployment-supplied default" — typically
    ``<corpus_name>.acc.local`` from the operator's pod template
    (proposal 011 PR-3).  Operators with multi-cluster federation
    override here."""

    svid_mount_path: str = "/run/spire/sockets"
    """Filesystem path where ``spiffe-helper`` (proposal 011 PR-3)
    mounts the X.509-SVID and JWT-SVID.  Matches the upstream
    Kagenti convention."""

    jwt_audience: str = "acc-role-update"
    """JWT-SVID audience claim required on every signed ROLE_UPDATE.
    Distinct from any other audience the same trust domain might
    issue tokens for — prevents token confusion attacks."""

    allow_ed25519_fallback: bool = True
    """When True and ``signing_mode: spiffe``, ROLE_UPDATE verification
    tries SPIFFE first and falls back to Ed25519 on SPIFFE error
    (SPIRE socket unreachable, JWT expired, bundle rotation gap).
    Default True for the v0.4.x migration window; operators tighten
    to False once the SPIFFE path is proven stable."""


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

    ``signing_mode`` + ``spiffe`` (proposal 011) layer a SPIFFE
    workload-identity option on top.  In the default ``ed25519`` mode
    this subtree is inert — existing deployments see no behaviour
    change.
    """

    arbiter_verify_key: str = ""

    signing_mode: SigningMode = "auto"
    """How ROLE_UPDATE payloads are signed + verified.  ``auto``
    resolves to the deploy-mode default at validation time (see
    ``_SIGNING_MODE_BY_DEPLOY_MODE``); explicit values pass through
    unchanged."""

    spiffe: SpiffeConfig = Field(default_factory=SpiffeConfig)
    """SPIFFE workload identity settings.  Inert until
    ``signing_mode: spiffe`` is set AND ``spiffe.enabled: true``."""


class ComplianceConfig(BaseModel):
    """Enterprise compliance settings (ACC-12).

    All enforcement defaults to **observe mode** (log but do not block) to allow
    safe rollout.  Set ``ACC_CAT_A_ENFORCE=true`` and ``ACC_OWASP_ENFORCE=true``
    to activate blocking enforcement.
    """

    enabled: bool = True
    """Master switch.  False disables all compliance controls (not recommended for production)."""

    frameworks: list[str] = Field(
        default_factory=lambda: ["EU_AI_ACT"],
        description="Active compliance frameworks: EU_AI_ACT | HIPAA | SOC2 | OWASP_LLM_TOP10",
    )

    hipaa_mode: bool = False
    """When True: PHI entities are redacted from LanceDB episodes; HIPAA §164.312 controls
    are applied to every task; Presidio analyzer must be installed."""

    owasp_enforce: bool = False
    """When True: OWASP guardrail violations block task processing (CRITICAL violations always
    block regardless of this flag).  False = observe mode (log but don't block)."""

    cat_a_enforce: bool = False
    """When True: Cat-A rule violations block task processing and emit ALERT_ESCALATE.
    False = observe mode."""

    cat_a_wasm_path: str = "/app/regulatory_layer/category_a/constitutional_rhoai.wasm"
    """Path to the compiled OPA WASM artifact for Cat-A evaluation."""

    audit_backend: Literal["file", "kafka", "multi"] = "file"
    """Audit record backend.  ``file`` = rotating JSONL (edge-default).
    ``kafka`` = AMQ Streams / Confluent Kafka.  ``multi`` = both simultaneously."""

    audit_file_path: str = "/app/data/audit"
    """Directory for rotating JSONL audit files (file backend)."""

    audit_kafka_topic: str = "acc-audit"
    """Kafka topic prefix; agent appends ``-{collective_id}``."""

    audit_kafka_bootstrap: str = ""
    """Kafka bootstrap servers (comma-separated host:port pairs)."""

    audit_retention_days: int = 7
    """Days to retain audit log files before deletion."""

    oversight_timeout_s: int = 300
    """Seconds to wait for human approval before proceeding (EU AI Act Art. 14)."""

    oversight_risk_threshold: str = "HIGH"
    """Minimum EU AI Act risk level that triggers human oversight queue submission."""

    injection_distance_threshold: float = 0.85
    """Cosine distance threshold above which a prompt is flagged as injection attempt (LLM01)."""

    evidence_signing_key_env: str = ""
    """Environment variable name holding the HMAC signing key for audit chain."""

    disabled_guardrails: list[str] = Field(default_factory=list)
    """List of guardrail codes to disable entirely, e.g. ``['LLM02', 'LLM04']``."""


RoleSource = Literal["files", "crd", "mirror", "auto"]
"""How the agent reconciles role definitions across surfaces.

- ``files``: ``roles/<id>/role.yaml`` on disk is the source of truth.
  CRDs (if any) are projections written *from* files.  Default for
  ``deploy_mode: standalone``.
- ``crd``: the ``AgentCollective`` CRD in the K8s API is the source of
  truth.  Files (if mounted) are read-only projections written *from*
  CRDs by the agent's role loader.  Default for ``deploy_mode: rhoai``.
- ``mirror``: both directions active; last writer wins by wall-clock
  timestamp.  Conflicts (writes within ``conflict_window_s``) emit a
  NATS event on ``events_subject``.  Default for ``deploy_mode: edge``.
- ``auto``: the literal placeholder requesting the deploy-mode default
  be applied at validation time.  After ``ACCConfig`` validation, the
  resolved value is one of ``files`` / ``crd`` / ``mirror``.  Operators
  never need to write ``auto`` — it's the implicit value when no
  ``role_sync.role_source`` is specified.

See proposal 010 in the operator's Obsidian vault for the design.
"""


# Default ``role_source`` per ``deploy_mode``.  Centralised so the
# resolution rule is observable (rather than buried in a validator).
_ROLE_SOURCE_BY_DEPLOY_MODE: dict[str, str] = {
    "standalone": "files",
    "edge":       "mirror",
    "rhoai":      "crd",
}


class RoleSyncConfig(BaseModel):
    """Cross-surface role-definition sync settings (proposal 010).

    Inert in PR-1 — this PR only adds the flag and resolves its default;
    no behaviour change.  PR-2/PR-3/PR-4 wire the file ↔ CRD plumbing.

    Example::

        role_sync:
          role_source: mirror
          conflict_window_s: 2.0
          events_subject: acc.role.sync
    """

    role_source: RoleSource = "auto"
    """Source of truth for role definitions.  ``auto`` (the default)
    resolves to the value in ``_ROLE_SOURCE_BY_DEPLOY_MODE`` for the
    active ``deploy_mode``; operators can override explicitly."""

    conflict_window_s: float = 2.0
    """Time window (seconds) within which a file write and a CRD patch
    are treated as a sync conflict.  Only meaningful when the resolved
    ``role_source`` is ``mirror``."""

    events_subject: str = "acc.role.sync"
    """NATS subject prefix for sync events.  Conflicts publish on
    ``<events_subject>.conflict``; successful round-trips on
    ``<events_subject>.applied``.  Reserved for PR-4."""


class ACCConfig(BaseModel):
    deploy_mode: DeployMode = "standalone"
    agent: AgentConfig = Field(default_factory=AgentConfig)
    signaling: SignalingConfig = Field(default_factory=SignalingConfig)
    vector_db: VectorConfig = Field(default_factory=VectorConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    role_definition: RoleDefinitionConfig = Field(default_factory=RoleDefinitionConfig)
    role_sync: RoleSyncConfig = Field(default_factory=RoleSyncConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    working_memory: WorkingMemoryConfig = Field(default_factory=WorkingMemoryConfig)
    compliance: ComplianceConfig = Field(default_factory=ComplianceConfig)

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

    @model_validator(mode="after")
    def _resolve_role_source(self) -> "ACCConfig":
        """Replace ``role_sync.role_source='auto'`` with the deploy-mode
        default so downstream consumers never have to perform the
        lookup themselves.  Idempotent — explicit values pass through.
        """
        if self.role_sync.role_source == "auto":
            resolved = _ROLE_SOURCE_BY_DEPLOY_MODE.get(self.deploy_mode, "files")
            # Pydantic v2 models are immutable by default unless
            # ``model_config['frozen'] = False`` (which is the default).
            # The nested model permits direct assignment.
            self.role_sync.role_source = resolved  # type: ignore[assignment]
        return self

    @model_validator(mode="after")
    def _resolve_signing_mode(self) -> "ACCConfig":
        """Replace ``security.signing_mode='auto'`` with the deploy-mode
        default (proposal 011 PR-1).  Same pattern as
        ``_resolve_role_source`` — idempotent, explicit values pass
        through.
        """
        if self.security.signing_mode == "auto":
            resolved = _SIGNING_MODE_BY_DEPLOY_MODE.get(
                self.deploy_mode, "ed25519",
            )
            self.security.signing_mode = resolved  # type: ignore[assignment]
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
    # Universal LLM fields (ACC-LLM-Independence)
    "ACC_LLM_MODEL":                ("llm", "model"),
    "ACC_LLM_BASE_URL":             ("llm", "base_url"),
    "ACC_LLM_API_KEY_ENV":          ("llm", "api_key_env"),
    "ACC_LLM_TIMEOUT_S":            ("llm", "request_timeout_s"),
    "ACC_LLM_MAX_RETRIES":          ("llm", "max_retries"),
    "ACC_METRICS_BACKEND":          ("observability", "backend"),
    "ACC_OTEL_SERVICE_NAME":        ("observability", "otel_service_name"),
    # Role definition overrides (ACC-6a)
    "ACC_ROLE_PURPOSE":             ("role_definition", "purpose"),
    "ACC_ROLE_PERSONA":             ("role_definition", "persona"),
    "ACC_ROLE_VERSION":             ("role_definition", "version"),
    # Role-sync source-of-truth (proposal 010)
    "ACC_ROLE_SOURCE":              ("role_sync", "role_source"),
    "ACC_ROLE_SYNC_CONFLICT_WINDOW_S": ("role_sync", "conflict_window_s"),
    "ACC_ROLE_SYNC_EVENTS_SUBJECT": ("role_sync", "events_subject"),
    # SPIFFE workload identity (proposal 011 PR-1)
    "ACC_SIGNING_MODE":             ("security", "signing_mode"),
    "ACC_SPIFFE_ENABLED":           ("security", "spiffe", "enabled"),
    "ACC_SPIFFE_TRUST_DOMAIN":      ("security", "spiffe", "trust_domain"),
    "ACC_SPIFFE_SVID_MOUNT_PATH":   ("security", "spiffe", "svid_mount_path"),
    "ACC_SPIFFE_JWT_AUDIENCE":      ("security", "spiffe", "jwt_audience"),
    "ACC_SPIFFE_ALLOW_ED25519_FALLBACK": ("security", "spiffe", "allow_ed25519_fallback"),
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
    # Compliance / governance (ACC-12)
    "ACC_COMPLIANCE_ENABLED":    ("compliance", "enabled"),
    "ACC_HIPAA_MODE":            ("compliance", "hipaa_mode"),
    "ACC_OWASP_ENFORCE":         ("compliance", "owasp_enforce"),
    "ACC_CAT_A_ENFORCE":         ("compliance", "cat_a_enforce"),
    "ACC_CAT_A_WASM_PATH":       ("compliance", "cat_a_wasm_path"),
    "ACC_AUDIT_BACKEND":         ("compliance", "audit_backend"),
    "ACC_AUDIT_FILE_PATH":       ("compliance", "audit_file_path"),
    "ACC_AUDIT_KAFKA_BOOTSTRAP": ("compliance", "audit_kafka_bootstrap"),
    "ACC_OVERSIGHT_TIMEOUT_S":   ("compliance", "oversight_timeout_s"),
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
            # Universal base_url takes precedence over legacy vllm_inference_url
            inference_url=config.llm.base_url or config.llm.vllm_inference_url,
            model=config.llm.model or config.llm.ollama_model,
        )
    elif config.llm.backend == "openai_compat":
        from acc.backends.llm_openai_compat import OpenAICompatBackend
        llm = OpenAICompatBackend(
            base_url=config.llm.base_url or config.llm.vllm_inference_url,
            model=config.llm.model or config.llm.ollama_model,
            api_key_env=config.llm.api_key_env,
            embedding_model_path=config.llm.embedding_model_path,
            timeout_s=config.llm.request_timeout_s,
            max_retries=config.llm.max_retries,
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
