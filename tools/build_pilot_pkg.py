#!/usr/bin/env python3
"""In-place pilot ``.accpkg`` builder — Stage 0 slice 9.

Per the operator's "roles stay in this repo for Stages 0+1"
constraint, this script builds a ``.accpkg`` from a role's source
directory **without moving any files**:

1. Read ``roles/<role>/role.yaml`` to enumerate ``allowed_skills``
   and ``allowed_mcps``.
2. Read ``tools/skill_mcp_tiers.yaml`` to classify each ref.
3. Assemble an ephemeral build tree at ``build/pilot/acc-<role>/``:
   * ``roles/<role>/role.yaml`` (copied from source)
   * ``skills/<name>/`` for every skill whose tier is NOT
     ``core_baseline`` (copied from ``skills/<name>/``)
   * ``mcps/<name>/`` for every MCP whose tier is NOT
     ``core_baseline``
   * ``accpkg.yaml`` synthesised from the role's refs + tier classes
4. Invoke ``acc.pkg.build.build()`` → ``dist/acc-<role>-<version>.accpkg``

Source files in ``roles/``, ``skills/``, and ``mcps/`` are NEVER
mutated.  Determinism: rerunning the script for the same role
produces a byte-identical ``.accpkg`` (the underlying builder is
deterministic and we copy file content unchanged).

Usage::

    python tools/build_pilot_pkg.py coding_agent
    python tools/build_pilot_pkg.py coding_agent --version 0.2.0
    python tools/build_pilot_pkg.py coding_agent --output /tmp/x.accpkg

Refuses if a referenced skill/MCP is not classified in the tiers
YAML (exit 2 — schema failure shape, since the tier YAML IS the
tier schema).

Exit codes
----------

* 0 ok
* 1 user error (missing role, missing source dir, bad arg)
* 2 unclassified skill/MCP (regenerate ``tools/skill_mcp_tiers.yaml``)
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Allow direct invocation: prepend repo root so `from acc.pkg ...` works.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from acc.pkg.build import build  # noqa: E402
from acc.pkg.manifest import (  # noqa: E402
    CORE_BASELINE_MCPS,
    CORE_BASELINE_SKILLS,
)

logger = logging.getLogger("acc.tools.build_pilot_pkg")


# ---------------------------------------------------------------------------
# Tiers YAML
# ---------------------------------------------------------------------------


def _load_tiers(tiers_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(skill_tier, mcp_tier)`` lookup maps from the YAML."""
    data = yaml.safe_load(tiers_path.read_text(encoding="utf-8")) or {}
    skill_tier = {s["name"]: s["tier"] for s in data.get("skills", [])}
    mcp_tier = {m["name"]: m["tier"] for m in data.get("mcps", [])}
    # Augment with baseline knowledge (in case the YAML drifts behind
    # acc.pkg.manifest's constants).
    for s in CORE_BASELINE_SKILLS:
        skill_tier.setdefault(s, "core_baseline")
    for m in CORE_BASELINE_MCPS:
        mcp_tier.setdefault(m, "core_baseline")
    return skill_tier, mcp_tier


# ---------------------------------------------------------------------------
# Role refs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoleRefs:
    role_name: str
    skills: list[str]
    mcps: list[str]


def _load_role(role_yaml: Path) -> RoleRefs:
    data = yaml.safe_load(role_yaml.read_text(encoding="utf-8")) or {}
    rd = data.get("role_definition", {})
    skills = sorted(set(
        list(rd.get("allowed_skills") or [])
        + list(rd.get("default_skills") or [])
    ))
    mcps = sorted(set(
        list(rd.get("allowed_mcps") or [])
        + list(rd.get("default_mcps") or [])
    ))
    return RoleRefs(role_name=role_yaml.parent.name, skills=skills, mcps=mcps)


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------


def _classify_or_die(
    name: str, tiers: dict[str, str], kind: str
) -> str:
    tier = tiers.get(name)
    if tier is None:
        raise SystemExit(
            f"error: {kind} {name!r} is not classified in "
            "tools/skill_mcp_tiers.yaml — regenerate via "
            "`python tools/classify_skills_mcps.py`"
        )
    return tier


