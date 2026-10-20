"""The index of what this runtime serves: roles, skills and MCP servers.

OpenSpec ``20261004-operator-release-split`` OR-14, decision D2 (D-033).
The agent image is the source of the roles, skills and MCP trees; the
operator mounts nothing and embeds nothing.  What it needs instead is a
way to ask an image what it serves, before any agent runs: the roles its
admission webhook validates an ``AgentCollective`` against, and the
runtime version it may pair with.  This module is that answer.

* ``python3 -m acc.roles_index`` prints the index as JSON.  It resolves the
  trees the way the agent does (``ACC_ROLES_ROOT`` / ``ACC_SKILLS_ROOT`` /
  ``ACC_MCPS_ROOT``, else the host layout of ``acc.paths``), and adds the
  roles the installed packages under ``ACC_PACKAGES_ROOT`` serve, so the
  same command describes a bare image and a running agent.
* The image builds bake the same output at ``$ACC_ROLES_INDEX``
  (``/app/roles.index`` on the source-built image, ``/usr/share/acc/roles.index``
  on the RPM image), so ``podman run --rm <image> cat "$ACC_ROLES_INDEX"``
  answers without starting Python, and a tree that does not parse fails
  the image build rather than the first agent.

The schema is deliberately small and versioned (``schema``); a consumer
that sees a higher ``schema`` than it knows must treat the index as
unreadable rather than guess.  Names are what ``AgentCollective``
``spec.agents[].role`` carries; ``control`` marks the substrate roles the
runtime reserves (``acc.pkg.role_resolution.CONTROL_ROLES``); ``source`` is
``in-tree`` or ``<package>@<version>``, the pack winning as it does in the
RoleLoader.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from acc import __version__ as _runtime_version
from acc.mcp.registry import list_mcp_server_ids
from acc.pkg.role_resolution import CONTROL_ROLES, list_installed_roles
from acc.role_loader import list_roles
from acc.skills.loader import list_skills

logger = logging.getLogger("acc.roles_index")

#: Bumped when a field changes meaning or goes away; adding a field does not bump it.
SCHEMA = 1

#: Where the image builds write the index; the env var the images set.
INDEX_ENV = "ACC_ROLES_INDEX"

_ROOT_ENV = {"roles": "ACC_ROLES_ROOT", "skills": "ACC_SKILLS_ROOT", "mcps": "ACC_MCPS_ROOT"}
_FALLBACK_VERSION = "0.1.0"  # RoleDefinitionConfig.version's default


def tree_root(kind: str) -> Path:
    """The tree the agent would use for *kind*: the env var as given, else
    the host layout (`20260909-acc-install`), else the cwd-relative name."""
    explicit = os.environ.get(_ROOT_ENV[kind], "").strip()
    if explicit:
        return Path(explicit)
    from acc import paths as _paths  # noqa: PLC0415

    return Path(_paths.path_of(kind))


def _role_version(role_yaml: Path, base_version: str | None) -> str:
    """``role_definition.version`` of the role, else the ``_base`` one, else
    the config default -- the same precedence the RoleLoader's merge gives."""
    try:
        data = yaml.safe_load(role_yaml.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 -- an unreadable role is reported, not fatal
        logger.warning("roles_index: cannot read %s", role_yaml, exc_info=True)
        data = {}
    definition = data.get("role_definition") if isinstance(data, dict) else None
    version = definition.get("version") if isinstance(definition, dict) else None
    if version is None or str(version).strip() == "":
        return base_version or _FALLBACK_VERSION
    return str(version)


def _base_version(roles_root: Path) -> str | None:
    base = roles_root / "_base" / "role.yaml"
    if not base.is_file():
        return None
    try:
        data = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None
    definition = data.get("role_definition") if isinstance(data, dict) else None
    version = definition.get("version") if isinstance(definition, dict) else None
    return None if version is None else str(version)


def build_index(
    *,
    roles_root: str | Path | None = None,
    skills_root: str | Path | None = None,
    mcps_root: str | Path | None = None,
    with_packages: bool = True,
) -> dict[str, Any]:
    """Describe what this runtime serves.

    Roots default to what the agent resolves (:func:`tree_root`).  With
    *with_packages*, roles served by installed packages are added and a
    pack's copy of a role wins over the in-tree one, as in the RoleLoader;
    a missing or unreadable package registry adds nothing.
    """
    roles_dir = Path(roles_root) if roles_root is not None else tree_root("roles")
    skills_dir = Path(skills_root) if skills_root is not None else tree_root("skills")
    mcps_dir = Path(mcps_root) if mcps_root is not None else tree_root("mcps")

    base_version = _base_version(roles_dir)
    roles: dict[str, dict[str, Any]] = {}
    for name in list_roles(roles_dir):
        roles[name] = {
            "name": name,
            "version": _role_version(roles_dir / name / "role.yaml", base_version),
            "control": name in CONTROL_ROLES,
            "source": "in-tree",
        }

    if with_packages:
        try:
            installed = list_installed_roles()
        except Exception:  # noqa: BLE001 -- the index must not fail on a registry
            logger.debug("roles_index: installed-role scan failed", exc_info=True)
            installed = {}
        for name, resolved in installed.items():
            pkg = resolved.package
            roles[name] = {
                "name": name,
                "version": _role_version(Path(resolved.role_yaml_path), base_version),
                "control": name in CONTROL_ROLES,
                "source": f"{pkg.name}@{pkg.version}",
            }

    return {
        "schema": SCHEMA,
        "runtime": _runtime_version,
        "roots": {"roles": str(roles_dir), "skills": str(skills_dir), "mcps": str(mcps_dir)},
        "roles": [roles[name] for name in sorted(roles)],
        "skills": list_skills(skills_dir),
        "mcps": list_mcp_server_ids(mcps_dir),
    }


def render(index: dict[str, Any]) -> str:
    """The index as the one JSON text every consumer parses: sorted keys,
    two-space indent, a trailing newline."""
    return json.dumps(index, indent=2, sort_keys=True) + "\n"


def role_names(index: dict[str, Any]) -> list[str]:
    """The role names an index lists, sorted."""
    return sorted(str(r["name"]) for r in index.get("roles", []))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m acc.roles_index",
        description="Print what this ACC runtime serves: roles, skills and MCP servers.",
    )
    parser.add_argument("--out", metavar="PATH", help="write here instead of stdout")
    parser.add_argument("--roles-root", metavar="DIR")
    parser.add_argument("--skills-root", metavar="DIR")
    parser.add_argument("--mcps-root", metavar="DIR")
    parser.add_argument(
        "--no-packages",
        action="store_true",
        help="list the trees only; ignore roles from installed packages",
    )
    args = parser.parse_args(argv)

    index = build_index(
        roles_root=args.roles_root,
        skills_root=args.skills_root,
        mcps_root=args.mcps_root,
        with_packages=not args.no_packages,
    )
    if not index["roles"]:
        print(
            f"roles_index: no roles under {index['roots']['roles']} -- is ACC_ROLES_ROOT set?",
            file=sys.stderr,
        )
        return 1
    text = render(index)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
