"""Marketplace data adapter — Stage 2.4 partial (data layer).

Pure-Python adapter the Marketplace TUI/WebGUI surfaces consume.
Returns formatted rows the presentation layer renders.  Tests
exercise the data shape; the Textual + React surfaces wrap this
without business logic of their own.

Surfaces this powers:

* ``acc/tui/screens/marketplace.py`` (future TUI pane)
* ``acc/webgui/routes_roles.py`` (future WebGUI route)
* ``acc-podman-desktop`` extension (separate repo)

The Compliance pane's Package Proposals tab (PR #32) is the
*install* surface; this module is the *discovery* surface.  The
two compose: clicking Install here stages a PROPOSE_INFUSE that
lands in the Compliance pane queue for operator approval.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from acc.pkg.catalog import Catalog, CatalogIndexEntry, list_available

logger = logging.getLogger("acc.marketplace")


@dataclass(frozen=True)
class MarketplaceRow:
    """One row in the Marketplace table — the presentation contract.

    The TUI/WebGUI renders these directly; the Compliance pane's
    Package Proposals tab uses ``install_marker`` to stage approvals.
    """

    name: str                # @scope/name
    version: str             # exact semver
    tier: str                # trusted | tp | community | self
    tier_badge: str          # "[TRUSTED]", "[TP]", etc. — UI-friendly
    catalog_id: str          # which catalog advertises it
    catalog_mode: str        # https | file
    signer: str              # human-readable signer identity
    install_marker: str      # canonical PROPOSE_INFUSE marker text

    @property
    def installable(self) -> bool:
        """True iff this row is selectable for install (sanity bit)."""
        return bool(self.name and self.version)


# UI-friendly tier badges.  Order matches the brainstorm Q3b tier
# hierarchy (most-trusted first).
_TIER_BADGE: dict[str, str] = {
    "trusted":   "[TRUSTED]",
    "tp":        "[TP]",
    "community": "[COMMUNITY]",
    "self":      "[SELF]",
}


def _format_signer(signer) -> str:
    """Render the catalog's required_signer as a one-line label."""
    if signer.key_path:
        # Keypair mode — surface the key file name (Stage 0 pilot).
        return f"keypair:{Path(signer.key_path).name}"
    # Keyless mode — show issuer + truncated subject pattern.
    subject = signer.subject_pattern
    if len(subject) > 40:
        subject = subject[:37] + "..."
    return f"oidc:{signer.issuer}~{subject}"


def _format_install_marker(name: str, constraint: str) -> str:
    """Canonical PROPOSE_INFUSE marker text for an install action."""
    return f"[PROPOSE_INFUSE:{name}@{constraint}:operator-marketplace-action]"


def render_rows(
    *,
    name_filter: Optional[str] = None,
    workspace: Optional[Path] = None,
) -> list[MarketplaceRow]:
    """Return Marketplace-ready rows for every available package.

    ``name_filter`` (e.g. ``"@acc"``) matches catalog entries whose
    ``name`` starts with the substring — supports the search box the
    TUI/WebGUI presentation layer wires.
    """
    rows: list[MarketplaceRow] = []
    for catalog, entry in list_available(name=None, workspace=workspace):
        if name_filter and not entry.name.startswith(name_filter):
            continue
        rows.append(_row_for(catalog, entry))
    # Stable ordering: name asc, then version desc.  Matches what
    # operators expect when scrolling — group by package, latest
    # version at the top.
    rows.sort(key=lambda r: (r.name, _version_sort_key(r.version)))
    return rows


def list_versions(
    name: str, *, workspace: Optional[Path] = None,
) -> list[MarketplaceRow]:
    """All known versions of ``name`` across every layered catalog.

    The TUI version-picker dropdown calls this; ordering is highest
    version first.
    """
    rows = [
        _row_for(c, e)
        for c, e in list_available(name=name, workspace=workspace)
    ]
    rows.sort(key=lambda r: _version_sort_key(r.version), reverse=True)
    return rows


def stage_install(
    row: MarketplaceRow, *, constraint: Optional[str] = None,
) -> str:
    """Return the PROPOSE_INFUSE marker text for the operator's install
    intent.

    The TUI/WebGUI doesn't dispatch directly — it surfaces this marker
    to the Compliance pane's Package Proposals queue (PR #32) so the
    operator's approve/reject UX stays in one place.
    """
    if not row.installable:
        raise ValueError(f"row {row.name!r} is not installable")
    final = constraint or f"^{row.version.split('-')[0]}"
    return _format_install_marker(row.name, final)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _row_for(catalog: Catalog, entry: CatalogIndexEntry) -> MarketplaceRow:
    tier = catalog.tier
    return MarketplaceRow(
        name=entry.name,
        version=entry.version,
        tier=tier,
        tier_badge=_TIER_BADGE.get(tier, f"[{tier.upper()}]"),
        catalog_id=catalog.id,
        catalog_mode=catalog.mode,
        signer=_format_signer(catalog.required_signer),
        install_marker=_format_install_marker(entry.name, entry.version),
    )


def _version_sort_key(version: str) -> tuple:
    """Lexicographic sort key that orders semver-shaped versions
    correctly without depending on _semver's full constraint parser.
    """
    parts: list[int | str] = []
    for chunk in version.replace("-", ".").split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(chunk)
    return tuple(parts)
