"""Capability validator — proposal 033 WS-A.

Verifies that the skills + MCP servers a role *references* actually
*resolve* to a loadable capability, and that the capability manifests a
package ships are well-formed, BEFORE a ``.accpkg`` is built or
installed.

Motivation (2026-06-16 TUI review).  The assistant surfaced
``SkillNotFound`` for ``pwd`` / ``disk_free`` — capabilities it is
granted (``os_basics``) and that are declared *core_baseline*
(:data:`acc.pkg.manifest.CORE_BASELINE_SKILLS`) but were absent from the
running registry.  A role that lists a skill/MCP which nothing provides
is a *configuration* error that should be caught at author / package
time, not surfaced to an operator at runtime.  (The other half of that
screenshot — the assistant emitting malformed ``[SKILL:...]`` markers to
*describe* its tools, yielding ``json_decode`` — is runtime behaviour,
not config; it is addressed separately, see proposal 033 §1.)

Resolution model.  A reference resolves when the id is present in any of:

* the capabilities shipped *inside the package* (``skills/`` + ``mcps/``
  under the source tree), or
* the *in-tree* capabilities of the building/installing ACC
  (``ACC_SKILLS_ROOT`` / ``ACC_MCPS_ROOT``), or
* the **core-baseline** floor (:data:`CORE_BASELINE_SKILLS` /
  :data:`CORE_BASELINE_MCPS`) — guaranteed to ship with ACC core by
  contract.

Three entry points:

* :func:`validate_role_capabilities` — pure check of one loaded
  role config (anything exposing ``allowed_skills`` /
  ``default_skills`` / ``allowed_mcps`` / ``default_mcps``) against a
  known available set.  No I/O; trivially unit-testable.
* :func:`validate_roles_dir` — load every in-tree role under a
  ``roles/`` root and validate it against the in-tree caps.  The CI /
  ``acc-pkg lint`` guard for the control roles (unresolved → ERROR,
  because in-tree roles have no external dependency to defer to).
* :func:`validate_package_tree` — validate an unpacked ``.accpkg``
  source tree (its own caps ∪ in-tree ∪ core-baseline).  Wired into
  :func:`acc.pkg.build.build` so a broken pack can't ship.  Unresolved
  refs are WARNINGs here (a declared dependency may satisfy them);
  malformed manifests and ``default_*`` ⊄ ``allowed_*`` are ERRORs.

Severity policy mirrors the operator's role-catalogue hybrid validation:
structural breakage hard-fails; ambiguous-by-context (a pack reference
that a dependency could satisfy) warns.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from acc.pkg.manifest import CORE_BASELINE_MCPS, CORE_BASELINE_SKILLS
from acc.skills.loader import SkillLoader, list_skills

if TYPE_CHECKING:  # pragma: no cover
    from acc.config import RoleDefinitionConfig  # noqa: F401

logger = logging.getLogger("acc.capability_validator")

ERROR = "ERROR"
WARNING = "WARNING"

# Mirror the loader exclusions so discovery agrees with the registries.
_EXCLUDED_DIRS = {"_base", "TEMPLATE", "__pycache__"}


@dataclass(frozen=True)
class ValidationFinding:
    """One validation result.

    Attributes:
        severity: :data:`ERROR` or :data:`WARNING`.
        code: stable machine code (e.g. ``"skill_unresolved"``) for
            programmatic handling / TUI grouping.
        location: where the problem is (``"role:assistant"``,
            ``"skill:pwd"``, ``"mcp:web_fetch"``).
        message: human-readable detail.
    """

    severity: str
    code: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.location}: {self.message}"


class PackageValidationError(ValueError):
    """Raised by :func:`acc.pkg.build.build` when a package has ERROR findings.

    Carries the structured findings on ``.findings`` so callers (CLI,
    TUI, tests) can render them without re-parsing the message.
    """

    def __init__(self, findings: Iterable[ValidationFinding]) -> None:
        self.findings: list[ValidationFinding] = list(findings)
        detail = "; ".join(str(f) for f in self.findings) or "no detail"
        super().__init__(f"package failed capability validation: {detail}")


def has_errors(findings: Iterable[ValidationFinding]) -> bool:
    """True when any finding is an :data:`ERROR`."""
    return any(f.severity == ERROR for f in findings)


def format_findings(findings: Iterable[ValidationFinding]) -> str:
    """Render findings one per line (``"no findings"`` when empty)."""
    rendered = "\n".join(str(f) for f in findings)
    return rendered or "no findings"


# ---------------------------------------------------------------------------
# Root resolution (mirrors acc.skills.registry / acc.mcp.registry defaults)
# ---------------------------------------------------------------------------


def _skills_root_default() -> str:
    return os.environ.get("ACC_SKILLS_ROOT", "skills")


def _mcps_root_default() -> str:
    return os.environ.get("ACC_MCPS_ROOT", "mcps")


# ---------------------------------------------------------------------------
# Available-capability discovery — scans EXACTLY the given root (no
# dual-source), validating each manifest as a side effect.
# ---------------------------------------------------------------------------


def _available_skills(root: str | Path) -> tuple[set[str], list[ValidationFinding]]:
    """Return (loadable skill ids under *root*, manifest findings).

    Manifest validity is checked via the loader's manifest-only path
    (no adapter import) — a ``skill.yaml`` that fails YAML / Pydantic
    validation yields a ``skill_manifest_invalid`` ERROR and is excluded
    from the available set.
    """
    root = Path(root)
    findings: list[ValidationFinding] = []
    available: set[str] = set()
    for skill_id in list_skills(root):
        if SkillLoader(root, skill_id).manifest() is not None:
            available.add(skill_id)
        else:
            findings.append(
                ValidationFinding(
                    ERROR,
                    "skill_manifest_invalid",
                    f"skill:{skill_id}",
                    f"skill.yaml under {root} failed to load/validate",
                )
            )
    return available, findings


def _available_mcps(root: str | Path) -> tuple[set[str], list[ValidationFinding]]:
    """Return (loadable MCP server ids under *root*, manifest findings)."""
    # Lazy import: acc.mcp.registry pulls the MCP client (httpx); keep
    # this module import-light for callers that only need skill/role
    # validation.
    from acc.mcp.registry import (  # noqa: PLC0415
        MCPRegistry,
        list_mcp_server_ids,
    )

    root = Path(root)
    declared = set(list_mcp_server_ids(root))
    registry = MCPRegistry()
    # Explicit base_dir → scans exactly this directory (no dual-source).
    registry.load_from(root)
    available = set(registry.list_server_ids())
    findings = [
        ValidationFinding(
            ERROR,
            "mcp_manifest_invalid",
            f"mcp:{server_id}",
            f"mcp.yaml under {root} failed to load/validate",
        )
        for server_id in sorted(declared - available)
    ]
    return available, findings


# ---------------------------------------------------------------------------
# Pure reference check
# ---------------------------------------------------------------------------


def validate_role_capabilities(
    role_id: str,
    role: "RoleDefinitionConfig | Any",
    *,
    available_skills: set[str],
    available_mcps: set[str],
    unresolved_severity: str = ERROR,
) -> list[ValidationFinding]:
    """Check one role's capability references against an available set.

    Pure — *role* need only expose ``allowed_skills`` /
    ``default_skills`` / ``allowed_mcps`` / ``default_mcps`` (a
    :class:`acc.config.RoleDefinitionConfig` or any duck-typed
    stand-in).  Validate the *loaded* config so framework auto-grants
    (``os_basics`` / ``workspace_access``) are already folded into the
    lists.

    Two finding classes:

    * ``skill_unresolved`` / ``mcp_unresolved`` — an ``allowed_*`` id
      resolves to nothing.  Severity is *unresolved_severity*
      (ERROR for in-tree roles, WARNING at pack build).
    * ``default_skill_not_allowed`` / ``default_mcp_not_allowed`` — a
      ``default_*`` id is not contained in its ``allowed_*`` list.
      Always ERROR (a self-contradiction independent of context).
    """
    findings: list[ValidationFinding] = []

    allowed_skills = set(getattr(role, "allowed_skills", None) or [])
    default_skills = list(getattr(role, "default_skills", None) or [])
    allowed_mcps = set(getattr(role, "allowed_mcps", None) or [])
    default_mcps = list(getattr(role, "default_mcps", None) or [])

    for skill_id in sorted(allowed_skills):
        if skill_id not in available_skills:
            findings.append(
                ValidationFinding(
                    unresolved_severity,
                    "skill_unresolved",
                    f"role:{role_id}",
                    f"allowed_skill {skill_id!r} resolves to no loadable skill "
                    "(not in package, in-tree, or core-baseline)",
                )
            )
    for skill_id in default_skills:
        if skill_id not in allowed_skills:
            findings.append(
                ValidationFinding(
                    ERROR,
                    "default_skill_not_allowed",
                    f"role:{role_id}",
                    f"default_skill {skill_id!r} is not in allowed_skills",
                )
            )

    for server_id in sorted(allowed_mcps):
        if server_id not in available_mcps:
            findings.append(
                ValidationFinding(
                    unresolved_severity,
                    "mcp_unresolved",
                    f"role:{role_id}",
                    f"allowed_mcp {server_id!r} resolves to no loadable MCP "
                    "server (not in package, in-tree, or core-baseline)",
                )
            )
    for server_id in default_mcps:
        if server_id not in allowed_mcps:
            findings.append(
                ValidationFinding(
                    ERROR,
                    "default_mcp_not_allowed",
                    f"role:{role_id}",
                    f"default_mcp {server_id!r} is not in allowed_mcps",
                )
            )

    return findings


# ---------------------------------------------------------------------------
# Role loading helpers
# ---------------------------------------------------------------------------


def _list_role_ids(roles_root: str | Path) -> list[str]:
    root = Path(roles_root)
    if not root.is_dir():
        return []
    out: list[str] = []
    for candidate in sorted(root.iterdir()):
        if not candidate.is_dir():
            continue
        if candidate.name in _EXCLUDED_DIRS:
            continue
        if not (candidate / "role.yaml").is_file():
            continue
        out.append(candidate.name)
    return out


def _load_role_def(roles_root: str | Path, role_id: str) -> "Any | None":
    """Load a role via :class:`acc.role_loader.RoleLoader`, returning the
    object exposing ``allowed_skills`` (or ``None`` on any failure)."""
    from acc.role_loader import RoleLoader  # noqa: PLC0415

    try:
        loaded = RoleLoader(roles_root, role_id).load()
    except Exception:  # noqa: BLE001 — surface as a finding, never raise here
        logger.warning("capability_validator: role %r failed to load", role_id)
        return None
    if loaded is None:
        return None
    # RoleLoader.load() may return the config directly or a wrapper.
    for attr in ("role_definition", "definition", "config"):
        inner = getattr(loaded, attr, None)
        if inner is not None and hasattr(inner, "allowed_skills"):
            return inner
    if hasattr(loaded, "allowed_skills"):
        return loaded
    return None


# ---------------------------------------------------------------------------
# Directory + package entry points
# ---------------------------------------------------------------------------


def validate_roles_dir(
    roles_root: str | Path,
    *,
    skills_root: str | Path | None = None,
    mcps_root: str | Path | None = None,
) -> list[ValidationFinding]:
    """Validate every in-tree role under *roles_root*.

    Unresolved references are ERRORs — an in-tree (control) role has no
    external dependency to defer to, so a reference that resolves in
    neither the in-tree caps nor the core-baseline floor is a real bug.
    """
    skills_root = skills_root if skills_root is not None else _skills_root_default()
    mcps_root = mcps_root if mcps_root is not None else _mcps_root_default()

    avail_skills, skill_findings = _available_skills(skills_root)
    avail_mcps, mcp_findings = _available_mcps(mcps_root)
    avail_skills |= set(CORE_BASELINE_SKILLS)
    avail_mcps |= set(CORE_BASELINE_MCPS)

    findings: list[ValidationFinding] = [*skill_findings, *mcp_findings]
    for role_id in _list_role_ids(roles_root):
        role = _load_role_def(roles_root, role_id)
        if role is None:
            findings.append(
                ValidationFinding(
                    ERROR,
                    "role_invalid",
                    f"role:{role_id}",
                    "role.yaml failed to load/validate",
                )
            )
            continue
        findings.extend(
            validate_role_capabilities(
                role_id,
                role,
                available_skills=avail_skills,
                available_mcps=avail_mcps,
                unresolved_severity=ERROR,
            )
        )
    return findings


def validate_package_tree(
    source_dir: str | Path,
    *,
    in_tree_skills_root: str | Path | None = None,
    in_tree_mcps_root: str | Path | None = None,
    unresolved_severity: str = WARNING,
) -> list[ValidationFinding]:
    """Validate an unpacked ``.accpkg`` source tree.

    Checks (a) every ``skills/`` + ``mcps/`` manifest the package ships
    loads cleanly, and (b) every role under ``roles/`` references only
    capabilities resolvable in (pack ∪ in-tree ∪ core-baseline).

    Unresolved references default to WARNING because a declared
    dependency package may legitimately provide them — the caller
    (build) surfaces warnings but only hard-fails on ERROR findings.
    """
    source_dir = Path(source_dir)
    in_tree_skills_root = (
        in_tree_skills_root if in_tree_skills_root is not None else _skills_root_default()
    )
    in_tree_mcps_root = (
        in_tree_mcps_root if in_tree_mcps_root is not None else _mcps_root_default()
    )

    findings: list[ValidationFinding] = []

    # (a) Pack-shipped capability manifests must load.
    pack_skills, pack_skill_findings = _available_skills(source_dir / "skills")
    pack_mcps, pack_mcp_findings = _available_mcps(source_dir / "mcps")
    findings.extend(pack_skill_findings)
    findings.extend(pack_mcp_findings)

    # Resolution set = pack ∪ in-tree ∪ core-baseline.
    intree_skills, _ = _available_skills(in_tree_skills_root)
    intree_mcps, _ = _available_mcps(in_tree_mcps_root)
    available_skills = pack_skills | intree_skills | set(CORE_BASELINE_SKILLS)
    available_mcps = pack_mcps | intree_mcps | set(CORE_BASELINE_MCPS)

    # (b) Packaged roles must reference resolvable capabilities.
    for role_id in _list_role_ids(source_dir / "roles"):
        role = _load_role_def(source_dir / "roles", role_id)
        if role is None:
            findings.append(
                ValidationFinding(
                    ERROR,
                    "role_invalid",
                    f"role:{role_id}",
                    "role.yaml failed to load/validate",
                )
            )
            continue
        findings.extend(
            validate_role_capabilities(
                role_id,
                role,
                available_skills=available_skills,
                available_mcps=available_mcps,
                unresolved_severity=unresolved_severity,
            )
        )
    return findings
