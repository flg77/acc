"""Autoresearcher utility surface (E5).

Currently exports the post-run citation verifier used by
``examples/acc_autoresearcher/verify.sh``.  Future analytics
(iteration-quality experiment, cluster-cohort telemetry) will
slot in here without polluting the main ``acc/`` namespace.
"""

from acc.research.citation_verifier import (
    CitationEntry,
    VerificationReport,
    extract_inline_citations,
    verify_against_invocations,
    summarise,
)

__all__ = [
    "CitationEntry",
    "VerificationReport",
    "extract_inline_citations",
    "verify_against_invocations",
    "summarise",
]
