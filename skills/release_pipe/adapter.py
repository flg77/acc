"""release_pipe — plan the release pipeline for a new role / skill / pack.

The Assistant's *"ensure new role releases are piped accordingly"* capability
(operator goal 2026-06-22).  Pure PLANNER (LOW risk, no side effects): given an
artifact (kind + name [+ pack/version]) it returns the ordered, ACC-correct
release steps — reviewer gate → build → sign → publish → catalog → promote — each
tagged with who runs it (agent / reviewer / operator) and whether it is
oversight-gated.  It deliberately does NOT execute publish/push/sign: those are
operator-only + cosign-gated by ACC policy.  The Assistant uses the plan to
drive the safe steps and hand the gated ones to the operator.

Two artifact homes:
  * pack-shipped (movable roles + their skills) → @acc/<pack> in acc-ecosystem
    (spearhead → mirror), built by tools/build_family_pkg.py.
  * in-tree (the 7 control roles + core skills) → ship inside the runtime image;
    "release" = commit → tests → acc-promote → image rebuild → CatalogSource bump.
"""
from __future__ import annotations

from typing import Any

from acc.skills import Skill


def _pack_plan(name: str, pack: str, version: str, kind: str) -> list[dict]:
    return [
        {"n": 1, "title": f"Reviewer optimisation of {kind} '{name}'",
         "who": "reviewer", "gate": True,
         "action": "PROPOSE_ROUTE:reviewer — have the reviewer critique the draft "
                   "before any file is written; iterate until PASS."},
        {"n": 2, "title": "Write the reviewed files",
         "who": "agent", "gate": False,
         "command": f"# skill_author/role_author mode=write → {pack}/{'roles' if kind=='role' else 'skills'}/{name}/"},
        {"n": 3, "title": f"Build the family pack {pack}",
         "who": "agent", "gate": False,
         "command": f"python tools/build_family_pkg.py {pack}   # → dist/{pack.replace('@acc/','acc-')}-{version}.accpkg"},
        {"n": 4, "title": "Cosign keyless sign + Enterprise-Contract verify",
         "who": "operator", "gate": True,
         "command": f"acc-pkg verify dist/...accpkg   # signing is OIDC/keyless — operator identity, not the agent"},
        {"n": 5, "title": "Publish to the catalog (GitHub Pages / quay)",
         "who": "operator", "gate": True,
         "command": "acc-pkg publish ...   # USER-ONLY: private-image / catalog push is operator-run"},
        {"n": 6, "title": "Update the catalog index + bump version",
         "who": "agent", "gate": False,
         "command": f"# add {pack}@{version} to the acc-ecosystem catalog index; build-all.sh"},
        {"n": 7, "title": "Promote spearhead → mirror",
         "who": "operator", "gate": True,
         "command": "acc-promote   # one-directional, gated on tests-green + reasoning-bench baseline"},
        {"n": 8, "title": "Consume the new release",
         "who": "agent", "gate": False,
         "command": f"./acc-deploy.sh pkg add {pack}@{version}   # then infuse/spawn the role"},
    ]


def _intree_plan(name: str, version: str, kind: str) -> list[dict]:
    return [
        {"n": 1, "title": f"Reviewer optimisation of {kind} '{name}'",
         "who": "reviewer", "gate": True,
         "action": "PROPOSE_ROUTE:reviewer — critique before writing files."},
        {"n": 2, "title": "Write + bump version", "who": "agent", "gate": False,
         "command": f"# edit roles/{name}/role.yaml (or skills/{name}/) ; bump version to {version}"},
        {"n": 3, "title": "Tests green", "who": "agent", "gate": False,
         "command": "pytest tests/ --ignore=tests/container -q"},
        {"n": 4, "title": "Commit to spearhead (PR)", "who": "operator", "gate": True,
         "command": "git commit + open PR to acc-spearhead main"},
        {"n": 5, "title": "Promote spearhead → mirror", "who": "operator", "gate": True,
         "command": "acc-promote"},
        {"n": 6, "title": "Rebuild runtime image + bump CatalogSource", "who": "operator", "gate": True,
         "command": "build → quay.io/flg77/acc_images ; bump CatalogSource acc-catalog image"},
    ]


class ReleasePipeSkill(Skill):
    async def invoke(self, args: dict[str, Any]) -> dict[str, Any]:
        kind = args.get("kind", "role")
        if kind not in ("role", "skill", "pack"):
            raise ValueError("release_pipe: kind must be role|skill|pack")
        name = args.get("name", "")
        if not name:
            raise ValueError("release_pipe: 'name' is required")
        version = args.get("version", "0.1.0")
        pack = args.get("pack")
        in_tree = bool(args.get("in_tree", not pack))
        if in_tree:
            steps = _intree_plan(name, version, kind)
            home = "in-tree (ships in the runtime image)"
        else:
            steps = _pack_plan(name, pack or "@acc/<pack>", version, kind)
            home = f"pack {pack or '@acc/<pack>'} (acc-ecosystem)"
        gated = [s["n"] for s in steps if s.get("gate")]
        return {
            "artifact": {"kind": kind, "name": name, "version": version, "home": home},
            "steps": steps,
            "operator_gated_steps": gated,
            "summary": f"{len(steps)} steps to release {kind} '{name}' ({home}); "
                       f"reviewer first, operator runs sign/publish/promote (steps {gated}).",
        }
