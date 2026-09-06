"""``acc-cli instance`` — a collective bound to an owner, a posture and its
own state roots (`20260906-acc-instance`, HG-40.1a).

``profile`` stays the posture; ``instance`` is the binding. ``synth`` renders
the compose overlay that runs the instance with its roots; ``env`` prints what
the operator surfaces need to attach to it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

from acc import identity, instances as I


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("instance", help="Instances: a collective bound to an owner, a posture and its own state.")
    sp = p.add_subparsers(dest="instance_command", required=True, metavar="ACTION")

    ls = sp.add_parser("list", help="Instances on this host.")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_list)

    sh = sp.add_parser("show", help="One instance: record, roots, collective definition.")
    sh.add_argument("id")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=_cmd_show)

    cr = sp.add_parser("create", help="Create an instance (record + collective definition + state roots).")
    cr.add_argument("id", help="The instance id — it is the collective id (a DNS label).")
    cr.add_argument("--owner", default="", help="source:subject (default: the principal running this command).")
    cr.add_argument("--profile", default="", help="Deployment profile (posture) the instance runs under.")
    cr.add_argument("--hub", default="", help="Hub collective id; omit for standalone.")
    cr.add_argument("--pack", action="append", default=[], help="@scope/name@constraint (repeatable) — the installed set.")
    cr.add_argument("--agent", action="append", default=[], help="Domain role to run (repeatable).")
    cr.add_argument("--stack-profile", default="edge", help="Control set: edge-min | edge | full | dc (default edge).")
    cr.add_argument("--note", default="")
    cr.set_defaults(func=_cmd_create)

    ar = sp.add_parser("archive", help="Mark an instance archived (state stays; forget is erasure).")
    ar.add_argument("id")
    ar.set_defaults(func=_cmd_archive)

    ex = sp.add_parser("export", help="Emit a portable instance document (definition, never state).")
    ex.add_argument("id")
    ex.add_argument("-o", "--out", default="-")
    ex.set_defaults(func=_cmd_export)

    im = sp.add_parser("import", help="Create an instance from an exported document, owned here.")
    im.add_argument("path")
    im.add_argument("--owner", default="", help="source:subject (default: the principal running this command).")
    im.add_argument("--id", default="", help="New instance id (default: the document's).")
    im.set_defaults(func=_cmd_import)

    sy = sp.add_parser("synth", help="Render the podman-compose overlay that runs the instance with its roots.")
    sy.add_argument("id")
    sy.add_argument("-o", "--out", default="-")
    sy.add_argument("--image", default="localhost/acc-agent-core:0.2.0")
    sy.set_defaults(func=_cmd_synth)

    en = sp.add_parser("env", help="Shell environment for the surfaces (TUI, acc-cli) to attach to the instance.")
    en.add_argument("id")
    en.set_defaults(func=_cmd_env)


def _me() -> str:
    try:
        return identity.current().attribution()
    except Exception:  # noqa: BLE001 -- the caller sees a clear refusal instead
        return ""


def _fail(exc: Exception) -> int:
    print(str(exc), file=sys.stderr)
    return 1


def _cmd_list(args: argparse.Namespace) -> int:
    ids = I.list_instances()
    rows = [I.load_instance(i) for i in ids]
    if args.json:
        print(json.dumps([r.as_dict() for r in rows], indent=2))
        return 0
    print("instances:")
    if not rows:
        print("  none — acc-cli instance create <id> --owner <source:subject>")
        return 0
    for r in rows:
        flags = " (archived)" if r.archived else ""
        hub = f"  hub {r.hub}" if r.hub else ""
        print(f"  {r.id:<24} owner {r.owner:<28} profile {r.profile or '-':<14}{hub}{flags}")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        inst = I.load_instance(args.id)
    except I.InstanceError as exc:
        return _fail(exc)
    roots = {k: str(v) for k, v in I.state_roots(inst).items()}
    cpath = I.instance_dir(inst.id) / I.COLLECTIVE_FILE
    collective = yaml.safe_load(cpath.read_text(encoding="utf-8")) if cpath.is_file() else {}
    if args.json:
        print(json.dumps({"instance": inst.as_dict(), "roots": roots, "collective": collective}, indent=2))
        return 0
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(inst.created_at)) if inst.created_at else "-"
    print(f"instance {inst.id}{' (archived)' if inst.archived else ''}")
    print(f"  owner:    {inst.owner}")
    print(f"  profile:  {inst.profile or '-'}")
    print(f"  hub:      {inst.hub or '- (standalone)'}")
    print(f"  created:  {when}{' by ' + inst.created_by if inst.created_by else ''}")
    if inst.note:
        print(f"  note:     {inst.note}")
    print("  roots:")
    for k, v in roots.items():
        print(f"    {k:<9} {v}")
    agents = [a.get("role") for a in (collective.get("agents") or [])]
    print(f"  agents:   {', '.join(str(a) for a in agents) or '-'}")
    print(f"  packs:    {', '.join(collective.get('required_packages') or []) or '-'}")
    return 0


def _cmd_create(args: argparse.Namespace) -> int:
    owner = args.owner or _me()
    try:
        inst = I.create(
            args.id, owner=owner, profile=args.profile, hub=args.hub,
            packs=args.pack, agents=args.agent, stack_profile=args.stack_profile,
            created_by=_me(), note=args.note,
        )
    except I.InstanceError as exc:
        return _fail(exc)
    print(f"  created instance {inst.id!r} for {inst.owner}"
          f"{' under profile ' + inst.profile if inst.profile else ''}"
          f"{' with hub ' + inst.hub if inst.hub else ''}")
    print(f"  {I.instance_dir(inst.id)}")
    print(f"  next: acc-cli instance synth {inst.id} -o container/production/instance.{inst.id}.yml"
          f" && ./acc-deploy.sh instance up {inst.id}")
    return 0


def _cmd_archive(args: argparse.Namespace) -> int:
    try:
        inst = I.archive(args.id)
    except I.InstanceError as exc:
        return _fail(exc)
    print(f"  archived {inst.id!r} — its state stays under {I.instance_dir(inst.id)};"
          f" `acc-cli memory forget` is erasure")
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    try:
        ex = I.export_instance(args.id)
    except I.InstanceError as exc:
        return _fail(exc)
    text = yaml.safe_dump(ex.document, sort_keys=False, allow_unicode=True)
    if args.out == "-":
        print(text, end="")
    else:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"  wrote {args.out}")
    print(f"  not carried (state): {', '.join(ex.state_excluded)}", file=sys.stderr)
    return 0


def _cmd_import(args: argparse.Namespace) -> int:
    try:
        document = yaml.safe_load(Path(args.path).read_text(encoding="utf-8"))
        inst = I.import_instance(
            document, owner=args.owner or _me(), instance_id=args.id or None, created_by=_me(),
        )
    except (OSError, yaml.YAMLError) as exc:
        return _fail(exc)
    except I.InstanceError as exc:
        return _fail(exc)
    print(f"  imported instance {inst.id!r} for {inst.owner} — state starts empty")
    return 0


def _cmd_synth(args: argparse.Namespace) -> int:
    try:
        inst = I.load_instance(args.id)
        overlay = I.compose_overlay(inst, image=args.image)
    except I.InstanceError as exc:
        return _fail(exc)
    text = yaml.safe_dump(overlay, sort_keys=False, default_flow_style=False, indent=2)
    if args.out == "-":
        print(text, end="")
    else:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"  wrote {args.out} ({len(overlay.get('services', {}))} cells)")
    return 0


def _cmd_env(args: argparse.Namespace) -> int:
    try:
        inst = I.load_instance(args.id)
    except I.InstanceError as exc:
        return _fail(exc)
    for k, v in I.surface_env(inst).items():
        print(f"export {k}={v}")
    return 0
