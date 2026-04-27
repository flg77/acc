"""OWASP LLM Top 10 grading for ACC agents (ACC-12).

Tracks per-LLMxx pass/fail rates and produces a graded compliance report
exportable as a ``OWASP-LLM-TOP10-2025`` evidence artifact.

Reference: OWASP Top 10 for LLM Applications 2025
           https://owasp.org/www-project-top-10-for-large-language-model-applications/
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

SPEC_VERSION = "OWASP_LLM_TOP10_2025"

# OWASP LLM Top 10 2025 catalogue
OWASP_CATALOGUE: dict[str, str] = {
    "LLM01": "Prompt Injection",
    "LLM02": "Sensitive Information Disclosure",
    "LLM03": "Supply Chain",
    "LLM04": "Data and Model Poisoning",
    "LLM05": "Improper Output Handling",
    "LLM06": "Excessive Agency",
    "LLM07": "System Prompt Leakage",
    "LLM08": "Vector and Embedding Weaknesses",
    "LLM09": "Misinformation",
    "LLM10": "Unbounded Consumption",
}

# ACC guardrail coverage per OWASP code
_GUARDRAIL_COVERAGE: dict[str, bool] = {
    "LLM01": True,   # prompt_injection.py
    "LLM02": True,   # output_handler.py (output handling → sensitive disclosure)
    "LLM03": False,  # supply chain — not in scope
    "LLM04": True,   # dos_shield.py (unbounded consumption / DoS)
    "LLM05": True,   # output_handler.py (improper output handling)
    "LLM06": True,   # pii_detector.py
    "LLM07": False,  # system prompt leakage — future
    "LLM08": True,   # agency_limiter.py
    "LLM09": False,  # misinformation — requires external fact-check
    "LLM10": True,   # dos_shield.py (unbounded consumption)
}


@dataclass
class LLMCodeStats:
    """Per-OWASP code statistics."""
    code: str
    description: str
    covered: bool
    checks: int = 0
    violations: int = 0

    @property
    def pass_rate(self) -> float:
        if self.checks == 0:
            return 1.0 if self.covered else 0.0
        return 1.0 - (self.violations / self.checks)


class OWASPGrader:
    """Tracks OWASP LLM Top 10 violation rates and produces graded reports.

    Thread-safe via simple in-process counters.  Metrics are reset on restart
    unless persisted externally (future: Redis backend).
    """

    def __init__(self) -> None:
        self._stats: dict[str, LLMCodeStats] = {
            code: LLMCodeStats(
                code=code,
                description=desc,
                covered=_GUARDRAIL_COVERAGE.get(code, False),
            )
            for code, desc in OWASP_CATALOGUE.items()
        }

    def record_check(self, codes: list[str]) -> None:
        """Record that guardrails for the given codes were executed.

        Args:
            codes:  List of OWASP codes checked (e.g. ``['LLM01', 'LLM04']``).
        """
        for code in codes:
            if code in self._stats:
                self._stats[code].checks += 1

    def record_violations(self, violations: list[str]) -> None:
        """Record violation detections.

        Args:
            violations:  List of OWASP violation codes triggered.
        """
        for code in violations:
            if code in self._stats:
                self._stats[code].violations += 1
                if self._stats[code].checks == 0:
                    self._stats[code].checks = 1  # ensure denominator

    def grade(self) -> dict[str, Any]:
        """Produce a graded OWASP LLM Top 10 report.

        Returns:
            Dict with ``overall_score``, per-code grades, and export metadata.
        """
        covered_stats = [s for s in self._stats.values() if s.covered]
        if covered_stats:
            overall = sum(s.pass_rate for s in covered_stats) / len(covered_stats)
        else:
            overall = 0.0

        return {
            "spec_version": SPEC_VERSION,
            "generated_at_ms": int(time.time() * 1000),
            "overall_score": round(overall, 4),
            "overall_grade": _score_to_letter(overall),
            "covered_count": sum(1 for s in self._stats.values() if s.covered),
            "total_count": len(self._stats),
            "codes": {
                code: {
                    "description": s.description,
                    "covered": s.covered,
                    "checks": s.checks,
                    "violations": s.violations,
                    "pass_rate": round(s.pass_rate, 4),
                    "grade": _score_to_letter(s.pass_rate) if s.covered else "N/A",
                }
                for code, s in self._stats.items()
            },
        }

    def snapshot(self) -> dict[str, Any]:
        """Return raw stats as a serializable dict."""
        return {code: asdict(s) for code, s in self._stats.items()}


def _score_to_letter(score: float) -> str:
    if score >= 0.95:
        return "A"
    if score >= 0.85:
        return "B"
    if score >= 0.70:
        return "C"
    if score >= 0.50:
        return "D"
    return "F"
