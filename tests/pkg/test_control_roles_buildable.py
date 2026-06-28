"""Regression guard: the ``@acc/control-roles`` governance pack stays buildable.

Every skill/MCP referenced by an in-tree CONTROL role must be *classified* —
either ``core_baseline`` (``CORE_BASELINE_SKILLS``/``CORE_BASELINE_MCPS``) or
listed in ``tools/skill_mcp_tiers.yaml``. When the assistant gains a capability
(e.g. a new integration skill) that nobody classifies, ``build_family_pkg.py
--manifest packaging/control-roles.yaml`` fails — and so does the flavour
governance bake (``Containerfile.flavour`` → ``acc-pkg install @acc/control-roles``).

This caught the v0.5.15 gap: the assistant's catalog_query / python_exec /
role+skill authoring / release_pipe + the A/B/C integration skills/mcps had
accrued without being added to the baseline. See 043 §11.
"""

from __future__ import annotations

import glob
import os

import yaml

from acc.pkg.manifest import CORE_BASELINE_MCPS, CORE_BASELINE_SKILLS

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tiers() -> tuple[set[str], set[str]]:
    path = os.path.join(_ROOT, "tools", "skill_mcp_tiers.yaml")
    data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    skills = {e["name"] for e in (data.get("skills") or [])}
    mcps = {e["name"] for e in (data.get("mcps") or [])}
    return skills, mcps


def test_control_role_caps_are_classified():
    tier_s, tier_m = _tiers()
    known_s = set(CORE_BASELINE_SKILLS) | tier_s
    known_m = set(CORE_BASELINE_MCPS) | tier_m

    unclassified: dict[str, dict[str, list[str]]] = {}
    for f in sorted(glob.glob(os.path.join(_ROOT, "roles", "*", "role.yaml"))):
        rd = (yaml.safe_load(open(f, encoding="utf-8")) or {}).get("role_definition", {}) or {}
        role = os.path.basename(os.path.dirname(f))
        miss_s = (set(rd.get("allowed_skills") or []) | set(rd.get("default_skills") or [])) - known_s
        miss_m = (set(rd.get("allowed_mcps") or []) | set(rd.get("default_mcps") or [])) - known_m
        if miss_s or miss_m:
            unclassified[role] = {"skills": sorted(miss_s), "mcps": sorted(miss_m)}

    assert not unclassified, (
        "control-role skills/mcps are not classified — add them to "
        "CORE_BASELINE_SKILLS/MCPS (acc/pkg/manifest.py) or "
        f"tools/skill_mcp_tiers.yaml, else the governance pack won't build: {unclassified}"
    )
