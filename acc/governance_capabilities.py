"""Cat-A capability guard — A-017 (skills) + A-018 (MCP tools).

The existing :class:`acc.governance.CatAEvaluator` is the right home for
*signal-level* rules (A-001 through A-016 — bus subjects, cross-collective
gating, signed payloads).  Skill and MCP-tool invocations are
*decision-level* events: they happen inside one agent's
:meth:`acc.cognitive_core.CognitiveCore.process_task`, before any signal
hits the bus.  Reaching for OPA at that boundary would mean serialising
a context document to JSON, shelling out, parsing the answer — for a
check that resolves in microseconds against a list and an enum.

So A-017 and A-018 live here as a small Python evaluator with the same
shape as :class:`CatAEvaluator`: an ``enforce`` flag (observe vs block),
a structured input doc, a ``(allowed, reason)`` return.  The
``allowed_actions`` / ``allowed_skills`` / ``allowed_mcps`` lookups are
O(len(list)) at most a handful of entries — no caching needed.

Both rules are also expressed in
``regulatory_layer/category_a/constitutional_rhoai.rego`` as declarative
documentation; the Python implementation is the source of truth for
runtime enforcement.

Entry points:

* :meth:`CapabilityGuard.check_skill_invocation` — called before
  :meth:`acc.skills.SkillRegistry.invoke`.
* :meth:`CapabilityGuard.check_mcp_invocation` — called before
  :meth:`acc.mcp.MCPClient.call_tool`.

Both raise the package-native ``Forbidden``/``ToolNotFound`` errors when
``enforce=True``; in observe mode they log and return without raising
so the call proceeds (matching :class:`CatAEvaluator`'s observe-mode
contract).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acc.config import RoleDefinitionConfig
    from acc.mcp.manifest import MCPManifest
    from acc.skills.manifest import SkillManifest

logger = logging.getLogger("acc.governance_capabilities")


# Shared LOW < MEDIUM < HIGH < CRITICAL ranking.  Same string set as
# both SkillRiskLevel and MCPRiskLevel (kept duplicated to avoid an
# import cycle with the skills + mcp packages — risk strings are
# stable enough to live in three places).
_RISK_RANK: dict[str, int] = {
    "LOW": 0,
    "MEDIUM": 1,
    "HIGH": 2,
    "CRITICAL": 3,
}


def _risk_exceeds(actual: str, ceiling: str) -> bool:
    """True when *actual* outranks *ceiling*.

    Unknown strings are treated as ``LOW`` rather than raising — the
    pydantic Literal types make truly invalid values impossible at
    config-load time, and being permissive here means a future risk
    level that ships before this lookup is updated does not hard-fail
    the agent.
    """
    return _RISK_RANK.get(actual, 0) > _RISK_RANK.get(ceiling, 1)


# ---------------------------------------------------------------------------
# Decision result
# ---------------------------------------------------------------------------


class CapabilityDecision:
    """One evaluation outcome.  Use :attr:`allowed` to branch; use
    :attr:`reason` for log lines and audit records.

    ``rule`` is the Cat-A id (``"A-017"`` / ``"A-018"``) so audit
    records can be filtered by rule without parsing free-form reasons.
    """

    __slots__ = ("allowed", "rule", "reason", "needs_oversight")

    def __init__(
        self,
        *,
        allowed: bool,
        rule: str,
        reason: str,
        needs_oversight: bool = False,
    ) -> None:
        self.allowed = allowed
        self.rule = rule
        self.reason = reason
        #: True when the manifest's risk_level is CRITICAL.  Caller is
        #: expected to enqueue an OVERSIGHT_SUBMIT request (EU AI Act
        #: Art. 14) before proceeding, even if ``allowed`` is True.
        self.needs_oversight = needs_oversight

    def __repr__(self) -> str:  # pragma: no cover — debugging aid only
        return (
            f"CapabilityDecision(allowed={self.allowed}, rule={self.rule!r}, "
            f"reason={self.reason!r}, needs_oversight={self.needs_oversight})"
        )


# ---------------------------------------------------------------------------
# CapabilityGuard
# ---------------------------------------------------------------------------


class CapabilityGuard:
    """Stateless Cat-A guard for A-017 (skills) and A-018 (MCP tools).

    Args:
        enforce: When True (block mode), violations return
            ``allowed=False`` and the calling site is expected to raise.
            When False (observe mode — default), violations log a
            warning and return ``allowed=True`` so the call proceeds.
            Mirrors the :class:`acc.governance.CatAEvaluator` contract.
    """

    def __init__(self, *, enforce: bool = False) -> None:
        self._enforce = enforce
        logger.debug(
            "governance_capabilities: CapabilityGuard initialised "
            "(enforce=%s)", enforce,
        )

    @property
    def enforce(self) -> bool:
        return self._enforce

    # ------------------------------------------------------------------
    # A-017 — Skill invocation guard
    # ------------------------------------------------------------------

    def check_skill_invocation(
        self,
        role: "RoleDefinitionConfig",
        manifest: "SkillManifest",
    ) -> CapabilityDecision:
        """Apply A-017 to one skill invocation.

        Three independent checks, evaluated in this order:

        1. **Whitelist** — ``manifest.skill_id`` must appear in
           ``role.allowed_skills``.  Empty whitelist denies every skill
           (fail-closed); this is the inverse of ``allowed_actions``
           where empty = unconstrained.
        2. **Required actions** — every entry in
           ``manifest.requires_actions`` must appear in
           ``role.allowed_actions``.  This composes with the existing
           LLM08 agency limiter: a skill cannot smuggle in an action
           the role's prompt-side limiter would reject.
        3. **Risk ceiling** — ``manifest.risk_level`` must rank at or
           below ``role.max_skill_risk_level``.

        CRITICAL skills additionally set ``needs_oversight=True`` even
        when ``allowed=True`` so the caller can enqueue an
        OVERSIGHT_SUBMIT request alongside the invocation.
        """
        skill_id = manifest.skill_id

        # Check 1 — whitelist
        if skill_id not in role.allowed_skills:
            return self._deny(
                rule="A-017",
                reason=(
                    f"skill {skill_id!r} not in role.allowed_skills "
                    f"(allowed={role.allowed_skills})"
                ),
            )

        # Check 2 — required actions
        missing = [
            action for action in manifest.requires_actions
            if action not in role.allowed_actions
        ]
        if missing:
            return self._deny(
                rule="A-017",
                reason=(
                    f"skill {skill_id!r} requires actions {missing} "
                    f"missing from role.allowed_actions"
                ),
            )

        # Check 3 — risk ceiling
        ceiling = getattr(role, "max_skill_risk_level", "MEDIUM")
        if _risk_exceeds(manifest.risk_level, ceiling):
            return self._deny(
                rule="A-017",
                reason=(
                    f"skill {skill_id!r} risk_level={manifest.risk_level!r} "
                    f"exceeds role ceiling {ceiling!r}"
                ),
            )

        return CapabilityDecision(
            allowed=True,
            rule="A-017",
            reason="pass",
            needs_oversight=(manifest.risk_level == "CRITICAL"),
        )

    # ------------------------------------------------------------------
    # A-018 — MCP tool invocation guard
    # ------------------------------------------------------------------

    def check_mcp_invocation(
        self,
        role: "RoleDefinitionConfig",
        manifest: "MCPManifest",
        tool_name: str,
    ) -> CapabilityDecision:
        """Apply A-018 to one MCP tool invocation.

        Same three checks as :meth:`check_skill_invocation` but against
        ``role.allowed_mcps`` / ``role.max_mcp_risk_level``, plus a
        fourth:

        4. **Manifest tool gate** — the tool must be permitted by the
           manifest's own ``allowed_tools`` / ``denied_tools`` lists
           (re-checked here so the audit record carries a Cat-A
           rule_id even when the underlying registry would also block).
        """
        server_id = manifest.server_id

        # Check 1 — server whitelist
        if server_id not in role.allowed_mcps:
            return self._deny(
                rule="A-018",
                reason=(
                    f"mcp_server {server_id!r} not in role.allowed_mcps "
                    f"(allowed={role.allowed_mcps})"
                ),
            )

        # Check 2 — required actions
        missing = [
            action for action in manifest.requires_actions
            if action not in role.allowed_actions
        ]
        if missing:
            return self._deny(
                rule="A-018",
                reason=(
                    f"mcp_server {server_id!r} requires actions {missing} "
                    f"missing from role.allowed_actions"
                ),
            )

        # Check 3 — risk ceiling
        ceiling = getattr(role, "max_mcp_risk_level", "MEDIUM")
        if _risk_exceeds(manifest.risk_level, ceiling):
            return self._deny(
                rule="A-018",
                reason=(
                    f"mcp_server {server_id!r} risk_level={manifest.risk_level!r} "
                    f"exceeds role ceiling {ceiling!r}"
                ),
            )

        # Check 4 — manifest tool gate
        if not manifest.is_tool_allowed(tool_name):
            return self._deny(
                rule="A-018",
                reason=(
                    f"mcp_server {server_id!r}: tool {tool_name!r} blocked by "
                    f"manifest (allowed={manifest.allowed_tools or 'all'}, "
                    f"denied={manifest.denied_tools})"
                ),
            )

        return CapabilityDecision(
            allowed=True,
            rule="A-018",
            reason="pass",
            needs_oversight=(manifest.risk_level == "CRITICAL"),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _deny(self, *, rule: str, reason: str) -> CapabilityDecision:
        """Construct a denial decision, honouring observe vs enforce mode."""
        if self._enforce:
            logger.warning("governance_capabilities: %s BLOCK — %s", rule, reason)
            return CapabilityDecision(allowed=False, rule=rule, reason=reason)
        # Observe mode — log but allow.  Caller's audit record will
        # record reason=observed:<...> so violations are still visible.
        logger.warning(
            "governance_capabilities: %s OBSERVE — would block: %s",
            rule, reason,
        )
        return CapabilityDecision(
            allowed=True,
            rule=rule,
            reason=f"observed:{reason}",
        )
