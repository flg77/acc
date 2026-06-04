#!/usr/bin/env python3
"""Build a family `.accpkg` from multiple in-tree roles — Stage 2.2.

The Stage 2 family extractions ship four packages:

    @acc/workspace-roles  — coding_agent family + analyst + synthesizer
    @acc/research-roles   — research_* family
    @acc/business-roles   — 30 business roles
    @acc/devops-roles     — data/devops/ml/security engineers

Each is a single .accpkg carrying multiple roles in its ``roles/``
directory.  This tool composes the build:

    1. Read a family manifest (defaults baked in; YAML override
       supported via ``--manifest``).
    2. For each role in the family, copy its tree + bundled
       skills/MCPs per ``tools/skill_mcp_tiers.yaml``.
    3. Synthesise an ``accpkg.yaml`` listing all roles.
    4. Invoke ``acc.pkg.build.build`` for the deterministic tarball.

The output is a regular ``.accpkg`` — ``acc-pkg verify``,
``install``, the catalog resolver, etc. all see it as one package
with N roles instead of one role.

Usage::

    python tools/build_family_pkg.py workspace
    python tools/build_family_pkg.py research --version 1.1.0
    python tools/build_family_pkg.py business -o /tmp/out/
    python tools/build_family_pkg.py --manifest custom-family.yaml
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

# Repo-relative imports
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from acc.pkg.build import build  # noqa: E402
from acc.pkg.manifest import (  # noqa: E402
    CORE_BASELINE_MCPS,
    CORE_BASELINE_SKILLS,
)

logger = logging.getLogger("acc.tools.build_family_pkg")


# ---------------------------------------------------------------------------
# Default family manifests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FamilyManifest:
    name: str           # @acc/<family>-roles
    roles: tuple[str, ...]
    description: str


DEFAULT_FAMILIES: dict[str, FamilyManifest] = {
    "workspace": FamilyManifest(
        name="@acc/workspace-roles",
        roles=(
            "coding_agent",
            "coding_agent_architect",
            "coding_agent_dependency",
            "coding_agent_implementer",
            "coding_agent_reviewer",
            "coding_agent_tester",
            "analyst",
            "synthesizer",
        ),
        description="Coding family + analyst + synthesizer.",
    ),
    "research": FamilyManifest(
        name="@acc/research-roles",
        roles=(
            "research_competitor",
            "research_critic",
            "research_economist",
            "research_planner",
            "research_strategist",
            "research_synthesizer",
        ),
        description="Multi-agent research collective.",
    ),
    "business": FamilyManifest(
        name="@acc/business-roles",
        roles=(
            "account_executive",
            "business_analyst",
            "content_marketer",
            "contract_analyst",
            "customer_success_manager",
            "customer_support_agent",
            "demand_generation_specialist",
            "financial_analyst",
            "fpa_analyst",
            "hr_business_partner",
            "it_operations_specialist",
            "it_support_specialist",
            "learning_development_specialist",
            "marketing_analyst",
            "operations_analyst",
            "procurement_specialist",
            "product_manager",
            "product_marketer",
            "project_manager",
            "recruiter",
            "revenue_operations_analyst",
            "risk_compliance_analyst",
            "sales_development_rep",
            "sales_engineer",
            "technical_support_specialist",
        ),
        description="Business / GTM / ops / HR / finance.",
    ),
    "devops": FamilyManifest(
        name="@acc/devops-roles",
        roles=(
            "data_engineer",
            "devops_engineer",
            "ml_engineer",
            "security_analyst",
        ),
        description="Engineering family with shell_exec workflows.",
    ),
}


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------


def _load_tiers(tiers_path: Path) -> tuple[dict[str, str], dict[str, str]]:
    data = yaml.safe_load(tiers_path.read_text(encoding="utf-8")) or {}
    skills = {s["name"]: s["tier"] for s in data.get("skills") or []}
    mcps = {m["name"]: m["tier"] for m in data.get("mcps") or []}
    # Augment with baseline (matches build_pilot_pkg semantics).
    for s in CORE_BASELINE_SKILLS:
        skills.setdefault(s, "core_baseline")
    for m in CORE_BASELINE_MCPS:
        mcps.setdefault(m, "core_baseline")
    return skills, mcps


# ---------------------------------------------------------------------------
# Per-role refs scan
# ---------------------------------------------------------------------------


def _role_refs(role_yaml: Path) -> tuple[list[str], list[str]]:
    raw = yaml.safe_load(role_yaml.read_text(encoding="utf-8")) or {}
    rd = raw.get("role_definition", {}) or {}
    skills = sorted(set(
        list(rd.get("allowed_skills") or [])
        + list(rd.get("default_skills") or [])
    ))
    mcps = sorted(set(
        list(rd.get("allowed_mcps") or [])
        + list(rd.get("default_mcps") or [])
    ))
    return skills, mcps


# ---------------------------------------------------------------------------
# Tree assembly
# ---------------------------------------------------------------------------


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _assemble(
    family: FamilyManifest,
    *,
    repo_root: Path,
    build_tree: Path,
    version: str,
    skill_tiers: dict[str, str],
    mcp_tiers: dict[str, str],
) -> None:
    if build_tree.exists():
        shutil.rmtree(build_tree)
    build_tree.mkdir(parents=True)

    roles_manifest: list[dict] = []
    skills_seen: set[str] = set()
    mcps_seen: set[str] = set()
    skills_manifest: list[dict] = []
    mcps_manifest: list[dict] = []

    for role_name in family.roles:
        # Role file
        role_src = repo_root / "roles" / role_name / "role.yaml"
        if not role_src.is_file():
            raise SystemExit(
                f"error: role {role_name!r} not found at {role_src} — "
                "family manifest is out of sync with the roles/ tree"
            )
        role_dst = build_tree / "roles" / role_name / "role.yaml"
        role_dst.parent.mkdir(parents=True)
        role_dst.write_bytes(role_src.read_bytes())
        roles_manifest.append({
            "name": role_name,
            "path": f"roles/{role_name}/role.yaml",
        })

        # Skills + MCPs — dedupe across the family
        skills, mcps = _role_refs(role_src)
        for s in skills:
            if s in skills_seen:
                continue
            tier = skill_tiers.get(s)
            if tier is None:
                raise SystemExit(
                    f"error: skill {s!r} (used by {role_name}) is not "
                    "classified in tools/skill_mcp_tiers.yaml — "
                    "regenerate via tools/classify_skills_mcps.py"
                )
            if tier == "core_baseline":
                continue
            src_dir = repo_root / "skills" / s
            if not src_dir.is_dir():
                raise SystemExit(
                    f"error: skill source dir missing: {src_dir}"
                )
            _copy_tree(src_dir, build_tree / "skills" / s)
            skills_manifest.append({
                "name": s,
                "tier": "bundle_in_role",
                "path": f"skills/{s}/",
            })
            skills_seen.add(s)

        for m in mcps:
            if m in mcps_seen:
                continue
            tier = mcp_tiers.get(m)
            if tier is None:
                raise SystemExit(
                    f"error: mcp {m!r} (used by {role_name}) is not "
                    "classified in tools/skill_mcp_tiers.yaml"
                )
            if tier == "core_baseline":
                continue
            src_dir = repo_root / "mcps" / m
            if not src_dir.is_dir():
                raise SystemExit(
                    f"error: mcp source dir missing: {src_dir}"
                )
            _copy_tree(src_dir, build_tree / "mcps" / m)
            mcps_manifest.append({
                "name": m,
                "tier": "bundle_in_role",
                "path": f"mcps/{m}/",
            })
            mcps_seen.add(m)

    manifest = {
        "schema_version": 1,
        "name": family.name,
        "version": version,
        "description": family.description,
        "depends_on": [],
        "roles": roles_manifest,
        "skills": skills_manifest,
        "mcps": mcps_manifest,
    }
    (build_tree / "accpkg.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_family(
    family_key: str,
    *,
    version: str = "1.0.0",
    repo_root: Path | None = None,
    output: Path | None = None,
    tiers_path: Path | None = None,
    manifest_override: FamilyManifest | None = None,
) -> Path:
    """Build a family `.accpkg` and return the output path."""
    repo_root = (repo_root or _REPO_ROOT).resolve()
    tiers_path = tiers_path or (
        repo_root / "tools" / "skill_mcp_tiers.yaml"
    )

    family = manifest_override or DEFAULT_FAMILIES.get(family_key)
    if family is None:
        raise SystemExit(
            f"error: unknown family {family_key!r} — known: "
            f"{', '.join(sorted(DEFAULT_FAMILIES))}"
        )
    if not tiers_path.is_file():
        raise SystemExit(f"error: tiers YAML not found at {tiers_path}")

    skill_tiers, mcp_tiers = _load_tiers(tiers_path)

    safe_name = family.name.replace("@", "").replace("/", "-")
    build_tree = repo_root / "build" / "family" / safe_name
    if output is None:
        output = repo_root / "dist" / f"{safe_name}-{version}.accpkg"

    _assemble(
        family,
        repo_root=repo_root,
        build_tree=build_tree,
        version=version,
        skill_tiers=skill_tiers,
        mcp_tiers=mcp_tiers,
    )

    result = build(build_tree, output)
    logger.info(
        "family built: %s (%d roles, %d skills, %d mcps, sha256=%s)",
        family.name, len(family.roles),
        sum(1 for _ in (build_tree / "skills").iterdir())
            if (build_tree / "skills").is_dir() else 0,
        sum(1 for _ in (build_tree / "mcps").iterdir())
            if (build_tree / "mcps").is_dir() else 0,
        result.content_sha256[:12],
    )
    return result.output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "family",
        nargs="?",
        choices=sorted(DEFAULT_FAMILIES),
        help="Family key (workspace / research / business / devops). "
             "Omit when supplying --manifest.",
    )
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--repo-root", type=Path, default=None)
    parser.add_argument("--tiers", type=Path, default=None)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="YAML override: {name, description, roles: [...]}",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    override = None
    if args.manifest:
        if args.family:
            print(
                "error: --manifest and a positional family are mutually exclusive",
                file=sys.stderr,
            )
            return 1
        data = yaml.safe_load(args.manifest.read_text(encoding="utf-8")) or {}
        override = FamilyManifest(
            name=str(data["name"]),
            roles=tuple(data["roles"]),
            description=str(data.get("description", "")),
        )
        family_key = override.name
    else:
        if not args.family:
            parser.error("specify a family (workspace/research/business/devops) "
                         "or pass --manifest")
        family_key = args.family

    try:
        out = build_family(
            family_key,
            version=args.version,
            repo_root=args.repo_root,
            output=args.output,
            tiers_path=args.tiers,
            manifest_override=override,
        )
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        print(exc, file=sys.stderr)
        return 2
    print(out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
