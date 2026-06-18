"""Execute an *approved* infuse — resolve a spec + actually install it.

033 WS-G Part 3.  The ``[PROPOSE_INFUSE:@scope/name@constraint:reason]``
marker parses into an :class:`acc.assistant_proposal.AssistantProposal`
and routes through ``decide_dispatch``.  This module owns the
"now-actually-install-it" step the EXECUTE path invokes: take a
``@scope/name@constraint`` spec, resolve it against the layered catalog
resolver (:mod:`acc.pkg.catalog`), and hand off to the existing
installer (:func:`acc.pkg.install.install`, reached transitively through
:func:`acc.pkg.fetch.fetch_and_install_closure` so the catalog walk +
signing-floor + dependency-closure all stay shared with the CLI /
boot-time path).

Design notes
------------

* **Thin sibling, not in install.py.**  ``acc.pkg.install`` must not
  import ``acc.pkg.fetch`` / ``acc.pkg.catalog`` (fetch already imports
  install — that would be a cycle).  This separate module is the seam.
* **Idempotent.**  Re-installing an already-satisfied ``(name,
  version)`` is a no-op: the installer reports ``was_already_installed``
  and we surface it as ``already_satisfied``.
* **Never raises.**  A resolve failure / signing-floor refusal / bad
  spec returns a result with ``ok=False`` + a one-line ``error`` so the
  dispatch loop can log + carry on (same posture as the rest of the
  assistant-proposal dispatch surface).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("acc.pkg.install_infuse")


@dataclass(frozen=True)
class InfuseInstallResult:
    """Small, JSON-friendly outcome of an executed infuse.

    Exactly one of the success fields (``installed_ref``) or ``error``
    is meaningful: ``ok`` disambiguates.  ``already_satisfied`` is True
    on an idempotent re-install (the package was present at a matching
    content hash; nothing was rewritten).
    """

    ok: bool
    name: str = ""
    version: str = ""
    installed_ref: str = ""          # "@scope/name@version", "" on error
    install_path: str = ""
    already_satisfied: bool = False
    error: str = ""


def execute_infuse_install(
    spec: str,
    *,
    allow_unsigned: bool = False,
    workspace: Optional[Path] = None,
    registry=None,
) -> InfuseInstallResult:
    """Resolve ``@scope/name@constraint`` against the catalog + install it.

    Args:
        spec: ``"@scope/name@constraint"`` (e.g. ``"@acc/coding-roles@^1.2"``).
            A bare ``"@scope/name"`` (no ``@constraint``) is accepted and
            treated as ``">=0.0.0"`` (match anything).
        allow_unsigned: Audit-logged bypass of the signing floor.  The
            EXECUTE wiring passes ``True`` only in ``operator_mode == "dev"``
            (consistent with proposal 034); prod stays strict.
        workspace: Optional workspace whose ``.acc/catalogs.yaml`` layers
            on top of user + system catalogs.
        registry: Override the install-target registry (tests use a tmp
            root; production uses the default).

    Returns:
        An :class:`InfuseInstallResult`.  Never raises — a resolve /
        verify / install failure is reported via ``ok=False`` + ``error``.
    """
    spec = (spec or "").strip()
    if not spec:
        return InfuseInstallResult(ok=False, error="empty infuse spec")

    # Parse the spec with the same helper the marker parser + collective
    # required_packages use, so the constraint grammar stays consistent.
    try:
        from acc.collective import parse_required_package  # noqa: PLC0415

        name, constraint = parse_required_package(spec)
    except Exception as exc:  # noqa: BLE001
        # A bare "@scope/name" (no @constraint) is legitimate — fall back
        # to match-anything rather than rejecting it.
        if spec.startswith("@") and "/" in spec and "@" not in spec[1:]:
            name, constraint = spec, ">=0.0.0"
        else:
            return InfuseInstallResult(
                ok=False, error=f"malformed infuse spec {spec!r}: {exc}"
            )
    constraint = (constraint or ">=0.0.0").strip() or ">=0.0.0"

    # Lazy import: fetch pulls catalog + verify + install; keeping it out
    # of module load means importing this sibling never drags the whole
    # fetch stack into hosts that only need the result type.
    from acc.pkg.fetch import (  # noqa: PLC0415
        FetchError,
        fetch_and_install_closure,
    )

    try:
        result = fetch_and_install_closure(
            name,
            constraint,
            workspace=workspace,
            registry=registry,
            allow_unsigned=allow_unsigned,
        )
    except FetchError as exc:
        logger.warning("infuse-install: %s@%s failed: %s", name, constraint, exc)
        return InfuseInstallResult(
            ok=False, name=name, error=f"{type(exc).__name__}: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 — content-hash / dep / unsafe-path
        logger.warning(
            "infuse-install: %s@%s install error: %s", name, constraint, exc
        )
        return InfuseInstallResult(
            ok=False, name=name, error=f"{type(exc).__name__}: {exc}"
        )

    inst = result.install
    entry_name = inst.entry.name
    entry_version = inst.entry.version
    logger.info(
        "infuse-install: %s@%s%s",
        entry_name,
        entry_version,
        " (already satisfied)" if inst.was_already_installed else "",
    )
    return InfuseInstallResult(
        ok=True,
        name=entry_name,
        version=entry_version,
        installed_ref=f"{entry_name}@{entry_version}",
        install_path=str(inst.install_path),
        already_satisfied=bool(inst.was_already_installed),
    )
