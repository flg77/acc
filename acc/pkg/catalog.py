"""Layered catalog loader + resolver — Stage 0 slice 6.

The catalog file is the seam between Assistant decisions and
``acc-pkg install`` (brainstorm Q3b).  It declares **where** packages
live, **what tier** they belong to, and **which signer identity**
their cosign signatures must match.

Layering
--------

Three locations, narrowest scope wins on name collisions:

* **System**:    ``/etc/acc/catalogs.yaml`` (root-installed)
* **User**:      ``~/.acc/catalogs.yaml``
* **Workspace**: ``<cwd>/.acc/catalogs.yaml`` (per-collective override)

Within a single layer, ``priority:`` (higher wins) breaks ties when
two catalogs advertise the same ``@scope/name``.  Lower-numbered
priority = lower precedence (npm-style, not nice-style).

Modes
-----

* ``mode: https`` — fetches ``<url>/index.json`` listing available
  ``(name, version, sha256, signature_url)`` tuples.
* ``mode: file`` — globs ``<path>/<scope>/<name>-*.accpkg`` directly.
  Used for offline / edge bundles AND for the Stage-0 dev hub on
  acc1 K8s (operator publishes via ``kubectl cp`` → glob picks it up).

Both modes feed the same resolver; ``acc-pkg install`` doesn't care
which one served the bytes — the signing-floor check (slice 7)
applies identically.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger("acc.pkg.catalog")


# ---------------------------------------------------------------------------
# Default catalog file locations
# ---------------------------------------------------------------------------

SYSTEM_CATALOG_PATH = Path("/etc/acc/catalogs.yaml")
USER_CATALOG_PATH = Path.home() / ".acc" / "catalogs.yaml"
WORKSPACE_CATALOG_DIR = ".acc"


def system_catalog_path() -> Path:
    return Path(os.environ.get("ACC_SYSTEM_CATALOG", str(SYSTEM_CATALOG_PATH)))


def user_catalog_path() -> Path:
    return Path(os.environ.get("ACC_USER_CATALOG", str(USER_CATALOG_PATH)))


def workspace_catalog_path(workspace: Path | None = None) -> Path:
    ws = workspace or Path.cwd()
    return ws / WORKSPACE_CATALOG_DIR / "catalogs.yaml"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


# The signing tiers permitted by brainstorm Q3b.  Difference between
# them is policy depth, not signing presence — every tier requires a
# valid cosign signature against ``required_signer`` (slice 7
# enforces).
Tier = Literal["trusted", "tp", "community", "self"]
Mode = Literal["https", "file"]


class RequiredSigner(BaseModel):
    """Cosign identity a catalog requires of its signers.

    Two verification modes, distinguished by ``key_path``:

    * **Keyless (Stage 1+, default)**: ``issuer`` + ``subject_pattern``
      drive ``cosign verify-blob --certificate-oidc-issuer ...
      --certificate-identity-regexp ...``.
    * **Keypair (Stage 0 pilot)**: ``key_path`` points at a local
      cosign public-key PEM file; ``cosign verify-blob --key <path>``.
      ``issuer`` is kept (as a free-form audit label) but the regex
      check is skipped because the key IS the identity.
    """

    model_config = ConfigDict(extra="forbid")

    issuer: str = Field(..., description="OIDC issuer URL (or audit label in keypair mode)")
    subject_pattern: str = Field(
        ..., description="Regex matched against the cert subject (ignored in keypair mode)"
    )
    key_path: str = Field(
        "",
        description=(
            "If set, switches to keypair-mode verification using this "
            "PEM public key (Stage 0 pilot)."
        ),
    )

    @property
    def mode(self) -> str:
        return "keypair" if self.key_path else "keyless"

    @model_validator(mode="after")
    def _check_regex(self) -> "RequiredSigner":
        # In keyless mode subject_pattern must compile; in keypair mode
        # it's advisory only but we still validate it to catch typos.
        try:
            re.compile(self.subject_pattern)
        except re.error as exc:
            raise ValueError(
                f"invalid subject_pattern regex: {exc}"
            ) from exc
        return self


class Catalog(BaseModel):
    """One catalog entry as it appears in ``catalogs.yaml``."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    tier: Tier
    mode: Mode
    required_signer: RequiredSigner
    priority: int = 100
    url: str = ""
    path: str = ""

    @model_validator(mode="after")
    def _check_mode_url_path(self) -> "Catalog":
        if self.mode == "https":
            if not self.url:
                raise ValueError("mode=https catalogs require url")
            if self.path:
                raise ValueError("mode=https catalogs must not declare path")
            if not (self.url.startswith("https://") or self.url.startswith("http://")):
                raise ValueError(
                    f"https catalog url must start with http(s)://: {self.url!r}"
                )
        else:  # file
            if not self.path:
                raise ValueError("mode=file catalogs require path")
            if self.url:
                raise ValueError("mode=file catalogs must not declare url")
        return self


