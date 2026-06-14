#!/usr/bin/env python3
"""check_collectives.py — validate collective presets against the rest of ACC.

The standing alignment check for ``collectives/*.yaml`` (+ the live
``collective.yaml`` at the repo root).  Drives the ``/acc-collectives`` skill.

For every preset it verifies:

  * the YAML parses and declares a ``collective_id``;
  * every agent ``role`` is a known role — a CONTROL/substrate role, an
    in-tree ``roles/<name>``, or a role served by a family pack the preset
    DECLARES in ``required_packages``;
  * every ``model:`` is a real ``models.yaml`` id;
  * every ``required_packages`` entry is well-formed (``@scope/name@constraint``)
    and resolvable (known in the pack manifest or advertised by a catalog);
  * declared packs are actually used (no dead deps);
  * ``managed_sub_collectives[*].role_templates`` reference known roles.

The role->pack knowledge lives in ``collectives/packs.yaml`` (committed, so the
check is hermetic in CI).  Refresh it from the installed/fixture/ecosystem packs
when a pack changes:

    python tools/check_collectives.py --refresh-packs

Exit code: 0 = clean, 1 = at least one ERROR (or any WARN under --strict).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a hard dep of acc
    sys.stderr.write("check_collectives: PyYAML is required\n")
    raise SystemExit(2)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PACKS_MANIFEST = _REPO_ROOT / "collectives" / "packs.yaml"
_MODELS_YAML = _REPO_ROOT / "models.yaml"
_ROLES_DIR = _REPO_ROOT / "roles"
_COLLECTIVES_DIR = _REPO_ROOT / "collectives"
_ROOT_SPEC = _REPO_ROOT / "collective.yaml"
_CATALOGS = [
    _REPO_ROOT / "examples" / "catalogs.yaml",
    _REPO_ROOT / "examples" / "catalogs.dev.yaml",
]

# A required_packages entry: @scope/name optionally @constraint.
_REQ_RE = re.compile(r"^@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*(@.+)?$")

# Substrate roles — always present, never served from a community pack.
# Mirrors acc.pkg.role_resolution.CONTROL_ROLES.
_CONTROL_ROLES = (
    "arbiter", "assistant", "compliance_officer", "ingester",
    "observer", "orchestrator", "reviewer",
)


# --------------------------------------------------------------------------- #
# Manifest + settings loaders
# --------------------------------------------------------------------------- #
def _load_manifest() -> dict:
    if not _PACKS_MANIFEST.is_file():
        return {"control_roles": [], "packs": {}}
    data = yaml.safe_load(_PACKS_MANIFEST.read_text(encoding="utf-8")) or {}
    data.setdefault("control_roles", [])
    data.setdefault("packs", {})
    return data


def _model_ids() -> set[str]:
    if not _MODELS_YAML.is_file():
        return set()
    data = yaml.safe_load(_MODELS_YAML.read_text(encoding="utf-8")) or {}
    return {m["model_id"] for m in (data.get("models") or []) if m.get("model_id")}


def _in_tree_roles() -> set[str]:
    if not _ROLES_DIR.is_dir():
        return set()
    return {
        d.name
        for d in _ROLES_DIR.iterdir()
        if d.is_dir() and (d / "role.yaml").is_file()
    }


def _catalog_pack_names() -> set[str]:
    """Pack @scope/name strings any configured catalog advertises (best effort)."""
    names: set[str] = set()
    for cat in _CATALOGS:
        if not cat.is_file():
            continue
        try:
            text = cat.read_text(encoding="utf-8")
        except OSError:
            continue
        names.update(re.findall(r"@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*", text))
    return names


# --------------------------------------------------------------------------- #
# --refresh-packs: rebuild the role->pack manifest from available sources
# --------------------------------------------------------------------------- #
def _roles_in_accpkg(path: Path) -> set[str]:
    roles: set[str] = set()
    try:
        with tarfile.open(path, "r:*") as tf:
            for name in tf.getnames():
                m = re.search(r"(?:^|/)roles/([^/]+)/role\.yaml$", name)
                if m:
                    roles.add(m.group(1))
    except (tarfile.TarError, OSError):
        pass
    return roles


def _roles_in_tree(family_dir: Path) -> set[str]:
    return {
        d.name
        for d in (family_dir / "roles").glob("*")
        if (d / "role.yaml").is_file()
    } if (family_dir / "roles").is_dir() else set()


def _refresh_packs() -> int:
    """Union-merge discovered packs into collectives/packs.yaml (additive)."""
    import os

    manifest = _load_manifest()
    if not manifest.get("control_roles"):
        manifest["control_roles"] = list(_CONTROL_ROLES)
    packs: dict[str, list[str]] = {k: list(v) for k, v in manifest["packs"].items()}

    sources: list[tuple[str, set[str]]] = []

    # 1. Committed fixture packs (hermetic, always in-repo).
    for pkg in (_REPO_ROOT / "tests" / "fixtures" / "packs").glob("*.accpkg"):
        # acc-workspace-roles-1.0.0.accpkg -> @acc/workspace-roles
        m = re.match(r"acc-(.+?)-\d", pkg.name)
        if m:
            sources.append((f"@acc/{m.group(1)}", _roles_in_accpkg(pkg)))

    # 2. Ecosystem build dir (present on dev hosts that build packs).
    for base in (_REPO_ROOT.parent.glob("acc-ecosystem*/build/family/*")):
        accpkg = base / "accpkg.yaml"
        if accpkg.is_file():
            meta = yaml.safe_load(accpkg.read_text(encoding="utf-8")) or {}
            name = meta.get("name")
            if name:
                sources.append((name, _roles_in_tree(base)))

    # 3. Installed packages root (the live registry on a deployed host).
    root = os.environ.get("ACC_PACKAGES_ROOT")
    if root and Path(root).is_dir():
        for base in Path(root).glob("*/*"):  # @scope/name
            if (base / "roles").is_dir():
                sources.append((f"@{base.parent.name.lstrip('@')}/{base.name}",
                                _roles_in_tree(base)))

    discovered = 0
    for name, roles in sources:
        if not roles:
            continue
        merged = set(packs.get(name, [])) | roles
        if merged != set(packs.get(name, [])):
            discovered += 1
        packs[name] = sorted(merged)

    manifest["packs"] = dict(sorted(packs.items()))
    header = (
        "# Auto-maintained role->pack manifest — source of truth for the\n"
        "# collective alignment check (tools/check_collectives.py) + the\n"
        "# /acc-collectives skill.  Regenerate after a pack changes:\n"
        "#   python tools/check_collectives.py --refresh-packs\n"
        "# Refresh is ADDITIVE (union) so packs unavailable on this host are\n"
        "# preserved.  control_roles = substrate; never served by a community pack.\n"
    )
    _PACKS_MANIFEST.write_text(
        header + yaml.safe_dump(manifest, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    print(f"refreshed {_PACKS_MANIFEST.relative_to(_REPO_ROOT)} "
          f"({len(packs)} packs, {discovered} updated)")
    return 0


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}: {msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}: {msg}")


# Non-collective YAML that may live in collectives/ (skipped by the checker).
_NON_SPEC_FILES = {"packs.yaml"}


def _iter_specs(dir_: Path) -> list[Path]:
    specs = [p for p in sorted(dir_.glob("*.yaml")) if p.name not in _NON_SPEC_FILES]
    if _ROOT_SPEC.is_file():
        specs.append(_ROOT_SPEC)
    return specs


def _validate_one(path: Path, ctx: dict, f: Findings) -> None:
    try:
        where = path.relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        where = path.name
    try:
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        f.error(where, f"YAML parse error: {exc}")
        return
    if not isinstance(spec, dict):
        f.error(where, "top-level YAML is not a mapping")
        return
    if not spec.get("collective_id"):
        f.error(where, "missing collective_id")

    control = ctx["control"]
    in_tree = ctx["in_tree"]
    role_to_packs = ctx["role_to_packs"]
    known_pack_names = ctx["known_pack_names"]
    catalog_names = ctx["catalog_names"]
    models = ctx["models"]

    declared = []
    for entry in (spec.get("required_packages") or []):
        if not isinstance(entry, str) or not _REQ_RE.match(entry):
            f.error(where, f"malformed required_packages entry: {entry!r}")
            continue
        name = entry.split("@", 2)
        pkg_name = "@" + name[1] if entry.startswith("@") else entry.split("@")[0]
        declared.append(pkg_name)
        if pkg_name not in known_pack_names and pkg_name not in catalog_names:
            f.warn(where, f"required pack {pkg_name} is unknown "
                          f"(not in packs.yaml or any catalog) — run --refresh-packs")
    declared_set = set(declared)
    used_packs: set[str] = set()

    def check_role(role: str, label: str, *, require_declared: bool = True) -> None:
        if role in control:
            return
        if role in in_tree:
            return
        providing = role_to_packs.get(role, set())
        if providing:
            covered = providing & declared_set
            if covered:
                used_packs.update(covered)
                return
            if not require_declared:
                # Sub-collective role_templates are provided by the CHILD
                # collective (which declares its own packs) — the parent need
                # not declare them.  Role is known, so this is fine.
                return
            f.error(where, f"{label} role {role!r} is served by "
                           f"{sorted(providing)} but none is in required_packages")
            return
        # Role unknown to the manifest entirely.
        if declared_set or not require_declared:
            f.warn(where, f"{label} role {role!r} is not in any known pack; "
                          f"run --refresh-packs if a new pack provides it")
        else:
            f.error(where, f"{label} role {role!r} is not CONTROL, not in-tree, "
                           f"and no required_packages declared to provide it")

    for agent in (spec.get("agents") or []):
        role = agent.get("role")
        if not role:
            f.error(where, "agent entry missing 'role'")
            continue
        check_role(role, "agent")
        model = agent.get("model")
        if model and model not in models:
            f.error(where, f"agent {role}: model {model!r} not in models.yaml")

    # Sub-collective role templates.
    for cid, sub in (spec.get("managed_sub_collectives") or {}).items():
        for role in (sub.get("role_templates") or []):
            check_role(role, f"sub-collective {cid}", require_declared=False)

    for pkg in declared_set - used_packs:
        # A pack declared but whose roles we couldn't confirm are used.  Only
        # warn when we KNOW the pack's roles (else we can't judge).
        if pkg in known_pack_names and not (
            ctx["pack_to_roles"].get(pkg, set()) & {
                a.get("role") for a in (spec.get("agents") or [])
            }
        ):
            f.warn(where, f"required pack {pkg} is declared but no agent uses "
                          f"a role it provides")


def _validate(dir_: Path) -> Findings:
    manifest = _load_manifest()
    pack_to_roles = {k: set(v) for k, v in manifest["packs"].items()}
    role_to_packs: dict[str, set[str]] = {}
    for pkg, roles in pack_to_roles.items():
        for r in roles:
            role_to_packs.setdefault(r, set()).add(pkg)
    ctx = {
        "control": set(manifest.get("control_roles") or []) | set(_CONTROL_ROLES),
        "in_tree": _in_tree_roles(),
        "pack_to_roles": pack_to_roles,
        "role_to_packs": role_to_packs,
        "known_pack_names": set(pack_to_roles),
        "catalog_names": _catalog_pack_names(),
        "models": _model_ids(),
    }
    f = Findings()
    specs = _iter_specs(dir_)
    if not specs:
        f.warn(dir_.as_posix(), "no collective specs found")
    for path in specs:
        _validate_one(path, ctx, f)
    return f


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=str(_COLLECTIVES_DIR),
                    help="directory of collective presets (default: collectives/)")
    ap.add_argument("--refresh-packs", action="store_true",
                    help="rebuild collectives/packs.yaml from available packs, then exit")
    ap.add_argument("--strict", action="store_true",
                    help="treat warnings as failures")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    args = ap.parse_args(argv)

    if args.refresh_packs:
        return _refresh_packs()

    f = _validate(Path(args.dir))

    if args.json:
        print(json.dumps({"errors": f.errors, "warnings": f.warnings}, indent=2))
    else:
        for w in f.warnings:
            print(f"  WARN  {w}")
        for e in f.errors:
            print(f"  ERROR {e}")
        n_specs = len(_iter_specs(Path(args.dir)))
        if not f.errors and not f.warnings:
            print(f"✓ {n_specs} collective(s) aligned — roles, models, packs all check out.")
        else:
            print(f"\n{len(f.errors)} error(s), {len(f.warnings)} warning(s) "
                  f"across {n_specs} collective(s).")

    if f.errors:
        return 1
    if args.strict and f.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
