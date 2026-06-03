#!/usr/bin/env python3
"""Classify every skill + MCP referenced by movable roles into one of
three tiers (brainstorm Q3a).

Tiers
-----

* ``core_baseline`` — ships with ACC core; never packaged.  Source of
  truth: ``acc.pkg.manifest.CORE_BASELINE_SKILLS`` /
  ``CORE_BASELINE_MCPS`` (the v0.3.50 stdlib set).
* ``bundle_in_role`` — used by exactly one movable role; travels
  inside that role's package.
* ``own_pack`` — used by two or more movable roles (and not baseline);
  belongs in its own ``@acc/skills-<topic>`` or ``@acc/mcp-<topic>``
  package.

CONTROL roles (the 7 that stay in core: ``arbiter``, ``assistant``,
``compliance_officer``, ``ingester``, ``observer``, ``orchestrator``,
``reviewer``) are scanned to seed the baseline check but their
references **do not** drive the classification.  We classify what
movable roles need.

Output
------

YAML written to ``tools/skill_mcp_tiers.yaml`` by default; configurable
via ``--output``.  Order is deterministic (alphabetic by name).  The
operator reviews and may hand-edit the YAML to merge own-packs or
reassign bundling — the script is the seed, not the source of truth.

Usage
-----

::

    python tools/classify_skills_mcps.py
    python tools/classify_skills_mcps.py --roles-dir /path/to/roles \\
        --output /tmp/tiers.yaml

Exit codes: 0 ok, 1 IO error, 2 malformed role.yaml.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Allow direct invocation as `python tools/classify_skills_mcps.py` by
# prepending the repo root to sys.path BEFORE importing the acc package.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml  # noqa: E402

from acc.pkg.manifest import CORE_BASELINE_MCPS, CORE_BASELINE_SKILLS  # noqa: E402

logger = logging.getLogger("acc.tools.classify_skills_mcps")

# The 7 CONTROL roles stay in core; their references seed the baseline
# check but do not drive classification.
CONTROL_ROLES: frozenset[str] = frozenset(
    {
        "arbiter",
        "assistant",
        "compliance_officer",
        "ingester",
        "observer",
        "orchestrator",
        "reviewer",
    }
)

# Directories under ``roles/`` that are NOT real roles.
NON_ROLE_DIRS: frozenset[str] = frozenset({"_base", "TEMPLATE"})


# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Classification:
    name: str
    tier: str                       # "core_baseline" | "bundle_in_role" | "own_pack"
    used_by: tuple[str, ...]        # movable roles that reference it
    suggested_pack: str | None      # for own_pack — heuristic only


# ---------------------------------------------------------------------------
# role.yaml parsing
# ---------------------------------------------------------------------------


def _load_role_refs(role_yaml: Path) -> tuple[list[str], list[str]]:
    """Return ``(skill_refs, mcp_refs)`` for one ``role.yaml``.

    Unions ``allowed_*`` and ``default_*`` lists defensively — a skill
    listed in ``default_skills`` is implicitly part of ``allowed_skills``,
    but malformed files in the wild sometimes omit one of the two.
    """
    try:
        data = yaml.safe_load(role_yaml.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"malformed YAML in {role_yaml}: {exc}") from exc

    role_def = data.get("role_definition", {})
    if not isinstance(role_def, dict):
        return [], []

    def _as_list(key: str) -> list[str]:
        val = role_def.get(key)
        if not val:
            return []
        if not isinstance(val, list):
            raise ValueError(
                f"{role_yaml}: {key!r} must be a list, got {type(val).__name__}"
            )
        return [str(x) for x in val]

    skills = sorted(set(_as_list("allowed_skills") + _as_list("default_skills")))
    mcps = sorted(set(_as_list("allowed_mcps") + _as_list("default_mcps")))
    return skills, mcps


# ---------------------------------------------------------------------------
# Walk + classify
# ---------------------------------------------------------------------------


def scan_roles(roles_dir: Path) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Walk ``roles_dir`` and return two name→roles maps for MOVABLE roles.

    The first map is ``skill_name -> list of movable role names that
    reference it``; the second is the same shape for MCPs.  CONTROL
    roles are scanned but excluded from the map (they don't drive
    classification).
    """
    skill_usage: dict[str, list[str]] = defaultdict(list)
    mcp_usage: dict[str, list[str]] = defaultdict(list)

    for role_dir in sorted(roles_dir.iterdir()):
        if not role_dir.is_dir() or role_dir.name in NON_ROLE_DIRS:
            continue
        role_yaml = role_dir / "role.yaml"
        if not role_yaml.is_file():
            logger.debug("skipping %s — no role.yaml", role_dir.name)
            continue

        skills, mcps = _load_role_refs(role_yaml)

        if role_dir.name in CONTROL_ROLES:
            # Track for diagnostics but don't drive classification.
            continue

        for s in skills:
            if role_dir.name not in skill_usage[s]:
                skill_usage[s].append(role_dir.name)
        for m in mcps:
            if role_dir.name not in mcp_usage[m]:
                mcp_usage[m].append(role_dir.name)

    return dict(skill_usage), dict(mcp_usage)


