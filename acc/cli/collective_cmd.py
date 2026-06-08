"""``acc-cli collective …`` — work with the declarative agentset spec.

Subcommands:

* ``synthesize <spec> [-o overlay.yaml]`` — render the spec as a
  podman-compose overlay; prints to stdout when ``-o`` is omitted.
* ``validate <spec>`` — parse + Pydantic-validate the spec; exits
  0 clean, 1 on failure.
* ``diff <spec>`` — print the reconcile diff (to_start / to_stop /
  unchanged) against the current ``podman ps`` state.

PR-B of the Ecosystem-led workflow rework.  ``acc-deploy.sh apply``
wraps ``synthesize`` to produce the overlay it hands to podman-compose.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from acc.collective import (
    CollectiveSpec,
    dump_compose_overlay,
    load_collective,
    reconcile,
    roles_to_compose,
)


# ---------------------------------------------------------------------------
# Registrar
# ---------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    """Attach the ``collective`` command tree."""
    root = sub.add_parser(
        "collective",
        help="Work with collective.yaml (declarative agentset).",
    )
    root_sub = root.add_subparsers(
        dest="collective_command", required=True, metavar="ACTION",
    )

    synth_p = root_sub.add_parser(
        "synthesize",
        help="Render the spec as a podman-compose overlay (-o file | stdout).",
    )
    synth_p.add_argument("spec", help="Path to collective.yaml.")
    synth_p.add_argument(
        "-o", "--output", default=None,
        help="Write overlay to this path instead of stdout.",
    )
    synth_p.add_argument(
        "--image",
        default="localhost/acc-agent-core:0.2.0",
        help="Image to bake into every synthesized service.",
    )
    synth_p.set_defaults(func=_cmd_synthesize)

    val_p = root_sub.add_parser("validate", help="Parse + validate the spec.")
    val_p.add_argument("spec", help="Path to collective.yaml.")
    val_p.set_defaults(func=_cmd_validate)

    diff_p = root_sub.add_parser(
        "diff",
        help="Print reconcile diff (to_start / to_stop / unchanged) "
             "against the live podman ps state.",
    )
    diff_p.add_argument("spec", help="Path to collective.yaml.")
    diff_p.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of human-readable text.",
    )
    diff_p.set_defaults(func=_cmd_diff)

    # Stage 1.5.3 — boot-time package fetch
    status_p = root_sub.add_parser(
        "pkg-status",
        help="Show which required_packages are missing from the local registry.",
    )
    status_p.add_argument("spec", help="Path to collective.yaml.")
    status_p.add_argument(
        "--json", action="store_true",
        help="Emit JSON instead of human-readable text.",
    )
    status_p.set_defaults(func=_cmd_pkg_status)

    install_p = root_sub.add_parser(
        "pkg-install",
        help="Resolve + fetch + verify + install every required_packages "
             "entry that isn't already satisfied.",
    )
    install_p.add_argument("spec", help="Path to collective.yaml.")
    install_p.add_argument(
        "--allow-unsigned", action="store_true",
        help="Operator-explicit bypass of the signing floor (audit-logged).",
    )
    install_p.add_argument(
        "--json", action="store_true",
        help="Emit JSON results.",
    )
    install_p.set_defaults(func=_cmd_pkg_install)

    # Stage 1.6b — direct install entry point used by the operator
    # reconciler.  Takes a single ``@scope/name@constraint`` spec and
    # invokes fetch_and_install directly, no collective.yaml in the
    # loop.  The output JSON shape matches `pkg-install` so the
    # operator can reuse the same parser.
    direct_p = root_sub.add_parser(
        "pkg-install-direct",
        help="Install one @scope/name@constraint without a "
             "collective.yaml; used by the operator reconciler.",
    )
    direct_p.add_argument("package_spec",
                          help="@scope/name@constraint, e.g. @acc/coding-roles@^1.2")
    direct_p.add_argument(
        "--allow-unsigned", action="store_true",
        help="Operator-explicit bypass of the signing floor (audit-logged).",
    )
    direct_p.add_argument(
        "--catalog",
        help="Optional catalog id to pin resolution to a single catalog.",
    )
    direct_p.add_argument(
        "--json", action="store_true",
        help="Emit JSON results (always on for reconciler use).",
    )
    direct_p.set_defaults(func=_cmd_pkg_install_direct)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_synthesize(args: argparse.Namespace) -> int:
    try:
        spec = load_collective(args.spec)
    except Exception as exc:  # noqa: BLE001
        print(f"acc-cli collective synthesize: {exc}", file=sys.stderr)
        return 1
    if args.output:
        dump_compose_overlay(spec, args.output, image=args.image)
        print(f"wrote {args.output}", file=sys.stderr)
        return 0
    overlay = roles_to_compose(spec, image=args.image)
    yaml.safe_dump(overlay, sys.stdout, sort_keys=False,
                    default_flow_style=False, indent=2)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    try:
        spec = load_collective(args.spec)
    except Exception as exc:  # noqa: BLE001
        print(f"acc-cli collective validate: invalid: {exc}", file=sys.stderr)
        return 1
    # ASCII-only — Windows cp1252 consoles choke on '✓' / '—'.
    print(
        f"OK: {args.spec} valid -- collective_id={spec.collective_id} "
        f"agents={len(spec.agents)}"
    )
    return 0


def _cmd_pkg_status(args: argparse.Namespace) -> int:
    """Stage 1.5.3: report which required_packages need installing."""
    try:
        spec = load_collective(args.spec)
    except Exception as exc:  # noqa: BLE001
        print(f"acc-cli collective pkg-status: {exc}", file=sys.stderr)
        return 1
    missing = spec.unsatisfied_requirements()
    payload = {
        "collective_id": spec.collective_id,
        "required_packages": list(spec.required_packages),
        "missing": missing,
        "satisfied": [
            s for s in spec.required_packages if s not in missing
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"collective: {spec.collective_id}")
        print(f"  required ({len(spec.required_packages)}):")
        for spec_str in spec.required_packages:
            mark = "MISSING" if spec_str in missing else "ok     "
            print(f"    [{mark}] {spec_str}")
    return 0 if not missing else 3   # 3 = EXIT_DEPS per acc-pkg CLI contract


def _cmd_pkg_install(args: argparse.Namespace) -> int:
    """Stage 1.5.3: fetch + verify + install missing required_packages."""
    try:
        spec = load_collective(args.spec)
    except Exception as exc:  # noqa: BLE001
        print(f"acc-cli collective pkg-install: {exc}", file=sys.stderr)
        return 1

    # Lazy import — keeps the CLI tree loadable in environments without
    # the pkg subsystem (legacy stand-alone).
    try:
        from acc.collective import collective_workspace  # noqa: PLC0415
        from acc.pkg.fetch import (  # noqa: PLC0415
            FetchError,
            fetch_and_install_closure,
        )
    except ImportError as exc:
        print(
            f"acc-cli collective pkg-install: acc.pkg unavailable ({exc})",
            file=sys.stderr,
        )
        return 1

    workspace = collective_workspace(args.spec)
    missing = spec.unsatisfied_requirements()
    if not missing:
        if args.json:
            print(json.dumps({
                "collective_id": spec.collective_id,
                "installed": [],
                "already_satisfied": True,
            }, indent=2, sort_keys=True))
        else:
            print(
                f"OK: all required_packages already installed "
                f"for {spec.collective_id}"
            )
        return 0

    results: list[dict] = []
    failures: list[dict] = []
    for spec_str in missing:
        # Parse '@scope/name@constraint' — we keep the import light
        # by reusing collective.parse_required_package.
        from acc.collective import parse_required_package  # noqa: PLC0415
        name, constraint = parse_required_package(spec_str)
        try:
            res = fetch_and_install_closure(
                name,
                constraint,
                workspace=workspace,
                allow_unsigned=args.allow_unsigned,
            )
            results.append({
                "spec": spec_str,
                "installed": f"{res.install.entry.name}@{res.install.entry.version}",
                "install_path": str(res.install.install_path),
                "was_already_installed": res.install.was_already_installed,
            })
        except FetchError as exc:
            failures.append({"spec": spec_str, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 — surface but don't crash
            failures.append({"spec": spec_str, "error": f"{type(exc).__name__}: {exc}"})

    payload = {
        "collective_id": spec.collective_id,
        "installed": results,
        "failed": failures,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for r in results:
            print(f"  installed {r['installed']} -> {r['install_path']}")
        for f in failures:
            print(f"  FAILED   {f['spec']}: {f['error']}", file=sys.stderr)
    return 0 if not failures else 3


def _cmd_pkg_install_direct(args: argparse.Namespace) -> int:
    """Stage 1.6b — direct single-package install for the operator
    reconciler.

    Output shape matches ``pkg-install`` so the operator's JSON parser
    can reuse one struct.
    """
    try:
        from acc.collective import parse_required_package  # noqa: PLC0415
        from acc.pkg.fetch import (  # noqa: PLC0415
            FetchError,
            fetch_and_install_closure,
        )
    except ImportError as exc:
        print(
            f"acc-cli collective pkg-install-direct: acc.pkg unavailable ({exc})",
            file=sys.stderr,
        )
        return 1

    try:
        name, constraint = parse_required_package(args.package_spec)
    except ValueError as exc:
        print(f"acc-cli collective pkg-install-direct: {exc}", file=sys.stderr)
        return 1

    results: list[dict] = []
    failures: list[dict] = []
    try:
        res = fetch_and_install_closure(
            name, constraint,
            allow_unsigned=args.allow_unsigned,
        )
        results.append({
            "spec": args.package_spec,
            "installed": f"{res.install.entry.name}@{res.install.entry.version}",
            "install_path": str(res.install.install_path),
            "was_already_installed": res.install.was_already_installed,
        })
    except FetchError as exc:
        failures.append({"spec": args.package_spec, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        failures.append({"spec": args.package_spec, "error": f"{type(exc).__name__}: {exc}"})

    payload = {"installed": results, "failed": failures}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for r in results:
            print(f"  installed {r['installed']} -> {r['install_path']}")
        for f in failures:
            print(f"  FAILED   {f['spec']}: {f['error']}", file=sys.stderr)
    return 0 if not failures else 3


def _cmd_diff(args: argparse.Namespace) -> int:
    try:
        spec = load_collective(args.spec)
    except Exception as exc:  # noqa: BLE001
        print(f"acc-cli collective diff: {exc}", file=sys.stderr)
        return 1
    result = reconcile(spec)
    if args.json:
        print(json.dumps({
            "to_start": result.to_start,
            "to_stop": result.to_stop,
            "unchanged": result.unchanged,
        }, indent=2))
        return 0
    print(f"Reconcile diff for {args.spec} (collective_id={spec.collective_id})")
    print(f"  to_start ({len(result.to_start)}):")
    for n in result.to_start:
        print(f"    + {n}")
    print(f"  to_stop  ({len(result.to_stop)}):")
    for n in result.to_stop:
        print(f"    - {n}")
    print(f"  unchanged ({len(result.unchanged)}):")
    for n in result.unchanged:
        print(f"    = {n}")
    return 0
