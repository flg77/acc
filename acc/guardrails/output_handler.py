"""LLM02 — Insecure Output Handling guardrail (ACC-12 / OWASP LLM Top 10).

Checks:
1. **Action whitelist**: scan LLM output for action markers not in ``role.allowed_actions``.
2. **Length anomaly**: output token estimate > ``token_budget × 1.5`` → MEDIUM warning.
3. **Schema validation**: if ``role.category_b_overrides['response_schema']`` present,
   validate output as JSON against the declared schema.

Return signature::

    (violations: list[str], risk_level: str, detail: dict, redacted: None)
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acc.config import ComplianceConfig, RoleDefinitionConfig

logger = logging.getLogger("acc.guardrails.output_handler")

# Patterns for extracting action invocations from LLM output
_ACTION_PATTERNS = [
    # ACC native: [ACTION: action_name]
    re.compile(r"\[ACTION:\s*([^\]]+)\]", re.IGNORECASE),
    # Simple function-call-like: tool_name(args)
    re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", re.MULTILINE),
]

# Tool-call JSON patterns (OpenAI function calling format)
_TOOL_CALL_RE = re.compile(r'"name"\s*:\s*"([^"]+)"', re.IGNORECASE)


def _extract_actions(text: str) -> list[str]:
    """Extract all action names referenced in the LLM output."""
    actions: set[str] = set()

    for pat in _ACTION_PATTERNS:
        for m in pat.finditer(text):
            actions.add(m.group(1).strip().lower())

    # Try parsing as JSON for OpenAI tool_calls format
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "tool_calls" in data:
            for tc in data["tool_calls"]:
                name = tc.get("function", {}).get("name") or tc.get("name")
                if name:
                    actions.add(name.strip().lower())
    except (json.JSONDecodeError, TypeError):
        # Try to find "name": "xxx" patterns in partial JSON
        for m in _TOOL_CALL_RE.finditer(text):
            actions.add(m.group(1).strip().lower())

    return list(actions)


def _estimate_tokens(text: str) -> int:
    return int(len(text.split()) / 0.75)


async def check_output(
    output: str,
    role: "RoleDefinitionConfig",
    config: "ComplianceConfig",
) -> tuple[list[str], str, dict[str, Any], None]:
    """Validate LLM output against role constraints.

    Args:
        output:  Raw LLM output text.
        role:    Active role definition.
        config:  Active compliance configuration.

    Returns:
        ``(violations, risk_level, detail, None)``.
    """
    detail: dict[str, Any] = {
        "extracted_actions": [],
        "unauthorized_actions": [],
        "token_estimate": 0,
        "length_anomaly": False,
        "schema_valid": None,
    }
    violations: list[str] = []
    risk_levels: list[str] = []

    # ── Action whitelist check ───────────────────────────────────────────────
    if role.allowed_actions:
        extracted = _extract_actions(output)
        detail["extracted_actions"] = extracted
        allowed = {a.lower() for a in role.allowed_actions}
        unauthorized = [a for a in extracted if a not in allowed and len(a) > 2]
        detail["unauthorized_actions"] = unauthorized
        if unauthorized:
            logger.warning(
                "guardrails.LLM02: unauthorized actions detected=%s allowed=%s",
                unauthorized,
                list(allowed),
            )
            violations.append("LLM02")
            risk_levels.append("CRITICAL")

    # ── Length anomaly ───────────────────────────────────────────────────────
    token_budget = int(role.category_b_overrides.get("token_budget", 0))
    estimated = _estimate_tokens(output)
    detail["token_estimate"] = estimated
    if token_budget > 0 and estimated > token_budget * 1.5:
        detail["length_anomaly"] = True
        logger.warning(
            "guardrails.LLM02: length anomaly estimated=%d budget=%d",
            estimated,
            token_budget,
        )
        violations.append("LLM02")
        risk_levels.append("MEDIUM")

    # ── JSON schema validation ───────────────────────────────────────────────
    schema_str = role.category_b_overrides.get("response_schema")
    if schema_str and output.strip().startswith("{"):
        try:
            schema = json.loads(str(schema_str)) if isinstance(schema_str, str) else schema_str
            output_data = json.loads(output)
            # Basic key presence check (full jsonschema validation is optional dep)
            required = schema.get("required", [])
            missing = [k for k in required if k not in output_data]
            if missing:
                detail["schema_valid"] = False
                detail["schema_missing_keys"] = missing
                violations.append("LLM02")
                risk_levels.append("MEDIUM")
            else:
                detail["schema_valid"] = True
        except (json.JSONDecodeError, TypeError) as exc:
            detail["schema_valid"] = False
            detail["schema_error"] = str(exc)

    # ── Result ───────────────────────────────────────────────────────────────
    if not violations:
        return [], "LOW", detail, None

    risk = max(risk_levels, key=lambda r: ["LOW", "MEDIUM", "HIGH", "CRITICAL"].index(r),
               default="LOW")
    return list(set(violations)), risk, detail, None