def classify(
    usage: dict[str, list[str]],
    baseline: Iterable[str],
    suggested_pack_prefix: str,
) -> list[Classification]:
    """Convert a name→roles map into a sorted list of ``Classification``."""
    baseline_set = set(baseline)
    out: list[Classification] = []
    for name in sorted(usage):
        users = tuple(sorted(usage[name]))
        if name in baseline_set:
            tier = "core_baseline"
            suggested = None
        elif len(users) >= 2:
            tier = "own_pack"
            suggested = f"{suggested_pack_prefix}-shared"  # operator renames
        else:
            tier = "bundle_in_role"
            suggested = None
        out.append(
            Classification(
                name=name, tier=tier, used_by=users, suggested_pack=suggested
            )
        )
    return out


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------


def render_yaml(
    skills: list[Classification], mcps: list[Classification]
) -> str:
    """Produce the tiers YAML text.  Output is stable (sorted)."""
    header = [
        "# Generated by tools/classify_skills_mcps.py — DO NOT hand-edit",
        "# without re-running the classifier OR keeping a note in this header",
        "# describing the override.  Source of truth: brainstorm Q3a tier policy.",
        "#",
        "# Tiers:",
        "#   core_baseline   — ships with ACC core; never packaged.",
        "#   bundle_in_role  — used by exactly one movable role; travels in pack.",
        "#   own_pack        — used by 2+ movable roles; its own @acc/skills-* pack.",
        "",
    ]

    def _block(items: list[Classification]) -> list:
        out = []
        for c in items:
            entry: dict = {"name": c.name, "tier": c.tier}
            if c.tier != "core_baseline":
                entry["used_by"] = list(c.used_by)
            if c.suggested_pack:
                entry["suggested_pack"] = c.suggested_pack
            out.append(entry)
        return out

    doc = {
        "skills": _block(skills),
        "mcps": _block(mcps),
    }
    body = yaml.safe_dump(
        doc, sort_keys=False, default_flow_style=False, width=999
    )
    return "\n".join(header) + body


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--roles-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "roles",
        help="root of the roles tree (default: <repo>/roles)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tools" / "skill_mcp_tiers.yaml",
        help="where to write the tiers YAML (default: tools/skill_mcp_tiers.yaml)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="write to stdout instead of --output",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.roles_dir.is_dir():
        logger.error("roles dir not found: %s", args.roles_dir)
        return 1

    try:
        skill_usage, mcp_usage = scan_roles(args.roles_dir)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    skill_classifications = classify(
        skill_usage, CORE_BASELINE_SKILLS, "@acc/skills"
    )
    mcp_classifications = classify(
        mcp_usage, CORE_BASELINE_MCPS, "@acc/mcp"
    )

    output_text = render_yaml(skill_classifications, mcp_classifications)

    if args.stdout:
        sys.stdout.write(output_text)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output_text, encoding="utf-8")
        logger.info(
            "wrote %d skills + %d mcps → %s",
            len(skill_classifications),
            len(mcp_classifications),
            args.output,
        )

    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
