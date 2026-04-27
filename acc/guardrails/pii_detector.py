"""LLM06 — Sensitive Information Disclosure / PHI detector guardrail (ACC-12).

Detects PII and PHI entities in task input and output.

Detection engine (selected automatically):
1. **Presidio** (preferred): Microsoft ``presidio-analyzer`` + ``presidio-anonymizer``.
   Fully local, offline-capable via spaCy ``en_core_web_lg`` model.
   Activated when ``presidio-analyzer`` is installed.
2. **Regex fallback**: covers EMAIL_ADDRESS, PHONE_NUMBER, US_SSN, CREDIT_CARD, IP_ADDRESS.
   Always available; no extra dependencies.

HIPAA PHI entity superset (when ``hipaa_mode=True``):
    PERSON, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, US_SSN,
    LOCATION, DATE_TIME, NRP, MEDICAL_LICENSE, US_PASSPORT

Severity mapping:
    CRITICAL  CREDIT_CARD, US_SSN, MEDICAL_LICENSE, US_PASSPORT
    HIGH      PHONE_NUMBER, US_BANK_NUMBER
    MEDIUM    PERSON, EMAIL_ADDRESS, LOCATION, NRP
    LOW       DATE_TIME, IP_ADDRESS

Return signatures::

    check_pii_pre  → (violations, risk_level, detail, None)
    check_pii_post → (violations, risk_level, detail, redacted_text | None)

``check_pii_pre``:  logs PII presence; does NOT redact (agent may legitimately need it).
``check_pii_post``: detects + optionally redacts when ``hipaa_mode=True``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from acc.config import ComplianceConfig

logger = logging.getLogger("acc.guardrails.pii_detector")

# ---------------------------------------------------------------------------
# Entity severity map
# ---------------------------------------------------------------------------

_CRITICAL_ENTITIES = {"CREDIT_CARD", "US_SSN", "MEDICAL_LICENSE", "US_PASSPORT"}
_HIGH_ENTITIES = {"PHONE_NUMBER", "US_BANK_NUMBER", "IBAN_CODE"}
_MEDIUM_ENTITIES = {"PERSON", "EMAIL_ADDRESS", "LOCATION", "NRP", "US_DRIVER_LICENSE"}
_LOW_ENTITIES = {"DATE_TIME", "IP_ADDRESS", "URL"}


def _entity_risk(entity_type: str) -> str:
    if entity_type in _CRITICAL_ENTITIES:
        return "CRITICAL"
    if entity_type in _HIGH_ENTITIES:
        return "HIGH"
    if entity_type in _MEDIUM_ENTITIES:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Regex fallback patterns
# ---------------------------------------------------------------------------

@dataclass
class _RegexMatch:
    entity_type: str
    start: int
    end: int
    text: str


_REGEX_PATTERNS = [
    ("US_SSN",       re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CREDIT_CARD",  re.compile(r"\b(?:4\d{12}(?:\d{3})?|5[1-5]\d{14}|3[47]\d{13}|6(?:011|5\d{2})\d{12})\b")),
    ("EMAIL_ADDRESS", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("PHONE_NUMBER",  re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b")),
    ("IP_ADDRESS",    re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
]


def _regex_detect(text: str) -> list[_RegexMatch]:
    matches: list[_RegexMatch] = []
    for entity_type, pattern in _REGEX_PATTERNS:
        for m in pattern.finditer(text):
            matches.append(_RegexMatch(
                entity_type=entity_type,
                start=m.start(),
                end=m.end(),
                text=m.group(),
            ))
    return matches


def _regex_redact(text: str, matches: list[_RegexMatch]) -> str:
    """Replace detected PII spans with ``<entity_type>`` placeholders."""
    # Sort by start position descending to preserve offsets during replacement
    for m in sorted(matches, key=lambda x: x.start, reverse=True):
        text = text[: m.start] + f"<{m.entity_type}>" + text[m.end:]
    return text


# ---------------------------------------------------------------------------
# Presidio integration (lazy)
# ---------------------------------------------------------------------------

_presidio_analyzer = None
_presidio_anonymizer = None
_presidio_available: bool | None = None  # None = not yet checked


def _get_presidio():
    """Lazy-load Presidio analyzer and anonymizer."""
    global _presidio_analyzer, _presidio_anonymizer, _presidio_available
    if _presidio_available is not None:
        return _presidio_available

    try:
        from presidio_analyzer import AnalyzerEngine
        from presidio_anonymizer import AnonymizerEngine
        _presidio_analyzer = AnalyzerEngine()
        _presidio_anonymizer = AnonymizerEngine()
        _presidio_available = True
        logger.info("pii_detector: Presidio analyzer loaded")
    except ImportError:
        logger.info(
            "pii_detector: presidio-analyzer not installed — using regex fallback. "
            "Install with: pip install presidio-analyzer presidio-anonymizer "
            "&& python -m spacy download en_core_web_lg"
        )
        _presidio_available = False
    return _presidio_available


def _presidio_detect(text: str, hipaa_mode: bool) -> list[dict[str, Any]]:
    """Detect PII/PHI entities using Presidio."""
    entities = [
        "PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "US_SSN",
        "IP_ADDRESS", "DATE_TIME", "LOCATION", "NRP", "US_DRIVER_LICENSE",
        "US_BANK_NUMBER", "IBAN_CODE", "MEDICAL_LICENSE", "US_PASSPORT",
    ]
    results = _presidio_analyzer.analyze(  # type: ignore[union-attr]
        text=text,
        entities=entities if hipaa_mode else entities[:8],
        language="en",
    )
    return [
        {
            "entity_type": r.entity_type,
            "start": r.start,
            "end": r.end,
            "score": r.score,
        }
        for r in results
        if r.score >= 0.7
    ]


def _presidio_redact(text: str, entities: list[dict]) -> str:
    """Redact detected entities using Presidio anonymizer."""
    from presidio_anonymizer.entities import RecognizerResult, OperatorConfig
    recognizer_results = [
        RecognizerResult(
            entity_type=e["entity_type"],
            start=e["start"],
            end=e["end"],
            score=e["score"],
        )
        for e in entities
    ]
    result = _presidio_anonymizer.anonymize(  # type: ignore[union-attr]
        text=text,
        analyzer_results=recognizer_results,
        operators={"DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"})},
    )
    return result.text


# ---------------------------------------------------------------------------
# Public guardrail functions
# ---------------------------------------------------------------------------


async def check_pii_pre(
    text: str,
    config: "ComplianceConfig",
) -> tuple[list[str], str, dict[str, Any], None]:
    """Pre-LLM PII check: detect and log; do NOT redact input.

    Args:
        text:   Input prompt text to check.
        config: Active compliance configuration.

    Returns:
        ``(violations, risk_level, detail, None)``.
    """
    entities = _detect(text, config.hipaa_mode)
    detail: dict[str, Any] = {
        "entities": entities,
        "engine": "presidio" if _presidio_available else "regex",
        "hipaa_mode": config.hipaa_mode,
    }

    if not entities:
        return [], "LOW", detail, None

    risk = max((_entity_risk(e["entity_type"]) for e in entities), default="LOW",
               key=lambda r: ["LOW", "MEDIUM", "HIGH", "CRITICAL"].index(r))

    logger.warning(
        "guardrails.LLM06: PII detected in input entities=%s risk=%s hipaa_mode=%s",
        [e["entity_type"] for e in entities],
        risk,
        config.hipaa_mode,
    )

    # Pre-LLM: log only; never block on PII in input (agent may legitimately handle PHI)
    return ["LLM06"], risk, detail, None


async def check_pii_post(
    text: str,
    config: "ComplianceConfig",
) -> tuple[list[str], str, dict[str, Any], str | None]:
    """Post-LLM PII check: detect + redact when HIPAA mode is active.

    Args:
        text:   LLM output text.
        config: Active compliance configuration.

    Returns:
        ``(violations, risk_level, detail, redacted_text | None)``.
        ``redacted_text`` is set only when ``hipaa_mode=True`` and PII was found.
    """
    entities = _detect(text, config.hipaa_mode)
    detail: dict[str, Any] = {
        "entities": entities,
        "engine": "presidio" if _presidio_available else "regex",
        "hipaa_mode": config.hipaa_mode,
        "redacted": False,
    }

    if not entities:
        return [], "LOW", detail, None

    risk = max((_entity_risk(e["entity_type"]) for e in entities), default="LOW",
               key=lambda r: ["LOW", "MEDIUM", "HIGH", "CRITICAL"].index(r))

    logger.warning(
        "guardrails.LLM06: PII detected in output entities=%s risk=%s",
        [e["entity_type"] for e in entities],
        risk,
    )

    # Redact when HIPAA mode is active
    redacted_text: str | None = None
    if config.hipaa_mode:
        redacted_text = _redact(text, entities)
        detail["redacted"] = True
        logger.info("guardrails.LLM06: output redacted for episode storage (HIPAA mode)")

    return ["LLM06"], risk, detail, redacted_text


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect(text: str, hipaa_mode: bool) -> list[dict[str, Any]]:
    """Detect PII/PHI entities using Presidio (preferred) or regex fallback."""
    if _get_presidio():
        return _presidio_detect(text, hipaa_mode)

    regex_matches = _regex_detect(text)
    return [
        {"entity_type": m.entity_type, "start": m.start, "end": m.end, "score": 1.0}
        for m in regex_matches
    ]


def _redact(text: str, entities: list[dict[str, Any]]) -> str:
    """Redact detected entities."""
    if _presidio_available:
        return _presidio_redact(text, entities)

    # Regex fallback redaction
    regex_matches = [
        _RegexMatch(
            entity_type=e["entity_type"],
            start=e["start"],
            end=e["end"],
            text=text[e["start"]:e["end"]],
        )
        for e in entities
    ]
    return _regex_redact(text, regex_matches)
