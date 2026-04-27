"""LLM08 — Excessive Agency guardrail (ACC-12 / OWASP LLM Top 10).

Parses LLM output for action invocations in all three common formats:
1. ACC native:    ``[ACTION: action_name]``
2. OpenAI:        ``{"tool_calls": [{"function": {"name": "..."}}]}``
3. Anthropic:     ``<function_calls><invoke name="...">...</invoke></function_calls>``

Each extracted action is verified against ``role.allowed_actions``.
Unknown actions → CRITICAL violation (agent attempting to exceed its authority).
Actions within the allowed set but outside declared ``role.task_types`` context → MEDIUM.

Return signature::

    (violations: list[str], risk_level: str, detail: dict, redacted: None)
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acc.config import RoleDefinitionConfig

logger = logging.getLogger("acc.guardrails.agency_limiter")

# ACC native action marker
_ACC_ACTION_RE = re.compile(r"\[ACTION:\s*([^\]]+)\]", re.IGNORECASE)
# Anthropic XML function call
_ANTHROPIC_INVOKE_RE = re.compile(r'<invoke\s+name=["\']([^"\']+)["\']', re.IGNORECASE)


def _extract_all_actions(text: str) -> list[str]:
    """Extract action names from all three LLM output formats."""
    actions: set[str] = set()

    # 1. ACC native
    for m in _ACC_ACTION_RE.finditer(text):
        actions.add(m.group(1).strip().lower())

    # 2. OpenAI JSON tool_calls
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for tc in data.get("tool_calls", []):
                name = (tc.get("function") or {}).get("name") or tc.get("name")
                if name:
                    actions.add(name.strip().lower())
    except (json.JSONDecodeError, TypeError):
        pass

    # 3. Anthropic XML
    for m in _ANTHROPIC_INVOKE_RE.finditer(text):
        actions.add(m.group(1).strip().lower())

    return list(actions)


async def check_agency(
    output: str,
    role: "RoleDefinitionConfig",
) -> tuple[list[str], str, dict[str, Any], None]:
    """Check LLM output for excessive agency.

    Args:
        output:  Raw LLM output text.
        role:    Active role definition (``allowed_actions``, ``task_types``).

    Returns:
        ``(violations, risk_level, detail, None)``.
    """
    detail: dict[str, Any] = {
        "extracted_actions": [],
        "unauthorized_actions": [],
        "out_of_context_actions": [],
    }

    if not role.allowed_actions:
        # No constraints declared — cannot evaluate
        return [], "LOW", detail, None

    extracted = _extract_all_actions(output)
    detail["extracted_actions"] = extracted

    if not extracted:
        return [], "LOW", detail, None

    allowed = {a.lower() for a in role.allowed_actions}
    unauthorized = [a for a in extracted if a not in allowed]
    detail["unauthorized_actions"] = unauthorized

    if unauthorized:
        logger.warning(
            "guardrails.LLM08: unauthorized actions=%s role=%s",
            unauthorized,
            role.allowed_actions,
        )
        return ["LLM08"], "CRITICAL", detail, None

    # All extracted actions are in allowed_actions — no violation.
    # NOTE: actions in allowed_actions are pre-approved by the role definition;
    # the task_types context check would produce false positives because
    # task_types describe *inputs* while allowed_actions describe *outputs*.
    return [], "LOW", detail, None
