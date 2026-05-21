"""Atomic writers for `roles/<role>/role.yaml` and `role.md`.

The Ecosystem screen's inline editor (PR-A of the workflow rework)
persists role-file edits through this module.  Mirrors the
:mod:`acc.tui.env_writeback` pattern but adds Pydantic pre-write
validation for role.yaml — the new contents must parse as a valid
``RoleDefinitionConfig`` after `_base` merge.  An invalid write
raises :class:`RoleValidationError` carrying the pydantic error trail
so the TUI can surface it in the `#yaml-save-status` line.

role.md is free-form operator narrative and is written without
schema validation.

Both functions go through :func:`acc._atomic_write.atomic_write_text`
— so the EBUSY fallback for bind-mounted targets (the agent
containers mount `roles/` read-only at `/app/roles`), single-rotation
``.bak`` backup, and POSIX `fcntl.flock` serialisation all apply.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from acc._atomic_write import atomic_write_text

logger = logging.getLogger("acc.tui.role_writeback")


class RoleValidationError(ValueError):
    """The candidate role.yaml didn't parse as a valid `RoleDefinitionConfig`.

    Attributes:
        errors: optional list of pydantic error dicts (as returned by
            ``exc.errors()``).  Empty when the failure was a non-pydantic
            error (YAML parse, missing file, etc.).
    """

    def __init__(self, message: str, errors: list[dict] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


def validate_role_yaml(
    yaml_text: str,
    role_name: str,
    roles_root: Path | str = "roles",
) -> None:
    """Validate *yaml_text* as a role.yaml for *role_name*; raise on failure.

    Materialises a tiny in-tmp `roles/` tree containing a copy of
    `_base/role.yaml` (if present in the real root) plus the candidate
    `<role_name>/role.yaml`, then runs :class:`acc.role_loader.RoleLoader`
    against it.  Raises :class:`RoleValidationError` on any pydantic /
    YAML / IO failure.  The real `roles/` directory is never touched.
    """
    from acc.role_loader import RoleLoader  # noqa: PLC0415 — avoid cycle at import time

    roles_root = Path(roles_root)
    with tempfile.TemporaryDirectory(prefix="acc-role-validate-") as tmp:
        tmp_root = Path(tmp)
        base_src = roles_root / "_base" / "role.yaml"
        if base_src.is_file():
            (tmp_root / "_base").mkdir(parents=True)
            (tmp_root / "_base" / "role.yaml").write_bytes(base_src.read_bytes())
        (tmp_root / role_name).mkdir(parents=True)
        (tmp_root / role_name / "role.yaml").write_text(yaml_text, encoding="utf-8")

        try:
            loader = RoleLoader(tmp_root, role_name)
            role_def = loader.load()
        except Exception as exc:  # noqa: BLE001 — surface every failure mode
            errors = []
            getter = getattr(exc, "errors", None)
            if callable(getter):
                try:
                    errors = list(getter())
                except Exception:  # noqa: BLE001
                    errors = []
            raise RoleValidationError(str(exc), errors=errors) from exc

        if role_def is None:
            raise RoleValidationError(
                f"role.yaml for {role_name!r} parsed but yielded no "
                f"RoleDefinitionConfig (missing required fields?)",
                errors=[],
            )


def upsert_role_yaml(
    path: Path | str,
    yaml_text: str,
    *,
    role_name: str | None = None,
    roles_root: Path | str | None = None,
    validate: bool = True,
) -> None:
    """Atomically write *yaml_text* to a `roles/<role>/role.yaml`.

    Args:
        path: the target ``roles/<role>/role.yaml``.
        yaml_text: full file contents.
        role_name: defaults to ``path.parent.name``.
        roles_root: defaults to ``path.parent.parent`` (the `roles/`
            tree).  Used to find `_base/role.yaml` for the merge during
            validation.
        validate: when True (default), parse + Pydantic-validate via
            :class:`RoleLoader` before writing.  An invalid candidate
            raises :class:`RoleValidationError` and leaves the file
            untouched.
    """
    path = Path(path)
    if role_name is None:
        role_name = path.parent.name
    if roles_root is None:
        roles_root = path.parent.parent

    if validate:
        validate_role_yaml(yaml_text, role_name, roles_root)

    atomic_write_text(path, yaml_text, mode=0o644,
                       tmp_prefix=".role.yaml.tmp.")


def upsert_role_md(path: Path | str, md_text: str) -> None:
    """Atomically write *md_text* to a `roles/<role>/role.md`.

    No schema validation — role.md is free-form operator-facing prose.
    """
    atomic_write_text(path, md_text, mode=0o644,
                       tmp_prefix=".role.md.tmp.")
