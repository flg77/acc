"""Assistant-role support modules.

The assistant role is ACC's router + moderator + (per proposal 019)
system operator.  This package holds the pure-logic helpers the
assistant's skills wrap — kept out of the role.yaml / cognitive_core
so they're unit-testable without the NATS + LLM machinery.

Modules:
* :mod:`acc.assistant.catalog_view` — proposal 019 PR-OP1: a
  read-only, unified view of what roles the ecosystem provides
  (installed in-tree + installed-from-package + available-in-catalog),
  so the assistant routes to the genuinely best-matched role rather
  than only the ones that happen to be running.
* :mod:`acc.assistant.gap_analysis` — proposal 019 PR-OP4: request-time
  role-gap discovery.  When the best-matched role's confidence is below
  threshold, recognise the gap and propose a remedy (infuse a known
  pack / extend an installed role / author a new role), grounded in
  reviewer + compliance_officer feedback evidence.
"""

from __future__ import annotations

from acc.assistant.catalog_view import (
    AvailablePackageEntry,
    CatalogView,
    RoleCatalogEntry,
    build_catalog_view,
)
from acc.assistant.gap_analysis import (
    DEFAULT_ROLE_GAP_THRESHOLD,
    GapEvidence,
    RoleGapFinding,
    analyze_role_gap,
    build_evidence,
    infer_gap_kind,
    parse_role_gap_markers,
)

__all__ = [
    "AvailablePackageEntry",
    "CatalogView",
    "RoleCatalogEntry",
    "build_catalog_view",
    # gap discovery (PR-OP4)
    "DEFAULT_ROLE_GAP_THRESHOLD",
    "GapEvidence",
    "RoleGapFinding",
    "analyze_role_gap",
    "build_evidence",
    "infer_gap_kind",
    "parse_role_gap_markers",
]