class CatalogFile(BaseModel):
    """Top-level container — ``catalogs:`` as a list."""

    model_config = ConfigDict(extra="forbid")

    catalogs: list[Catalog] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Catalog index (the data a catalog serves)
# ---------------------------------------------------------------------------


class CatalogIndexEntry(BaseModel):
    """One row of a catalog's ``index.json`` (https mode) or one matched
    glob entry (file mode).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    tarball_sha256: str = Field(..., min_length=64, max_length=64)
    tarball_url: str = ""             # https mode
    tarball_path: str = ""            # file mode
    signature_url: str = ""           # https mode (relative or absolute)
    signature_path: str = ""          # file mode


class ResolvedPackage(BaseModel):
    """The catalog resolver's output: chosen catalog + index entry,
    plus any alternates that also advertise this ``@scope/name``.
    """

    model_config = ConfigDict(extra="forbid")

    catalog: Catalog
    entry: CatalogIndexEntry
    alternates: list[Catalog] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Layered loading
# ---------------------------------------------------------------------------


def _load_one(path: Path) -> list[Catalog]:
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed YAML in {path}: {exc}") from exc
    parsed = CatalogFile.model_validate(data)
    return parsed.catalogs


def load_catalogs(workspace: Path | None = None) -> list[list[Catalog]]:
    """Return ``[system_catalogs, user_catalogs, workspace_catalogs]``.

    The outer list preserves layer order from broad to narrow; the
    resolver walks it in reverse so workspace wins.  Empty layers
    produce empty inner lists.
    """
    return [
        _load_one(system_catalog_path()),
        _load_one(user_catalog_path()),
        _load_one(workspace_catalog_path(workspace)),
    ]


# ---------------------------------------------------------------------------
# Index fetching — per mode
# ---------------------------------------------------------------------------


class IndexFetchError(Exception):
    """An index could not be fetched or parsed.

    :func:`fetch_index` swallows this to an empty list so the resolver can keep
    walking other catalogs; :func:`fetch_index_strict` re-raises it so the
    diagnostic path can tell an unreachable catalog apart from a genuinely
    absent package (proposal 032 Finding D).
    """


def _fetch_index_https_strict(catalog: Catalog) -> list[CatalogIndexEntry]:
    """Fetch + parse ``<url>/index.json`` for an https catalog, RAISING
    :class:`IndexFetchError` on any network/parse failure (no silent []).
    """
    index_url = catalog.url.rstrip("/") + "/index.json"
    try:
        with urllib.request.urlopen(index_url, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise IndexFetchError(f"GET {index_url}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise IndexFetchError(f"{index_url}: malformed index.json ({exc})") from exc
    return [CatalogIndexEntry.model_validate(e) for e in data.get("packages", [])]


def _fetch_index_https(catalog: Catalog) -> list[CatalogIndexEntry]:
    """Best-effort https index fetch: logs + returns [] on failure so the
    resolver can keep walking the remaining catalogs.
    """
    try:
        return _fetch_index_https_strict(catalog)
    except IndexFetchError as exc:
        logger.warning("catalog %s: %s", catalog.id, exc)
        return []


_ACCPKG_RE = re.compile(r"^(?P<name>[^/]+)-(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\.accpkg$")


def _fetch_index_file(catalog: Catalog) -> list[CatalogIndexEntry]:
    """Glob ``<path>/<scope>/<name>-<version>.accpkg`` for a file catalog.

    Filename pattern: ``<name>-<version>.accpkg`` directly under
    ``<path>/<scope>/`` — matches what the bundler + Stage-0 dev hub
    produce.  Sidecar ``.sha256`` and ``.sig`` files are picked up
    from the same dir.
    """
    base = Path(catalog.path)
    if not base.is_dir():
        return []
    entries: list[CatalogIndexEntry] = []
    for scope_dir in sorted(base.iterdir()):
        if not scope_dir.is_dir():
            continue
        scope = scope_dir.name
        for accpkg in sorted(scope_dir.glob("*.accpkg")):
            m = _ACCPKG_RE.match(accpkg.name)
            if not m:
                continue
            name = f"@{scope}/{m['name']}"
            version = m["version"]
            sha_file = accpkg.with_suffix(".accpkg.sha256")
            if not sha_file.is_file():
                logger.warning(
                    "catalog %s: %s missing sidecar sha256 — skipping",
                    catalog.id, accpkg.name,
                )
                continue
            sha = sha_file.read_text(encoding="utf-8").strip().split()[0]
            sig_file = accpkg.with_suffix(".accpkg.sig")
            entries.append(
                CatalogIndexEntry(
                    name=name,
                    version=version,
                    tarball_sha256=sha,
                    tarball_path=str(accpkg),
                    signature_path=str(sig_file) if sig_file.is_file() else "",
                )
            )
    return entries


def fetch_index(catalog: Catalog) -> list[CatalogIndexEntry]:
    """Mode-agnostic index fetcher (best-effort: [] on failure)."""
    if catalog.mode == "https":
        return _fetch_index_https(catalog)
    return _fetch_index_file(catalog)


def fetch_index_strict(catalog: Catalog) -> list[CatalogIndexEntry]:
    """Like :func:`fetch_index` but RAISES :class:`IndexFetchError` instead of
    swallowing fetch/parse failures — lets :func:`explain_resolution_failure`
    distinguish an unreachable catalog from a genuinely absent package
    (proposal 032 Finding D).
    """
    if catalog.mode == "https":
        return _fetch_index_https_strict(catalog)
    return _fetch_index_file(catalog)


def explain_resolution_failure(
    name: str, constraint: str, *, workspace: Path | None = None
) -> str:
    """Build a self-diagnosing message for a failed resolve (proposal 032 Finding D).

    The bare "no catalog advertises X matching C" cannot distinguish three very
    different causes: the catalog could not be FETCHED (egress / availability —
    the live coding-pack symptom hid here), the name is genuinely ABSENT, or the
    name IS published but no version satisfies the constraint. Re-walk the
    catalogs once (only on the failure path) and say which. Never let the
    diagnostic itself mask the original failure.
    """
    try:
        consulted: list[str] = []
        fetch_failures: dict[str, str] = {}
        versions_seen: list[str] = []
        for catalog in _iter_layered(workspace):
            consulted.append(catalog.id)
            try:
                index = fetch_index_strict(catalog)
            except IndexFetchError as exc:
                fetch_failures[catalog.id] = str(exc)
                continue
            versions_seen.extend(e.version for e in index if e.name == name)
        return _format_resolution_failure(
            name, constraint, consulted, fetch_failures, sorted(set(versions_seen))
        )
    except Exception as exc:  # noqa: BLE001 — diagnostics must never mask the failure
        return (
            f"no catalog advertises {name} matching {constraint!r} "
            f"(diagnostics unavailable: {exc})"
        )


def _format_resolution_failure(
    name: str,
    constraint: str,
    consulted: list[str],
    fetch_failures: dict[str, str],
    versions_seen: list[str],
) -> str:
    """Pure formatter for :func:`explain_resolution_failure` (unit-tested)."""
    head = f"no catalog advertises {name} matching {constraint!r}"
    detail: list[str] = []
    if not consulted:
        detail.append("no catalogs are configured")
    if fetch_failures:
        fails = "; ".join(f"{cid} ({err})" for cid, err in fetch_failures.items())
        detail.append(
            f"{len(fetch_failures)}/{len(consulted)} catalog(s) could not be fetched "
            f"(check egress / availability): {fails}"
        )
    if versions_seen:
        detail.append(
            f"{name} IS published at {', '.join(versions_seen)} but none satisfy "
            f"{constraint!r} — check the version constraint"
        )
    elif consulted and not fetch_failures:
        detail.append(
            f"{name} is not present in any reachable catalog "
            f"({', '.join(consulted)}) — stale index or wrong package name"
        )
    return head + (" — " + "; ".join(detail) if detail else "")


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def _iter_layered(workspace: Path | None) -> Iterator[Catalog]:
    """Yield catalogs in resolution order: workspace > user > system,
    and within each layer by priority desc.
    """
    layers = load_catalogs(workspace)
    # Reverse so workspace (last loaded) is walked first
    for layer in reversed(layers):
        # Stable sort: priority desc, then id asc for determinism
        for cat in sorted(layer, key=lambda c: (-c.priority, c.id)):
            yield cat


def resolve(
    name: str,
    version: str | None = None,
    *,
    workspace: Path | None = None,
) -> ResolvedPackage | None:
    """Resolve ``@scope/name@version`` against layered catalogs.

    With ``version=None``, returns the highest-priority catalog's
    *newest* entry for the name.  Alternates carry every other
    catalog that also advertises this name (regardless of version) —
    the Compliance pane displays them for operator override.

    Returns ``None`` if no catalog advertises the name (with the
    requested version, if specified).
    """
    primary: tuple[Catalog, CatalogIndexEntry] | None = None
    alternates: list[Catalog] = []

    for catalog in _iter_layered(workspace):
        try:
            index = fetch_index(catalog)
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog %s: index fetch failed (%s)", catalog.id, exc)
            continue

        matches = [e for e in index if e.name == name]
        if version is not None:
            matches = [e for e in matches if e.version == version]
        if not matches:
            continue

        if primary is None:
            # Take the newest version this catalog offers
            chosen = sorted(matches, key=lambda e: e.version, reverse=True)[0]
            primary = (catalog, chosen)
        else:
            alternates.append(catalog)

    if primary is None:
        return None

    cat, entry = primary
    return ResolvedPackage(catalog=cat, entry=entry, alternates=alternates)


def resolve_constraint(
    name: str,
    constraint: str,
    *,
    workspace: Path | None = None,
) -> ResolvedPackage | None:
    """Resolve ``@scope/name`` against a semver *constraint* (Stage 1.5.3).

    Like :func:`resolve` but accepts range constraints (``^1.2``,
    ``~1.2.3``, ``>=1.2 <2.0``, etc.) rather than an exact version.
    Walks layered catalogs in resolution order; within the chosen
    catalog picks the *highest* version satisfying the constraint.
    Alternates carry every other catalog that also advertises this
    name (for the Compliance pane).

    Returns ``None`` if no catalog has a version satisfying the
    constraint.
    """
    # Lazy import so acc.pkg.catalog doesn't pull _semver at module
    # load time (catalog is loaded by code paths that don't otherwise
    # need install-time semver math).
    from acc.pkg._semver import version_satisfies  # noqa: PLC0415

    primary: tuple[Catalog, CatalogIndexEntry] | None = None
    alternates: list[Catalog] = []

    for catalog in _iter_layered(workspace):
        try:
            index = fetch_index(catalog)
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog %s: index fetch failed (%s)", catalog.id, exc)
            continue

        matches = [
            e for e in index
            if e.name == name and version_satisfies(e.version, constraint)
        ]
        if not matches:
            continue

        if primary is None:
            chosen = sorted(matches, key=lambda e: e.version, reverse=True)[0]
            primary = (catalog, chosen)
        else:
            alternates.append(catalog)

    if primary is None:
        return None

    cat, entry = primary
    return ResolvedPackage(catalog=cat, entry=entry, alternates=alternates)


def list_available(
    name: str | None = None,
    *,
    workspace: Path | None = None,
) -> list[tuple[Catalog, CatalogIndexEntry]]:
    """List every advertised ``(catalog, entry)`` pair across layers.

    With ``name`` set, filter to that scoped name.  Useful for the
    Marketplace TUI pane (Stage 2) and ``acc-pkg list --available``.
    """
    out: list[tuple[Catalog, CatalogIndexEntry]] = []
    for catalog in _iter_layered(workspace):
        try:
            index = fetch_index(catalog)
        except Exception as exc:  # noqa: BLE001
            logger.warning("catalog %s: index fetch failed (%s)", catalog.id, exc)
            continue
        for entry in index:
            if name is None or entry.name == name:
                out.append((catalog, entry))
    return out