def _copy_tree(src: Path, dst: Path) -> None:
    # Use shutil.copytree but ignore __pycache__ + .pyc — they're build
    # artefacts and would break determinism.
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _assemble_build_tree(
    role: RoleRefs,
    *,
    repo_root: Path,
    build_tree: Path,
    pkg_name: str,
    pkg_version: str,
    skill_tiers: dict[str, str],
    mcp_tiers: dict[str, str],
) -> None:
    if build_tree.exists():
        shutil.rmtree(build_tree)
    build_tree.mkdir(parents=True)

    # role.yaml
    role_src = repo_root / "roles" / role.role_name / "role.yaml"
    role_dst = build_tree / "roles" / role.role_name / "role.yaml"
    role_dst.parent.mkdir(parents=True)
    role_dst.write_bytes(role_src.read_bytes())

    # Manifest accumulators
    skills_manifest: list[dict] = []
    mcps_manifest: list[dict] = []

    # Skills
    for s in role.skills:
        tier = _classify_or_die(s, skill_tiers, "skill")
        if tier == "core_baseline":
            continue
        src_dir = repo_root / "skills" / s
        if not src_dir.is_dir():
            raise SystemExit(
                f"error: skill source dir missing: {src_dir}"
            )
        dst_dir = build_tree / "skills" / s
        _copy_tree(src_dir, dst_dir)
        # Stage-0 simplification: single-role pilot always bundles
        # locally even when the tiers YAML classifies as own_pack
        # (the shared-pack extraction is a Stage 2 concern).
        skills_manifest.append({
            "name": s,
            "tier": "bundle_in_role",
            "path": f"skills/{s}/",
        })

    # MCPs
    for m in role.mcps:
        tier = _classify_or_die(m, mcp_tiers, "mcp")
        if tier == "core_baseline":
            continue
        src_dir = repo_root / "mcps" / m
        if not src_dir.is_dir():
            raise SystemExit(
                f"error: mcp source dir missing: {src_dir}"
            )
        dst_dir = build_tree / "mcps" / m
        _copy_tree(src_dir, dst_dir)
        mcps_manifest.append({
            "name": m,
            "tier": "bundle_in_role",
            "path": f"mcps/{m}/",
        })

    # Manifest
    manifest = {
        "schema_version": 1,
        "name": pkg_name,
        "version": pkg_version,
        "description": f"Stage-0 pilot pack for the {role.role_name} role.",
        "depends_on": [],
        "roles": [{
            "name": role.role_name,
            "path": f"roles/{role.role_name}/role.yaml",
        }],
        "skills": skills_manifest,
        "mcps": mcps_manifest,
    }
    (build_tree / "accpkg.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_pilot(
    role_name: str,
    *,
    version: str = "0.1.0",
    repo_root: Path | None = None,
    output: Path | None = None,
    tiers_path: Path | None = None,
) -> Path:
    """Build a Stage-0 pilot pack for ``role_name`` and return the
    output ``.accpkg`` path.
    """
    repo_root = (repo_root or _REPO_ROOT).resolve()
    tiers_path = tiers_path or (repo_root / "tools" / "skill_mcp_tiers.yaml")
    role_yaml = repo_root / "roles" / role_name / "role.yaml"
    if not role_yaml.is_file():
        raise SystemExit(f"error: role not found: {role_yaml}")
    if not tiers_path.is_file():
        raise SystemExit(
            f"error: tiers YAML not found at {tiers_path} — "
            "regenerate via `python tools/classify_skills_mcps.py`"
        )

    role = _load_role(role_yaml)
    skill_tiers, mcp_tiers = _load_tiers(tiers_path)

    pkg_name = f"@acc/{role_name.replace('_', '-')}"
    build_tree = repo_root / "build" / "pilot" / f"acc-{role_name}"
    output = output or (
        repo_root / "dist"
        / f"acc-{role_name.replace('_', '-')}-{version}.accpkg"
    )

    _assemble_build_tree(
        role,
        repo_root=repo_root,
        build_tree=build_tree,
        pkg_name=pkg_name,
        pkg_version=version,
        skill_tiers=skill_tiers,
        mcp_tiers=mcp_tiers,
    )

    result = build(build_tree, output)
    logger.info(
        "pilot built: %s (content_sha256=%s)",
        result.output_path, result.content_sha256[:12],
    )
    return result.output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", help="role name (matches roles/<role>/)")
    parser.add_argument("--version", default="0.1.0", help="package version (default 0.1.0)")
    parser.add_argument("--output", type=Path, default=None,
                        help="output .accpkg path (default: dist/acc-<role>-<version>.accpkg)")
    parser.add_argument("--repo-root", type=Path, default=None,
                        help="override repo root (default: parent of tools/)")
    parser.add_argument("--tiers", type=Path, default=None,
                        help="override tier YAML path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        out = build_pilot(
            args.role,
            version=args.version,
            repo_root=args.repo_root,
            output=args.output,
            tiers_path=args.tiers,
        )
    except SystemExit as exc:
        # Re-raise SystemExits with their original message/code so the
        # caller sees the same behaviour as a direct script invocation.
        if isinstance(exc.code, int):
            return exc.code
        print(exc, file=sys.stderr)
        return 2
    print(out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
