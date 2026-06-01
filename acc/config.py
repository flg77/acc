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
AgentRole = Literal["ingester", "analyst", "synthesizer", "arbiter", "observer", "coding_agent", "orchestrator", "assistant", "compliance_officer"]

# Proposal `20260531-role-perception-profiles` Phase 1 — typed
# per-role perception profile.  Selects which live-state slice gets
# injected into the system prompt before each LLM call.  v0.3.43
# (PR #9) shipped the `control` profile on the Assistant only;
# v0.3.45 (this PR) generalises so every spawnable role can opt
# into the slice that fits its job.
#
# Profile shapes:
#   * `none`     — no block injected (default — preserves v0.3.42
#                  behaviour byte-identically).
#   * `control`  — roster + role catalog + MCPs + sub-collectives.
#                  For gatekeeper-class roles (assistant, future
#                  control roles).
#   * `workspace`— workspace path + allowed_skills ∩ catalog +
#                  allowed_mcps ∩ catalog + sibling workers in
#                  cluster.  For coding_agent / ingester / analyst.
#   * `domain`   — Phase 2.
#   * `reviewer` — Phase 3.
#   * `output`   — Phase 3.
#   * `customer` — Phase 5 (gates on AoA-P5b TUI auth).
#   * `queue`    — Phase 4.
PerceptionProfile = Literal[
    "none",
    "control",
    "workspace",
    "domain",
    "reviewer",
    "output",
    "customer",
    "queue",
]
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

    # D-002 (PR-I) — RAG-the-agent.  When True (default) the
    # CognitiveCore queries LanceDB's ``episodes`` table for the
    # top-K nearest past episodes (by cosine similarity to the
    # current task's embedding) and renders them into the system
    # prompt under a ``RECENT_RELEVANT_EPISODES`` section.  Adds
    # ~150-300ms of pre-LLM latency (one extra embedding +
    # LanceDB read).  Set ``memory_retrieval: false`` on roles
    # where the latency matters more than the recall (ephemeral
    # one-shot agents).  See docs/DECISIONS.md D-002.
    memory_retrieval: bool = True

    # PR-MEM2 — self-reflective memory.  When True, an out-of-band loop
    # periodically consolidates this agent's recent episodes into durable
    # "memory notes" (LLM summaries) that sharpen future retrieval.
    # The cadence is the ``reflection_interval_s`` Cat-B setpoint.
    #
    # v0.3.41 (followup #51 continuation) — flipped default to True.
    # Pre-v0.3.41 this defaulted False AND no role.yaml flipped it on,
    # so reflection was off across the entire roster even after v0.3.40
    # turned on the loop's env-gated outer wrapper.  Roles that genuinely
    # don't want reflection (the arbiter is a candidate, since it doesn't
    # reason on tasks) can opt out by setting ``memory_reflection: false``
    # in their role.yaml.  Cost: one extra LLM call per
    # ``reflection_interval_s`` window per active role.
    memory_reflection: bool = True

    # Proposal `20260531-role-perception-profiles` Phase 1
    # (v0.3.45) — opt this role into a typed perception profile.
    # The cognitive_core's Observe step queries the slice of live
    # state appropriate to this role's job and injects a
    # ``## Currently available`` block into the system prompt before
    # the LLM call.  Default ``none`` preserves v0.3.42 behaviour
    # byte-identically: no block, no per-task perception fan-out.
    # Each profile carries its own renderer + marker validator in
    # ``acc/perception.py``; see the typed `PerceptionProfile`
    # alias above for the full enum.
    perception_profile: PerceptionProfile = "none"

    # PR-V6b — routing authority.  When True, this role's ``[ROUTE:role:reason]``
    # markers are honoured: the agent re-dispatches the task to the named role
    # (the orchestrator pattern).  Default False so ordinary roles can NEVER
    # trigger a re-dispatch — a verbose model emitting a stray ROUTE marker
    # would otherwise cause a runaway routing loop (observed live).  Only the
    # orchestrator role sets this True.
    can_route: bool = False

    # PR-V3b — externalize reasoning.  When True, the CognitiveCore appends a
    # reasoning-externalization block to the system prompt asking the model to
    # think out loud inside a ``<reasoning>…</reasoning>`` block (prior
    # learnings → options → evaluation → plan → review) before the final
    # answer.  The block is parsed back out into ``CognitiveResult.reasoning``
    # and surfaced to the operator (TUI Prompt screen), while the clean answer
    # remains the deliverable.  Default False: it costs extra output tokens and
    # latency, so opt in per role (e.g. the coding agent).  The same prompt
    # text is mirrored in acc-dev-harness/tools/trace_eval/reasoning_prompt.py
    # so bench scores match live-agent output.
    reasoning_trace: bool = False

    # D-007 (PR-U2) — trusted-workspace filesystem access.
    # When True the role may read/write files in the operator's
    # trusted working directory (via the sandboxed ``fs_read`` /
    # ``fs_write`` skills).  Default False — every role carries the
    # option but it is DEACTIVATED until explicitly enabled in
    # role.yaml (operator's requirement).  ``coding_agent`` + its
    # subroles ship with it True; the actual working directory is
    # still selected per-project at prompt time (TUI Select Directory)
    # and must be trusted before any write lands.  When True, the
    # ``_grant_workspace_skills`` validator auto-adds ``fs_read`` /
    # ``fs_write`` to allowed+default skills and raises the skill-risk
    # ceiling to HIGH (fs_write is a HIGH-risk skill), so the operator
    # only flips one boolean.
    workspace_access: bool = False

    # D-003 (PR-L) — operator-controlled autonomy gate.
    # ``AUTO`` (default) matches today's behaviour: Cat-A blocks,
    # Cat-B observes, all other invocations run.  Other valid
    # values: ``PLAN`` (no execution, plan-only), ``ACCEPT_EDITS``
    # (gate write actions), ``ASK_PERMISSIONS`` (gate every
    # invocation).  The Nucleus Apply form prefills the Prompt
    # screen's mode selector from this value; the operator can
    # override per task.  See docs/DECISIONS.md D-003 and
    # ``acc/operating_modes.py``.
    default_operating_mode: str = "AUTO"

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

    # Proposal 20260530-acc-self-improvement-policy-gradient — per-role
    # learning surface.  Proposal 20260530-assistant-agent-of-agents
    # Phase 6 wires these from role.yaml into the agent's
    # RewardHarness so SIP-P2's bandit can move the right knobs at
    # the right cadence for each role.
    #
    # `policy_pinned`: list of θ-vector knob names the bandit must NOT
    # move.  Default empty == all-pinned for backward compatibility,
    # which is what SIP-P2's RewardHarness already does when ``pinned``
    # is None — so a role that doesn't declare this field behaves
    # exactly as today.  Operators opt-in to learning per-knob by
    # listing the knobs they want frozen here (the inverse of what
    # you might expect — pinned = frozen).
    policy_pinned: list[str] = Field(default_factory=list)
    """θ-vector knobs the bandit must not touch for this role.  Empty
    list with ``policy_enabled=False`` (default) preserves today's
    behaviour: no learning.  Set ``policy_enabled=True`` and list the
    knobs you want frozen here.  See
    :data:`acc.policy_layer.DEFAULT_POLICY_VECTOR` for the full set."""

    policy_enabled: bool = False
    """When True, the agent's RewardHarness ships the role-supplied
    pin/cadence/cap to SIP-P2's bandit.  Default False — a role that
    doesn't opt-in keeps the SIP-P1 observation-only behaviour
    (rewards logged, no θ updates)."""

    policy_update_every_n_tasks: int = 100
    """Bandit cadence (rail 3 — rate-limit vs Cat-C proposals).
    Smaller = faster learning, more variance; larger = slower, more
    stable.  Honoured by SIP-P2's RewardHarness when
    ``policy_enabled=True``.  Clamped to >=1 inside the harness."""

    policy_drift_cap: float = 0.8
    """Drift constraint (SIP-P2 rail 2).  The agent stays under this
    cap; rewards only kick in for drift *above* the cap (negative
    penalty for exceeding).  Below the cap, drift contributes zero
    to the reward — exploration is free."""

    policy_contextual: bool = False
    """SIP-P3 opt-in for the contextual policy seam.  Default False
    preserves SIP-P2 EWMA-only behaviour.  When True, the agent feeds
    per-task ContextFeatures (operating mode, drift, last eval
    reward) into RewardHarness so the per-knob contextual bias can
    learn against them.  Phase-3 lands the data path; the step-side
    wiring promotes to active when SNR analysis justifies."""

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

    _WORKSPACE_SKILLS = ("fs_read", "fs_write")
    _RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

    @model_validator(mode="after")
    def _grant_workspace_skills(self) -> "RoleDefinitionConfig":
        """D-007 (PR-U2) — when ``workspace_access`` is True, auto-grant
        the sandboxed filesystem skills so the operator only flips one
        boolean in role.yaml.

        Adds ``fs_read`` + ``fs_write`` to both ``allowed_skills`` (the
        Cat-A A-017 gate) and ``default_skills`` (advertised in the
        system prompt so the LLM knows it can write files), and raises
        ``max_skill_risk_level`` to at least HIGH because ``fs_write``
        is a HIGH-risk skill that A-017 would otherwise reject.

        No-op when ``workspace_access`` is False — every role keeps the
        option, deactivated by default.
        """
        if not getattr(self, "workspace_access", False):
            return self
        for sid in self._WORKSPACE_SKILLS:
            if sid not in self.allowed_skills:
                self.allowed_skills.append(sid)
            if sid not in self.default_skills:
                self.default_skills.append(sid)
        if self._RISK_ORDER.get(self.max_skill_risk_level, 1) < self._RISK_ORDER["HIGH"]:
            self.max_skill_risk_level = "HIGH"
        return self


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

    # A2A interop (OpenSpec 20260527-a2a-agent-interop, Phase 4): per-peer
    # A2A endpoint URLs.  When set + deploy_mode=rhoai, [DELEGATE:cid:reason]
    # routes via the A2A adapter (HTTPS/JSON-RPC) instead of the NATS bridge;
    # see acc.a2a.client.select_transport.  edge/standalone keep the NATS
    # bridge regardless — see vault note "A2A scope — ACC-9 bridge
    # deprecation path".  Empty (default) preserves legacy behaviour.
    peer_a2a_urls: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Mapping of peer collective_id → A2A JSON-RPC endpoint URL. "
            "Set via ACC_PEER_A2A_URLS as 'cid1=url1,cid2=url2'."
        ),
    )

    @field_validator("peer_collectives", mode="before")
    @classmethod
    def _parse_comma_separated(cls, v: object) -> list[str]:
        """Accept a comma-separated string (from env var) or a list."""
        if isinstance(v, str):
            return [cid.strip() for cid in v.split(",") if cid.strip()]
        return v  # type: ignore[return-value]

    @field_validator("peer_a2a_urls", mode="before")
    @classmethod
    def _parse_peer_a2a_urls(cls, v: object) -> dict[str, str]:
        """Accept 'cid1=url1,cid2=url2' from env, or a dict."""
        if isinstance(v, str):
            out: dict[str, str] = {}
            for pair in v.split(","):
                pair = pair.strip()
                if not pair or "=" not in pair:
                    continue
                cid, url = pair.split("=", 1)
                cid, url = cid.strip(), url.strip()
                if cid and url:
                    out[cid] = url
            return out
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
    enable_prompt_cache: bool = False
    """PR-CA2 — opt-in per-backend prompt-cache HINT (env
    ``ACC_LLM_ENABLE_PROMPT_CACHE``).  When true, the agent hints the
    backend that the stable system prompt is a cacheable prefix; the
    Anthropic backend attaches ``cache_control`` (a DC accelerator).
    Backends whose server auto-caches prefixes (vLLM, Ollama) ignore the
    hint — they already benefit from the stable prefix (PR-CA1), so this
    is optional in all modes and off by default."""


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

    arbiter_spiffe_id: str = ""
    """Expected SPIFFE ID of the collective's arbiter, e.g.
    ``spiffe://acc-prod.example.com/role/research``.  When set, the
    JWT-SVID verifier (proposal 011 PR-4) enforces that the token's
    ``sub`` claim equals this value — proving the ROLE_UPDATE came
    from the arbiter specifically, not merely from some attested
    workload in the trust domain.  When blank, the ``sub`` check is
    skipped and arbiter identity rests on the existing
    ``approver_id`` application-layer check."""

    # ------------------------------------------------------------------
    # Edge-specific fields (proposal 012 PR-1)
    # ------------------------------------------------------------------
    # These fields are only meaningful when ``deploy_mode: edge``.  They
    # default to the nested-SPIRE topology because that's the
    # SPIRE-canonical hierarchical pattern for edge ↔ datacenter trust.
    # Sites without an rhoai parent set ``edge_topology: federated``;
    # sites that won't run SPIRE at all set ``edge_topology: ed25519``.
    # See proposal 012 §4 for the topology trade-off table.

    edge_topology: Literal["nested", "federated", "ed25519"] = "nested"
    """Edge-side SPIFFE topology.  Only consulted when
    ``deploy_mode: edge``.

    - ``nested``: edge SPIRE server is downstream of an rhoai parent
      (the SPIRE-canonical pattern; default for edge).  Edge identities
      live under the shared trust domain at
      ``spiffe://<trust-domain>/edge/<site-id>/role/<id>``.
    - ``federated``: edge SPIRE owns its own trust domain, federated
      with peers via the SPIFFE bundle-endpoint API.  Industrial
      multi-site deployments without a datacenter dependency.
    - ``ed25519``: no edge SPIRE — the entire edge keeps using the
      legacy Ed25519 model regardless of ``signing_mode``.  Reserved
      for constrained hardware (RPi fleets, etc.).
    """

    edge_site_id: str = ""
    """Operator-supplied site identifier — qualifies the SPIFFE path
    so edge sites can never issue colliding workload IDs (e.g.
    ``factory-a`` vs ``plant-mke``).  Required when
    ``edge_topology: nested``; ignored (with a warning) when
    ``federated`` (the trust domain itself plays the scoping role);
    unused when ``ed25519``.

    Proposal 012 §8 Q5 resolution: operator-supplied in
    ``acc-config.yaml`` rather than derived from a K8s node label —
    consistency with ``trust_domain`` + ``parent_spire_url``.  PR-2
    adds a cluster-scoped uniqueness check + ``acc-cli validate``
    placeholder-name warning to catch copy-paste collisions.
    """

    parent_spire_url: str = ""
    """gRPC URL of the parent SPIRE server.  Required when
    ``edge_topology: nested``.  Typical value:
    ``spire-server.acc-system.svc.cluster.local:8081`` reached via
    the NATS leaf node's network path."""

    federation_peers: list[str] = Field(default_factory=list)
    """List of SPIFFE bundle-endpoint URLs to federate with.
    Required (≥ 1) when ``edge_topology: federated``.  Each peer
    becomes a ``ClusterFederatedTrustDomain`` CR in PR-3."""

    offline_bundle_cache_path: str = "/run/spire/cache/bundle.pem"
    """Filesystem path where the bundle-fetcher CronJob caches the
    latest trust bundle.  Agent's SPIFFE verifier falls back to this
    file when the live SPIRE socket is unreachable."""

    offline_max_age_h: float = 72.0
    """Maximum age (hours) of the cached trust bundle before
    ``offline_action`` fires.  Default 72h gives operators 3 days of
    air-gap tolerance — comfortably beyond typical maintenance
    windows."""

    bundle_refresh_h: float = 6.0
    """How often the bundle-fetcher CronJob polls the parent (or
    federation peers) for a fresh bundle.  Default 6h means 12+
    failed fetches before ``offline_max_age_h`` expires — generous
    margin for transient network blips."""

    offline_action: Literal["rotate", "degrade", "shutdown"] = "rotate"
    """What happens when the cached bundle approaches expiry
    (proposal 012 §8 Q2 resolution).

    - ``rotate``: edge-local SPIRE server uses its long-lived
      attested credential to issue a fresh bundle.  Air-gapped sites
      stay operational indefinitely.  Requires nested topology.
    - ``degrade``: agent enters read-only mode — existing tasks
      finish, new TASK_ASSIGN gets a CONFLICT response.
    - ``shutdown``: agent pods exit non-zero (fail-safe posture for
      sites that prefer "broken loudly" over "running with stale
      trust").
    """

    parent_unreachable_action: Literal["block", "degrade"] = "degrade"
    """What the operator reconciler does when the parent SPIRE is
    unreachable at AgentCollective Ready-check time (proposal 012
    §8 Q4 resolution).

    - ``degrade``: surface a degraded ``Status.Condition`` but allow
      the AgentCollective to reach Ready.  Matches edge's
      offline-first contract.
    - ``block``: hold the AgentCollective in a NotReady state until
      parent SPIRE is reachable.  Fail-safe posture.
    """

    nats_mtls_cert_path: str = ""
    """PEM-encoded X.509 cert path for NATS mTLS.  Used when SPIFFE
    isn't supplying the X.509-SVID — proposal 012 §8 Q6 resolution:
    NATS mTLS is default-on regardless of ``signing_mode``, with this
    field as the manual-cert fallback.  Empty + ``signing_mode:
    spiffe`` means "use the live SVID at ``svid_mount_path``"."""

    nats_mtls_key_path: str = ""
    """Private-key counterpart to ``nats_mtls_cert_path``."""

    # Cross-field validation lives on ``ACCConfig`` because the edge
    # topology constraints are only meaningful when ``deploy_mode:
    # edge`` — see ``ACCConfig._validate_edge_spiffe_fields`` below.


