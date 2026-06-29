"""``acc-cli overlay …`` — validate + inspect a role's personalization overlay.

Overlays are **role-scoped runtime files** (``roles/<name>/{AGENTS.md, soul.md}``
+ optional local ``skills/`` / ``mcp/`` defs) layered onto the role's *signed
envelope* at prompt-assembly time (see ``acc/overlay.py`` +
``docs/agent-personalization-overlay-DRAFT.md`` §0).  This is the operator-facing
counterpart to the boot-time loader:

* ``overlay validate <role>`` — lint the overlay files against the role's
  envelope (the ``validate`` gate from the proposal: rejects Tier-0/forbidden
  keys, unknown keys, unknown ``user_profile``, out-of-envelope enables).  Exits
  ``0`` clean, ``1`` when problems are found.
* ``overlay show <role>`` — dump the resolved :class:`acc.overlay.EffectiveProfile`
  (effective skills/MCPs, per-field provenance, dropped requests, local grants,
  user profile) as JSON/YAML — the effective-profile observability surface.

This lives in ``acc-cli`` (the runtime/operator CLI), not ``acc-pkg`` (the
packaging toolchain): overlays tune an *installed* role's runtime persona, they
are not a packaging artifact.  It mirrors ``acc-cli role show`` so both inspect
the same live ``roles/`` tree.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any



# ---------------------------------------------------------------------------
# Registrar
# ---------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    """Attach the ``overlay`` command tree."""
    overlay = sub.add_parser(
        "overlay", help="Validate or inspect a role's personalization overlay."
    )
    overlay_sub = overlay.add_subparsers(
        dest="overlay_command", required=True, metavar="ACTION"
    )

    # validate
    val = overlay_sub.add_parser(
        "validate",
        help="Lint <role>'s overlay files against its signed envelope.",
    )
    val.add_argument("name", help="Role directory name (e.g. coding_agent).")
    val.set_defaults(func=_cmd_validate)

    # show
    show = overlay_sub.add_parser(
        "show",
        help="Dump <role>'s resolved EffectiveProfile (provenance + dropped + grants).",
    )
    show.add_argument("name", help="Role directory name (e.g. coding_agent).")
    show.add_argument(
        "--format",
        choices=("json", "yaml"),
        default="json",
        help="Output format (default: json).",
    )
    show.add_argument(
        "--allow-unsigned",
        action="store_true",
        help=(
            "Preview as if the operator admitted user-added role-local "
            "skills/ / mcp/ defs (local, this-agent-only, unsigned). Off by "
            "default — matches the prod resolve."
        ),
    )
    show.set_defaults(func=_cmd_show)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _load_role(name: str):
    """Load a RoleDefinitionConfig + its role dir, or (None, err-printed).

    Resolves the roles-root with the SAME resolver the agent boot path uses
    (``acc.tui.path_resolution.resolve_manifest_root``), not the plain
    ``ACC_ROLES_ROOT``-or-``"roles"`` lookup — so ``overlay validate``/``show``
    inspect exactly the directory the agent would load its overlay from when
    ``ACC_ROLES_ROOT`` is unset (repo-anchored, then CWD-relative).
    """
    from acc.role_loader import RoleLoader  # noqa: PLC0415
    from acc.tui.path_resolution import resolve_manifest_root  # noqa: PLC0415

    roots = str(resolve_manifest_root("ACC_ROLES_ROOT", "roles"))
    role_def = RoleLoader(roots, name).load()
    if role_def is None:
        print(f"role {name!r} not found under {roots!r}", file=sys.stderr)
        return None, None
    return role_def, Path(roots) / name


def _cmd_validate(args: argparse.Namespace) -> int:
    from acc.overlay import (  # noqa: PLC0415
        load_overlay_sources,
        validate_overlay,
    )

    role_def, role_dir = _load_role(args.name)
    if role_def is None:
        return 1

    sources = load_overlay_sources(role_dir)
    problems = validate_overlay(role_def, sources)
    if not problems:
        layers = [s.layer for s in sources] or ["(none)"]
        print(f"overlay {args.name}: clean ({', '.join(layers)})")
        return 0
    for problem in problems:
        print(f"overlay {args.name}: {problem}", file=sys.stderr)
    return 1


def _cmd_show(args: argparse.Namespace) -> int:
    from acc.overlay import (  # noqa: PLC0415
        discover_local_capabilities,
        load_overlay_sources,
        resolve_overlay,
    )

    role_def, role_dir = _load_role(args.name)
    if role_def is None:
        return 1

    sources = load_overlay_sources(role_dir)
    local_skills, local_mcps = discover_local_capabilities(role_dir)
    profile = resolve_overlay(
        role_def,
        sources,
        local_skills=local_skills,
        local_mcps=local_mcps,
        allow_unsigned=args.allow_unsigned,
    )

    data: dict[str, Any] = {
        "role": args.name,
        "allow_unsigned": bool(args.allow_unsigned),
        "local_candidates": {"skills": local_skills, "mcps": local_mcps},
        "effective_profile": profile.to_dict(),
    }

    if args.format == "yaml":
        try:
            import yaml  # noqa: PLC0415

            print(yaml.dump(data, sort_keys=False, default_flow_style=False))
            return 0
        except ImportError:
            pass
    print(json.dumps(data, indent=2, default=str))
    return 0
