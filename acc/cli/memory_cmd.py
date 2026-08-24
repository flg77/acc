"""``acc-cli memory`` — what the collective has learned, and removing a person from it.

    acc-cli memory notes [--scope <scope>]
    acc-cli memory forget --person <id> [--apply] [--quorum N]

`forget` **defaults to a dry run**. Erasure is the one operation here that
cannot be undone, and the counts it reports are the point of looking first:
a person may be behind more notes than anyone expected, and finding that out
afterwards is finding it out too late.

What it touches is deliberately narrow. The audit trail records *that* something
happened; memory records *what was said*. This removes what was said. The audit
record of the request stays, and the erasure leaves its own journal entry — there
is no path here that produces a silent deletion.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import memory_forget

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_UNSUPPORTED = 2


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("memory", help="Durable memory notes, and erasure.")
    sp = p.add_subparsers(dest="memory_command", required=True, metavar="ACTION")

    ls = sp.add_parser("notes", help="Show the notes a scope would read.")
    ls.add_argument("--scope", default="local",
                    help="Memory scope (default: the operator's own).")
    ls.add_argument("--role", default="", help="Role whose notes to read.")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=_cmd_notes)

    fg = sp.add_parser(
        "forget",
        help="Remove a person's episodes and reconcile the notes drawn from them.",
    )
    fg.add_argument("--person", required=True,
                    help="Requester identity, e.g. slack:U123 (the room is ignored).")
    fg.add_argument("--apply", action="store_true",
                    help="Actually erase. Without this it is a dry run.")
    fg.add_argument("--quorum", type=int, default=memory_forget.QUORUM_DEFAULT,
                    help="People required to keep a note published.")
    fg.add_argument("--json", action="store_true")
    fg.set_defaults(func=_cmd_forget)


def _safe() -> None:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _backends():
    """The vector + redis clients, from the ambient config."""
    from acc.config import load_config
    from acc.memory_reflection import LOCAL_SCOPE  # noqa: F401  (re-export guard)

    cfg = load_config()
    vector = redis_client = None
    try:
        from acc.backends.vector_lancedb import LanceDBBackend
        vector = LanceDBBackend(cfg.vector.path)
    except Exception as exc:  # noqa: BLE001
        print(f"memory: no vector backend ({exc})")
    try:
        from acc.agent import _build_redis_client
        redis_client = _build_redis_client(cfg)
    except Exception:  # noqa: BLE001
        redis_client = None
    return cfg, vector, redis_client


def _cmd_notes(args: argparse.Namespace) -> int:
    _safe()
    cfg, _, redis_client = _backends()
    role = args.role or cfg.agent.role
    from acc.memory_reflection import read_hot_cache

    notes = read_hot_cache(
        redis_client, cfg.agent.collective_id, role, args.scope,
        bandwidth=100,
    )
    if args.json:
        print(json.dumps({"scope": args.scope, "role": role, "notes": notes},
                         indent=2))
        return EXIT_OK
    if not notes:
        print(f"No notes readable in scope {args.scope!r} for role {role!r}.")
        return EXIT_OK
    print(f"Notes readable in {args.scope} (role {role}):")
    for note in notes:
        print(f"  - {note}")
    return EXIT_OK


def _cmd_forget(args: argparse.Namespace) -> int:
    _safe()
    cfg, vector, redis_client = _backends()
    if vector is None:
        return EXIT_UNSUPPORTED

    report = memory_forget.forget_person(
        vector, args.person,
        redis_client=redis_client,
        collective_id=cfg.agent.collective_id,
        k=args.quorum,
        dry_run=not args.apply,
    )

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
        return EXIT_OK if report.supported else EXIT_UNSUPPORTED

    if not report.supported:
        # Said plainly rather than as a warning under a success line: an
        # erasure that quietly did not erase is the worst outcome available.
        print(f"REFUSED: {report.unsupported}")
        print("Nothing was removed. Do not treat this as an erasure.")
        return EXIT_UNSUPPORTED

    verb = "Would remove" if not args.apply else "Removed"
    print(f"{verb} for {report.person}:")
    print(f"  episodes        {report.episodes_removed}")
    print(f"  notes deleted   {report.notes_deleted}  (no sources left)")
    print(f"  notes demoted   {report.notes_demoted}  (below quorum → private)")
    print(f"  notes rebuilt   {report.notes_rebuilt}  (still above quorum)")
    if report.unpublished_from:
        print(f"  unpublished from {', '.join(report.unpublished_from)}")
    if not args.apply:
        print("\nDry run. Re-run with --apply to erase.")
    return EXIT_OK