class NKeyConfig(BaseModel):
    """NATS NKey authentication settings (proposal 013, Phase 0c).

    Inert in PR-2 — this PR ships the config surface + the canonical
    permission matrix only.  Proposal 013's PR-3/PR-4 (operator) and
    PR-5 (runtime connect path) consume these fields.

    All fields default to values that disable NKey auth entirely
    (``enabled: False``).  With the switch off, ``acc.backends.
    signaling_nats`` connects exactly as it did before — zero
    behaviour change for every existing deployment.

    NKeys authenticate the *connection* and gate which *subjects* an
    identity may publish/subscribe (server-enforced).  This is
    complementary to SPIFFE (proposal 011), which signs the
    ROLE_UPDATE *payload* — a deployment may run either, both, or
    neither.

    Example::

        security:
          nkey:
            enabled: true
            role: arbiter
            seed_path: /run/acc/nkeys/seed
    """

    enabled: bool = False
    """Master switch.  When False every other field is ignored and
    the NATS client connects without credentials (legacy behaviour)."""

    seed_path: str = "/run/acc/nkeys/seed"
    """Filesystem path to this process's NKey *seed* file (the
    secret half of the Ed25519 keypair, ``S...``-prefixed).  On
    rhoai/edge the operator projects a K8s Secret here; standalone
    operators point this at a ``0600`` file produced by
    ``scripts/acc-nkeys generate``.  Never logged or rendered."""

    role: str = ""
    """Which NKey identity this process presents — one of the six
    agent roles, ``tui``, or ``leaf``.  Empty means "derive from
    ``agent.role``" at connect time (the common case for agent
    pods)."""

    leaf_seed_path: str = ""
    """Seed file for the edge leaf-node link to the hub
    (``deploy_mode: edge`` only).  Empty disables leaf-link
    authentication.  Distinct from ``seed_path`` because the leaf
    connection is a different NATS identity than the agent."""


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

    arbiter_signing_key: str = ""
    """Base64-encoded raw 32-byte Ed25519 *private* key the arbiter
    signs worker-pool ROLE_ASSIGN payloads with (PR-M, J-2).

    Held ONLY by the arbiter process — set via the
    ``ACC_ARBITER_SIGNING_KEY`` env var (typically from a mounted
    secret).  Empty on every non-arbiter agent.  When empty the
    arbiter's reconcile loop logs a warning and emits no
    assignments (workers stay dormant) rather than publishing
    unsigned payloads that every worker would reject anyway."""

    signing_mode: SigningMode = "auto"
    """How ROLE_UPDATE payloads are signed + verified.  ``auto``
    resolves to the deploy-mode default at validation time (see
    ``_SIGNING_MODE_BY_DEPLOY_MODE``); explicit values pass through
    unchanged."""

    spiffe: SpiffeConfig = Field(default_factory=SpiffeConfig)
    """SPIFFE workload identity settings.  Inert until
    ``signing_mode: spiffe`` is set AND ``spiffe.enabled: true``."""

    nkey: NKeyConfig = Field(default_factory=NKeyConfig)
    """NATS NKey authentication settings (proposal 013).  Inert until
    ``nkey.enabled: true``.  Complementary to ``spiffe`` — NKeys
    authenticate the NATS connection; SPIFFE signs ROLE_UPDATE
    payloads."""


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

    runtime_evidence_enabled: bool = False
    """When True: the agent's CognitiveCore subscribes to KERNEL_EVENT
    signals and folds kernel-level evidence into Cat-A (proposal 015).
    Inert when False — Cat-A stays metadata-only.  The operator sets
    this on agent pods when the runtime-evidence bridge is deployed."""

    runtime_enforce: bool = False
    """When True: kernel-event Cat-A violations block task processing
    and emit ALERT_ESCALATE.  False (default) = the observe baseline —
    violations are logged (``OBSERVED:kernel:*``) but never block.
    Separate from ``cat_a_enforce`` so kernel evidence can run in
    observe while metadata Cat-A enforces, or vice versa."""

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

    @model_validator(mode="after")
    def _validate_edge_spiffe_fields(self) -> "ACCConfig":
        """Topology-specific field requirements for edge SPIFFE
        (proposal 012 PR-1).

        Only fires when ``deploy_mode: edge`` AND SPIFFE is enabled
        AND the resolved ``signing_mode`` actually consumes SPIFFE.
        Non-edge deployments aren't subject to the edge_topology
        constraints even if they happen to enable SPIFFE — the edge
        fields are simply ignored.

        - ``nested`` needs ``parent_spire_url`` + ``edge_site_id``.
        - ``federated`` needs ≥ 1 ``federation_peers`` entry.
        - ``offline_action == "rotate"`` requires ``nested`` topology.
        """
        if self.deploy_mode != "edge":
            return self
        if not self.security.spiffe.enabled:
            return self
        # ``signing_mode: ed25519`` means the operator explicitly chose
        # not to use SPIFFE even on edge — edge_topology fields stay
        # advisory; don't reject.
        if self.security.signing_mode == "ed25519":
            return self

        sp = self.security.spiffe

        if sp.edge_topology == "nested":
            missing = []
            if not sp.parent_spire_url:
                missing.append("parent_spire_url")
            if not sp.edge_site_id:
                missing.append("edge_site_id")
            if missing:
                raise ValueError(
                    f"deploy_mode=edge + spiffe.edge_topology='nested' "
                    f"requires {', '.join(missing)} to be set in "
                    "security.spiffe"
                )

        if sp.edge_topology == "federated" and not sp.federation_peers:
            raise ValueError(
                "deploy_mode=edge + spiffe.edge_topology='federated' "
                "requires at least one entry in "
                "security.spiffe.federation_peers"
            )

        if sp.offline_action == "rotate" and sp.edge_topology != "nested":
            raise ValueError(
                "security.spiffe.offline_action='rotate' requires "
                "edge_topology='nested' (rotation is only meaningful "
                "when the edge has a local SPIRE server)"
            )

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
    "ACC_LLM_ENABLE_PROMPT_CACHE":  ("llm", "enable_prompt_cache"),
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
    "ACC_SPIFFE_ARBITER_ID":        ("security", "spiffe", "arbiter_spiffe_id"),
    # Edge SPIRE topology (proposal 012 PR-1)
    "ACC_SPIFFE_EDGE_TOPOLOGY":     ("security", "spiffe", "edge_topology"),
    "ACC_SPIFFE_EDGE_SITE_ID":      ("security", "spiffe", "edge_site_id"),
    "ACC_SPIFFE_PARENT_URL":        ("security", "spiffe", "parent_spire_url"),
    "ACC_SPIFFE_OFFLINE_MAX_AGE_H": ("security", "spiffe", "offline_max_age_h"),
    "ACC_SPIFFE_BUNDLE_REFRESH_H":  ("security", "spiffe", "bundle_refresh_h"),
    "ACC_SPIFFE_OFFLINE_ACTION":    ("security", "spiffe", "offline_action"),
    "ACC_SPIFFE_PARENT_UNREACHABLE_ACTION": ("security", "spiffe", "parent_unreachable_action"),
    "ACC_NATS_MTLS_CERT_PATH":      ("security", "spiffe", "nats_mtls_cert_path"),
    "ACC_NATS_MTLS_KEY_PATH":       ("security", "spiffe", "nats_mtls_key_path"),
    # NATS NKey authentication (proposal 013 PR-2)
    "ACC_NKEY_ENABLED":             ("security", "nkey", "enabled"),
    "ACC_NKEY_SEED_PATH":           ("security", "nkey", "seed_path"),
    "ACC_NKEY_ROLE":                ("security", "nkey", "role"),
    "ACC_NKEY_LEAF_SEED_PATH":      ("security", "nkey", "leaf_seed_path"),
    # Note: ACC_SPIFFE_FEDERATION_PEERS handled separately — list type
    # needs comma-split that _apply_env's flat string assignment doesn't
    # do.  Operators set this field in acc-config.yaml directly.
    # ACC_ROLE_CONFIG_PATH is consumed by RoleStore.load_at_startup(), not here
    # Security (Phase 0a)
    "ACC_ARBITER_VERIFY_KEY":       ("security", "arbiter_verify_key"),
    # PR-M (J-2) — arbiter-only private key for signing worker-pool
    # ROLE_ASSIGN payloads.
    "ACC_ARBITER_SIGNING_KEY":      ("security", "arbiter_signing_key"),
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
    # Runtime-evidence / kernel-event Cat-A (proposal 015)
    "ACC_RUNTIME_EVIDENCE_ENABLED": ("compliance", "runtime_evidence_enabled"),
    "ACC_RUNTIME_ENFORCE":          ("compliance", "runtime_enforce"),
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


