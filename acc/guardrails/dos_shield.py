"""LLM04 — Model Denial of Service shield guardrail (ACC-12 / OWASP LLM Top 10).

Checks:
1. **Token budget pre-check**: estimated token count > ``token_budget`` → CRITICAL block.
   Prevents burning the token budget on oversized inputs.
2. **Recursive expansion**: regex patterns for "repeat N times", "generate 10000 lines".
   Indicates an attempt to exhaust the context window or inference budget.

Return signature (used by GuardrailEngine._collect)::

    (violations: list[str], risk_level: str, detail: dict, redacted: None)
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acc.config import ComplianceConfig, RoleDefinitionConfig

logger = logging.getLogger("acc.guardrails.dos_shield")

# Naïve token estimation: 1 token ≈ 0.75 words for English text
_WORDS_PER_TOKEN = 0.75

# Recursive expansion patterns — detect prompts designed to exhaust context/inference budget
_EXPANSION_PATTERNS = [
    # "repeat ... 5000 times" — any text between 'repeat' and a large number followed by 'times'
    re.compile(r"repeat\b.{0,60}\b\d{3,}\s+times", re.IGNORECASE),
    re.compile(r"generate\s+\d{4,}\s+(?:lines|words|tokens|characters)", re.IGNORECASE),
    re.compile(r"(?:write|output|produce)\s+\d{4,}", re.IGNORECASE),
    re.compile(r"(?:say|print|echo)\s+.{1,50}\s+\d{3,}\s+times", re.IGNORECASE),
    re.compile(r"expand\s+(?:this|the|each)\s+.{1,50}\s+\d{3,}\s+times", re.IGNORECASE),
    re.compile(r"infinite\s+loop", re.IGNORECASE),
    re.compile(r"(?:fill|pad)\s+(?:with|the\s+response\s+with)\s+\d{4,}", re.IGNORECASE),
]


def _estimate_tokens(text: str) -> int:
    """Naïve token estimation (avoids tokenizer dependency)."""
    word_count = len(text.split())
    return int(word_count / _WORDS_PER_TOKEN)


async def check_dos(
    prompt: str,
    role: "RoleDefinitionConfig",
    config: "ComplianceConfig",
) -> tuple[list[str], str, dict[str, Any], None]:
    """Check prompt for DoS attack patterns.

    Args:
        prompt:  Input prompt text.
        role:    Active role definition (token_budget from category_b_overrides).
        config:  Active compliance configuration.

    Returns:
        ``(violations, risk_level, detail, None)``.
    """
    detail: dict[str, Any] = {
        "estimated_tokens": 0,
        "token_budget": 0,
        "over_budget": False,
        "expansion_patterns": [],
    }

    # ── Token budget pre-check ───────────────────────────────────────────────
    token_budget = int(role.category_b_overrides.get("token_budget", 0))
    estimated = _estimate_tokens(prompt)
    detail["estimated_tokens"] = estimated
    detail["token_budget"] = token_budget

    if token_budget > 0 and estimated > token_budget:
        detail["over_budget"] = True
        logger.warning(
            "guardrails.LLM04: token over-budget estimated=%d budget=%d",
            estimated,
            token_budget,
        )
        return ["LLM04"], "CRITICAL", detail, None

    # ── Recursive expansion detection ────────────────────────────────────────
    expansion_hits: list[str] = []
    for pat in _EXPANSION_PATTERNS:
        if pat.search(prompt):
            expansion_hits.append(pat.pattern)

    detail["expansion_patterns"] = expansion_hits

    if expansion_hits:
        logger.warning(
            "guardrails.LLM04: expansion patterns detected count=%d",
            len(expansion_hits),
        )
        risk = "CRITICAL" if len(expansion_hits) >= 2 else "MEDIUM"
        return ["LLM04"], risk, detail, None

    return [], "LOW", detail, None
