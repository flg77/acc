"""GuardrailEngine — orchestrates all OWASP LLM Top 10 guardrail checks (ACC-12)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acc.config import ComplianceConfig, RoleDefinitionConfig

logger = logging.getLogger("acc.guardrails.engine")

# Risk level ordering (higher index = more severe)
_RISK_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _max_risk(levels: list[str]) -> str:
    if not levels:
        return "LOW"
    return max(levels, key=lambda r: _RISK_ORDER.index(r) if r in _RISK_ORDER else 0)


@dataclass
class GuardrailResult:
    """Result from one guardrail engine pass (pre or post LLM call)."""

    passed: bool
    """True when no violations above the enforce threshold were found."""

    violations: list[str] = field(default_factory=list)
    """OWASP violation codes detected, e.g. ``['LLM01', 'LLM06']``."""

    redacted_content: str | None = None
    """PII-redacted version of the checked text, or ``None`` if no redaction was applied."""

    risk_level: str = "LOW"
    """Highest risk level across all violations: LOW | MEDIUM | HIGH | CRITICAL."""

    details: dict = field(default_factory=dict)
    """Per-guardrail detail dict for audit record inclusion."""


class GuardrailEngine:
    """Async guardrail engine — runs all enabled guardrails concurrently.

    Args:
        compliance_config: Active ``ComplianceConfig`` instance.

    The engine lazily imports and instantiates individual guardrail modules
    to avoid hard dependencies when guardrails are disabled.
    """

    def __init__(self, compliance_config: "ComplianceConfig") -> None:
        self._cfg = compliance_config
        self._disabled = set(compliance_config.disabled_guardrails)

    async def pre_llm(
        self,
        prompt: str,
        role: "RoleDefinitionConfig",
    ) -> GuardrailResult:
        """Run pre-LLM guardrails concurrently.

        Checks: LLM01 (injection), LLM04 (DoS), LLM08 pre (agency scope),
        LLM06 pre (PII presence log).

        Args:
            prompt:  The user-facing prompt content to check.
            role:    Active role definition (for threshold overrides and allowed_actions).

        Returns:
            :class:`GuardrailResult`.  When ``risk_level == 'CRITICAL'`` the caller
            should block the task even in observe mode.
        """
        tasks: dict[str, asyncio.Task] = {}

        if "LLM01" not in self._disabled:
            from acc.guardrails.prompt_injection import check_injection
            tasks["LLM01"] = asyncio.create_task(
                check_injection(prompt, role, self._cfg)
            )
        if "LLM04" not in self._disabled:
            from acc.guardrails.dos_shield import check_dos
            tasks["LLM04"] = asyncio.create_task(check_dos(prompt, role, self._cfg))
        if "LLM06" not in self._disabled:
            from acc.guardrails.pii_detector import check_pii_pre
            tasks["LLM06"] = asyncio.create_task(
                check_pii_pre(prompt, self._cfg)
            )

        return await self._collect(tasks, enforce=self._cfg.owasp_enforce)

    async def post_llm(
        self,
        output: str,
        role: "RoleDefinitionConfig",
    ) -> GuardrailResult:
        """Run post-LLM guardrails concurrently.

        Checks: LLM02 (output validation), LLM06 post (PII/PHI redaction),
        LLM08 (excessive agency).

        Args:
            output:  Raw LLM output text.
            role:    Active role definition.

        Returns:
            :class:`GuardrailResult`.  ``redacted_content`` is set when PHI was
            found in HIPAA mode; callers should use it for episode storage.
        """
        tasks: dict[str, asyncio.Task] = {}

        if "LLM02" not in self._disabled:
            from acc.guardrails.output_handler import check_output
            tasks["LLM02"] = asyncio.create_task(check_output(output, role, self._cfg))
        if "LLM06" not in self._disabled:
            from acc.guardrails.pii_detector import check_pii_post
            tasks["LLM06"] = asyncio.create_task(
                check_pii_post(output, self._cfg)
            )
        if "LLM08" not in self._disabled:
            from acc.guardrails.agency_limiter import check_agency
            tasks["LLM08"] = asyncio.create_task(check_agency(output, role))

        return await self._collect(tasks, enforce=self._cfg.owasp_enforce)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _collect(
        tasks: dict[str, asyncio.Task],
        enforce: bool,
    ) -> GuardrailResult:
        """Await all tasks and merge results into a single GuardrailResult."""
        if not tasks:
            return GuardrailResult(passed=True)

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        violations: list[str] = []
        risk_levels: list[str] = []
        details: dict = {}
        redacted: str | None = None

        for code, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.error("guardrails: %s raised %s", code, result)
                details[code] = {"error": str(result)}
                continue

            # Individual guardrail functions return (violations, risk_level, detail, redacted?)
            code_violations, risk_level, detail, maybe_redacted = result
            if code_violations:
                violations.extend(code_violations)
                risk_levels.append(risk_level)
            details[code] = detail
            if maybe_redacted is not None:
                redacted = maybe_redacted

        combined_risk = _max_risk(risk_levels)
        # In observe mode: CRITICAL still allowed but violations still recorded
        passed = (combined_risk != "CRITICAL") or not enforce
        if violations:
            logger.warning(
                "guardrails: violations=%s risk=%s enforce=%s passed=%s",
                violations,
                combined_risk,
                enforce,
                passed,
            )

        return GuardrailResult(
            passed=passed,
            violations=violations,
            redacted_content=redacted,
            risk_level=combined_risk,
            details=details,
        )