def build_llm_backend(config: ACCConfig) -> LLMBackend:
    """Instantiate just the LLM backend from *config*.

    Extracted from :func:`build_backends` so the agent's
    ``config.reload`` handler can swap the LLM client in-process
    without rebuilding signaling / vector / metrics (those hold
    long-lived connections that must not be churned).
    """
    if config.llm.backend == "ollama":
        from acc.backends.llm_ollama import OllamaBackend
        return OllamaBackend(
            base_url=config.llm.ollama_base_url,
            model=config.llm.ollama_model,
        )
    if config.llm.backend == "anthropic":
        from acc.backends.llm_anthropic import AnthropicBackend
        return AnthropicBackend(
            model=config.llm.anthropic_model,
            embedding_model_path=config.llm.embedding_model_path,
        )
    if config.llm.backend == "vllm":
        from acc.backends.llm_vllm import VLLMBackend
        return VLLMBackend(
            # Universal base_url takes precedence over legacy vllm_inference_url
            inference_url=config.llm.base_url or config.llm.vllm_inference_url,
            model=config.llm.model or config.llm.ollama_model,
        )
    if config.llm.backend == "openai_compat":
        from acc.backends.llm_openai_compat import OpenAICompatBackend
        return OpenAICompatBackend(
            base_url=config.llm.base_url or config.llm.vllm_inference_url,
            model=config.llm.model or config.llm.ollama_model,
            api_key_env=config.llm.api_key_env,
            embedding_model_path=config.llm.embedding_model_path,
            timeout_s=config.llm.request_timeout_s,
            max_retries=config.llm.max_retries,
        )
    if config.llm.backend == "llama_stack":
        from acc.backends.llm_llama_stack import LlamaStackBackend
        return LlamaStackBackend(
            base_url=config.llm.llama_stack_url,
            embedding_model_path=config.llm.embedding_model_path,
        )
    raise ValueError(f"Unknown LLM backend: {config.llm.backend}")


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
        # Proposal 013 — thread the NKey seed when NKey auth is on.
        # When off, nkey_seed_path stays None and the connection is
        # credential-less, exactly as before.
        nkey = config.security.nkey
        signaling = NATSBackend(
            config.signaling.nats_url,
            nkey_seed_path=nkey.seed_path if nkey.enabled else None,
        )
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
    llm: LLMBackend = build_llm_backend(config)

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
