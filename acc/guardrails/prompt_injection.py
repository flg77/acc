"""LLM01 — Prompt Injection detection guardrail (ACC-12 / OWASP LLM Top 10).

Two detection layers:
1. **Pattern matching**: regex patterns loaded from
   ``regulatory_layer/owasp/injection_patterns.yaml``.  Patterns are reloaded
   on SIGHUP without agent restart.
2. **Semantic distance** (optional, requires embedder): cosine distance from
   the role's declared purpose embedding.  Configured via
   ``ComplianceConfig.injection_distance_threshold`` (default 0.85).

Return signature (used by GuardrailEngine._collect)::

    (violations: list[str], risk_level: str, detail: dict, redacted: None)

``violations`` is ``['LLM01']`` when injection is detected, ``[]`` otherwise.
``risk_level`` is ``'CRITICAL'`` for high-confidence injection, ``'MEDIUM'`` for
single-layer positive.
"""

from __future__ import annotations

import logging
import os
import re
import signal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acc.config import ComplianceConfig, RoleDefinitionConfig

logger = logging.getLogger("acc.guardrails.prompt_injection")

# ---------------------------------------------------------------------------
# Pattern registry (lazily loaded from YAML)
# ---------------------------------------------------------------------------

_PATTERNS_PATH = os.environ.get(
    "ACC_INJECTION_PATTERNS_PATH",
    "regulatory_layer/owasp/injection_patterns.yaml",
)

_COMPILED_PATTERNS: list[re.Pattern] = []
_PATTERNS_VERSION: str = "unloaded"


def _load_patterns() -> None:
    """Load injection patterns from YAML file."""
    global _COMPILED_PATTERNS, _PATTERNS_VERSION
    path = Path(_PATTERNS_PATH)
    if not path.exists():
        logger.warning(
            "prompt_injection: patterns file not found at %s — using built-in defaults",
            _PATTERNS_PATH,
        )
        _COMPILED_PATTERNS = _builtin_patterns()
        _PATTERNS_VERSION = "builtin"
        return
    try:
        import yaml
        data = yaml.safe_load(path.read_text())
        patterns = []
        for group in data.get("groups", []):
            for p in group.get("patterns", []):
                try:
                    patterns.append(re.compile(p, re.IGNORECASE | re.DOTALL))
                except re.error as exc:
                    logger.warning("prompt_injection: bad pattern '%s': %s", p, exc)
        _COMPILED_PATTERNS = patterns or _builtin_patterns()
        _PATTERNS_VERSION = data.get("version", "unknown")
        logger.info(
            "prompt_injection: loaded %d patterns version=%s",
            len(_COMPILED_PATTERNS),
            _PATTERNS_VERSION,
        )
    except Exception as exc:
        logger.error("prompt_injection: failed to load patterns: %s", exc)
        _COMPILED_PATTERNS = _builtin_patterns()
        _PATTERNS_VERSION = "builtin-fallback"


def _builtin_patterns() -> list[re.Pattern]:
    """Minimal built-in injection patterns used when YAML file is absent."""
    raw = [
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|context)",
        r"disregard\s+(all\s+)?(previous|prior)\s+(instructions?|directives?)",
        r"you\s+are\s+now\s+(a\s+)?(different|new|another)\s+(AI|assistant|model|bot)",
        r"forget\s+(everything|all|your\s+instructions)",
        r"(system|developer|admin)\s*:\s*(override|bypass|unlock|ignore)",
        r"do\s+anything\s+now\b",
        r"jailbreak",
        r"prompt\s+injection",
        r"</?(system|instruction|context)>",
        r"\[\[?INST\]?\]",
    ]
    return [re.compile(p, re.IGNORECASE | re.DOTALL) for p in raw]


# Load on module import; re-register SIGHUP handler
_load_patterns()

try:
    signal.signal(signal.SIGHUP, lambda *_: _load_patterns())
except (OSError, AttributeError):
    pass  # SIGHUP not available on Windows


# ---------------------------------------------------------------------------
# Public guardrail function
# ---------------------------------------------------------------------------


async def check_injection(
    prompt: str,
    role: "RoleDefinitionConfig",
    config: "ComplianceConfig",
) -> tuple[list[str], str, dict[str, Any], None]:
    """Detect prompt injection in the user-facing prompt content.

    Args:
        prompt:  Input prompt text.
        role:    Active role definition (for semantic threshold override).
        config:  Active compliance configuration.

    Returns:
        ``(violations, risk_level, detail, None)`` — None redacted content (no
        redaction at input stage).
    """
    detail: dict[str, Any] = {
        "patterns_version": _PATTERNS_VERSION,
        "pattern_matches": [],
        "semantic_match": False,
    }

    # ── Layer 1: pattern matching ────────────────────────────────────────────
    pattern_matches: list[str] = []
    for pat in _COMPILED_PATTERNS:
        if pat.search(prompt):
            pattern_matches.append(pat.pattern)

    detail["pattern_matches"] = pattern_matches

    # ── Layer 2: semantic distance (optional — requires embedder on role) ────
    # Semantic check is only performed when:
    #  a) The prompt is > 50 tokens (naïve estimate)
    #  b) role.purpose is non-empty
    # Embedder is not available here (CognitiveCore owns the LLM backend),
    # so this layer is marked as "deferred" — CognitiveCore can compute it
    # post-embed and update the violation if needed.
    detail["semantic_match"] = False  # computed by CognitiveCore if enabled

    # ── Classify ─────────────────────────────────────────────────────────────
    if not pattern_matches:
        return [], "LOW", detail, None

    # Single pattern hit → MEDIUM; multiple hits → CRITICAL
    risk = "CRITICAL" if len(pattern_matches) >= 2 else "MEDIUM"
    logger.warning(
        "guardrails.LLM01: injection detected patterns=%d risk=%s",
        len(pattern_matches),
        risk,
    )
    return ["LLM01"], risk, detail, None
