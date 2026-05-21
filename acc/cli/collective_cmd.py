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
