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
"""

from __future__ import annotations

from acc.assistant.catalog_view import (
    AvailablePackageEntry,
    CatalogView,
    RoleCatalogEntry,
    build_catalog_view,
)

__all__ = [
    "AvailablePackageEntry",
    "CatalogView",
    "RoleCatalogEntry",
    "build_catalog_view",
]
