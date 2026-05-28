"""A2A Agent Card generator.

Phase 1 of OpenSpec ``20260527-a2a-agent-interop``: a **pure function** that
turns a :class:`RoleDefinitionConfig` + collective/agent context into the JSON
document Kagenti / A2A peers consume from ``/.well-known/agent-card.json``.

No I/O, no HTTP, no NATS — this module is a *data mapping*.  Future phases
(:mod:`acc.a2a` next file: ``server.py``) will serve the dict over HTTP and
wire JSON-RPC translation.  Keeping the mapping standalone makes it trivial
to unit-test (no env, no deps beyond ``acc.config``) and lets the card
schema evolve in one place when A2A bumps versions.

Schema reference
----------------
A2A Agent Card v1 — the cross-vendor shape every Kagenti / A2A client knows:

``{
  "name":               str,
  "description":        str,
  "url":                str,            # JSON-RPC endpoint URL
  "version":            str,
  "capabilities":       {streaming, pushNotifications, stateTransitionHistory},
  "defaultInputModes":  ["text/plain", ...],
  "defaultOutputModes": ["text/plain", ...],
  "skills":             [{id, name, description, tags, examples,
                          inputModes, outputModes}, ...],
  "authentication":     {"schemes": [...]},
}``

ACC-specific metadata (role, collective, governance ceilings, reasoning_trace
flag, …) rides under a vendor extension key ``"acc"`` so it never collides
with the A2A standard and survives schema bumps.
"""

from __future__ import annotations

from typing import Any

from acc.config import RoleDefinitionConfig

# Default I/O modes for the Phase-1 card.  ACC agents speak text today;
# multimodal modes get added when the runtime supports them end-to-end.
_DEFAULT_INPUT_MODES = ["text/plain"]
_DEFAULT_OUTPUT_MODES = ["text/plain"]

# A2A Agent Card schema version this generator targets.  Kept in one place so
# a future spec bump is a one-line change.  See OpenSpec scope-and-risk note
# "A2A risk — protocol drift (A2A alpha)".
A2A_CARD_SCHEMA_VERSION = "1.0"


def build_agent_card(
    role: RoleDefinitionConfig,
    *,
    role_label: str,
    collective_id: str,
    agent_id: str,
    base_url: str,
) -> dict[str, Any]:
    """Build a valid A2A Agent Card document for an ACC agent.

    Parameters
    ----------
    role:
        The agent's resolved :class:`RoleDefinitionConfig` (post-validator —
        i.e. after ``workspace_access`` etc. have been applied).
    role_label:
        The role's short name (e.g. ``"coding_agent"``, ``"assistant"``).
        Used in ``name`` and the ACC extension.
    collective_id:
        The owning collective id (e.g. ``"sol-01"``).  Carried in the ACC
        extension so peers can correlate across collectives.
    agent_id:
        The specific agent instance id (one per pod / replica).
    base_url:
        The agent's JSON-RPC endpoint base URL — what an A2A client POSTs to.
        The card itself is conventionally hosted at
        ``<base_url>/.well-known/agent-card.json`` (Phase 1b will serve it).

    Returns
    -------
    A plain ``dict`` ready to JSON-serialise.  Caller controls serialisation
    (sort_keys, indent) so this stays pure.
    """
    return {
        "schemaVersion": A2A_CARD_SCHEMA_VERSION,
        "name": f"{role_label}@{collective_id}",
        "description": role.purpose.strip(),
        "url": base_url,
        "version": role.version,
        "capabilities": _capabilities(role),
        "defaultInputModes": list(_DEFAULT_INPUT_MODES),
        "defaultOutputModes": list(_DEFAULT_OUTPUT_MODES),
        "skills": _skills(role, role_label),
        # Phase 1: no auth scheme advertised.  Phase 5 (identity
        # convergence) will publish SPIRE x5c / Keycloak schemes here.
        "authentication": {"schemes": []},
        # Vendor extension.  ACC-specific fields live here, namespaced, so
        # the A2A standard fields stay clean and future spec bumps don't
        # collide with our metadata.
        "acc": _acc_extension(role, role_label, collective_id, agent_id),
    }


# --------------------------------------------------------------------------
# Internals — kept small + focused so the mapping is easy to audit.
# --------------------------------------------------------------------------


def _capabilities(role: RoleDefinitionConfig) -> dict[str, bool]:
    """A2A standard ``capabilities`` block.

    Phase 1 advertises only what ACC actually supports today over A2A — none
    of streaming, push notifications, or state-transition history are wired
    yet.  Flipping these to ``True`` is a Phase 2+ decision; defaulting them
    to ``False`` keeps the card honest.
    """
    return {
        "streaming": False,
        "pushNotifications": False,
        "stateTransitionHistory": False,
    }


def _skills(role: RoleDefinitionConfig, role_label: str) -> list[dict[str, Any]]:
    """Map ACC ``task_types`` onto A2A ``skills``.

    Each task_type becomes one skill entry.  Tags carry the role's domain id
    and persona so callers can filter by domain; allowed_skills (the ACC
    capability surface) ride as extra tags for discoverability without
    promising A2A-callable behaviour.
    """
    tags = _skill_tags(role, role_label)
    skills: list[dict[str, Any]] = []
    for tt in role.task_types or []:
        skills.append({
            "id": tt.lower(),
            "name": tt,
            "description": f"{tt} task handled by the {role_label} role.",
            "tags": list(tags),
            "examples": [],
            "inputModes": list(_DEFAULT_INPUT_MODES),
            "outputModes": list(_DEFAULT_OUTPUT_MODES),
        })
    return skills


def _skill_tags(role: RoleDefinitionConfig, role_label: str) -> list[str]:
    tags = [f"role:{role_label}", f"persona:{role.persona}"]
    if role.domain_id:
        tags.append(f"domain:{role.domain_id}")
    for skill in role.default_skills or []:
        tags.append(f"skill:{skill}")
    return tags


def _acc_extension(
    role: RoleDefinitionConfig,
    role_label: str,
    collective_id: str,
    agent_id: str,
) -> dict[str, Any]:
    """ACC-specific metadata, kept under the vendor key so the A2A standard
    fields stay vendor-neutral and future A2A spec bumps don't touch them."""
    return {
        "role": role_label,
        "collectiveId": collective_id,
        "agentId": agent_id,
        "persona": role.persona,
        "domainId": role.domain_id,
        "reasoningTrace": role.reasoning_trace,
        "memoryRetrieval": role.memory_retrieval,
        "canRoute": role.can_route,
        "workspaceAccess": role.workspace_access,
        "maxParallelTasks": role.max_parallel_tasks,
        "governance": {
            "maxSkillRiskLevel": role.max_skill_risk_level,
            "maxMcpRiskLevel": role.max_mcp_risk_level,
        },
        "defaultOperatingMode": role.default_operating_mode,
        # Echo the OpenSpec change id so a peer reading the card can correlate
        # to the feature definition; useful while A2A integration is alpha.
        "openSpec": "20260527-a2a-agent-interop",
    }
