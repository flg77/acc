"""``acc-cli sessions`` — read the durable per-session tracelog (acc.tracelog).

Post-session review of user sessions: prompts in/out, every tool call + its
execution output, and the Cat A/B/C governance verdicts — plus a ``verify``
gate that confirms Cat-ABC were present + evaluated for every turn (the edge
red-team requirement), exiting non-zero when incomplete so it can gate a script.
"""

from __future__ import annotations

import argparse
import json

from acc import tracelog


def register(sub: argparse._SubParsersAction) -> None:
    s = sub.add_parser(
        "sessions",
        help="Read the durable session tracelog (prompts, tool calls, Cat-ABC).",
    )
    ss = s.add_subparsers(dest="sessions_command", required=True,
                          metavar="SUBCOMMAND")

    lst = ss.add_parser("list", help="List sessions with a tracelog (newest first).")
    lst.set_defaults(func=_cmd_list)

    br = ss.add_parser("browse", help="Search and summarise sessions.")
    br.add_argument("query", nargs="?", default="")
    br.add_argument("--role", default="")
    br.add_argument("--since-days", type=float, default=None)
    br.add_argument("--json", action="store_true")
    br.set_defaults(func=_cmd_browse)

    rs = ss.add_parser("resume", help="Start a new session continuing an old one.")
    rs.add_argument("session_id")
    rs.add_argument("--context", action="store_true", help="Also print prior context.")
    rs.set_defaults(func=_cmd_resume)

    co = ss.add_parser("continue", help="Resume the most recent session.")
    co.add_argument("--context", action="store_true")
    co.set_defaults(func=_cmd_continue)

    rn = ss.add_parser("rename", help="Give a session a title.")
    rn.add_argument("session_id")
    rn.add_argument("title")
    rn.set_defaults(func=_cmd_rename)

    ex = ss.add_parser("export", help="Emit a session as JSONL.")
    ex.add_argument("session_id")
    ex.add_argument("--out", default=None)
    ex.set_defaults(func=_cmd_export)

    rt = ss.add_parser("retention", help="Show or apply the retention policy.")
    rt.add_argument("--apply", action="store_true", help="Remove what the policy allows.")
    rt.add_argument("--json", action="store_true")
    rt.set_defaults(func=_cmd_retention)

    show = ss.add_parser("show", help="Print a session's trace, turn by turn.")
    show.add_argument("session_id")
    show.add_argument("--json", action="store_true",
                      help="Emit raw JSONL records instead of the summary.")
    show.set_defaults(func=_cmd_show)

    ver = ss.add_parser(
        "verify",
        help="Verify Cat A/B/C were present + evaluated for every turn "
             "(exit 1 if incomplete).",
    )
    ver.add_argument("session_id")
    ver.set_defaults(func=_cmd_verify)


def _cmd_list(args: argparse.Namespace) -> int:
    ids = tracelog.list_sessions()
    if not ids:
        print(f"(no sessions under {tracelog.tracelog_dir()})")
        return 0
    for sid in ids:
        recs = tracelog.load_session(sid)
        turns = sum(1 for r in recs if r.get("kind") == tracelog.KIND_REPLY_OUT)
        tools = sum(1 for r in recs if r.get("kind") == tracelog.KIND_TOOL_CALL)
        print(f"{sid}\t{turns} turn(s)\t{tools} tool call(s)\t{len(recs)} records")
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    recs = tracelog.load_session(args.session_id)
    if not recs:
        print(f"(no trace for session {args.session_id!r})")
        return 1
    if args.json:
        for r in recs:
            print(json.dumps(r, ensure_ascii=False))
        return 0
    for r in recs:
        k = r.get("kind")
        tid = str(r.get("task_id", ""))[:8]
        if k == tracelog.KIND_PROMPT_IN:
            print(f"[{tid}] → IN  ({r.get('role')}): {str(r.get('prompt',''))[:140]}")
        elif k == tracelog.KIND_REPLY_OUT:
            flag = " [BLOCKED]" if r.get("blocked") else ""
            print(f"[{tid}] ← OUT{flag}: {str(r.get('reply',''))[:140]}")
        elif k == tracelog.KIND_TOOL_CALL:
            err = f"  err={r.get('error')}" if not r.get("ok") else ""
            print(f"[{tid}]   tool {r.get('tool_kind')}:{r.get('target')} "
                  f"ok={r.get('ok')}{err}")
        elif k == tracelog.KIND_GOVERNANCE:
            print(f"[{tid}]   gov Cat-{r.get('category')} = {r.get('verdict')} "
                  f"{r.get('detail','')}".rstrip())
        elif k == tracelog.KIND_REDTEAM:
            print(f"[{tid}]   redteam {r.get('challenge')} → {r.get('outcome')}")
        else:
            print(f"[{tid}] {k}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    v = tracelog.verify_governance(tracelog.load_session(args.session_id))
    print(json.dumps(v, indent=2, ensure_ascii=False))
    # Non-zero exit when Cat-ABC is incomplete → a scriptable post-session gate.
    return 0 if v.get("cat_abc_complete") else 1


# ---------------------------------------------------------------------------
# Lifecycle (PR: session resume + governed retention)
# ---------------------------------------------------------------------------


def _safe() -> None:
    import sys as _sys

    try:
        _sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass


def _cmd_browse(args) -> int:
    import json as _json

    from acc import sessions as S

    since = args.since_days * 86400 if args.since_days else None
    found = S.search(args.query, role=args.role, since_s=since)
    if args.json:
        print(_json.dumps([i.as_dict() for i in found], indent=2))
        return 0
    _safe()
    if not found:
        print("  no sessions match")
        return 0
    for info in found:
        import time as _time

        when = (
            _time.strftime("%Y-%m-%d %H:%M", _time.localtime(info.started_at))
            if info.started_at
            else "?"
        )
        flags = "  BLOCKED" if info.blocked else ""
        parent = f"  continues {info.parent}" if info.parent else ""
        title = f"  {info.title}" if info.title else ""
        print(
            f"  {info.session_id:<26} {when}  {info.turns:>3} turn(s)  "
            f"{','.join(info.roles) or '-'}{flags}{parent}{title}"
        )
    return 0


def _cmd_resume(args) -> int:
    from acc import sessions as S

    try:
        child = S.resume(args.session_id)
    except S.SessionError as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2
    _safe()
    print(f"  new session {child} continues {args.session_id}")
    print("  the original is untouched — resuming appends a link, never rewrites")
    if args.context:
        print()
        print(S.context_for(args.session_id))
    return 0


def _cmd_continue(args) -> int:
    from acc import sessions as S

    recent = S.most_recent()
    if recent is None:
        print("no sessions to continue", file=__import__("sys").stderr)
        return 2
    args.session_id = recent.session_id
    return _cmd_resume(args)


def _cmd_rename(args) -> int:
    from acc import sessions as S

    try:
        S.rename(args.session_id, args.title)
    except S.SessionError as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2
    _safe()
    print(f"  {args.session_id} titled {args.title!r}")
    return 0


def _cmd_export(args) -> int:
    from pathlib import Path as _Path

    from acc import sessions as S

    try:
        body = S.export(args.session_id)
    except S.SessionError as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 2
    if args.out:
        _Path(args.out).write_text(body + "\n", encoding="utf-8")
        _safe()
        print(f"  wrote {args.out}")
        return 0
    print(body)
    return 0


def _cmd_retention(args) -> int:
    import json as _json

    from acc import sessions as S

    policy = S.load_policy()
    due = S.due_for_removal()

    if args.json and not args.apply:
        print(_json.dumps(
            {"policy": policy.as_dict(), "due": [i.as_dict() for i in due]}, indent=2
        ))
        return 0

    _safe()
    if policy.keep_days <= 0:
        print("  retention: keep forever (the default)")
        print(f"  set a policy in {S.policy_path()} to change it")
        return 0

    print(f"  policy {policy.name!r}: keep {policy.keep_days} day(s)"
          f"{', blocked sessions kept regardless' if policy.keep_blocked else ''}")
    print(f"  {len(due)} session(s) currently eligible for removal")
    if not args.apply:
        for info in due:
            print(f"      {info.session_id}")
        if due:
            print()
            print("  `--apply` removes them; every removal is recorded with the")
            print("  policy it ran under and a digest of what was removed")
        return 0

    removed = S.apply_retention()
    print(f"  removed {len(removed)} session(s); each is recorded in the removal journal")
    for entry in removed:
        print(f"      {entry['session_id']}  sha256={entry['sha256'][:12]}...")
    return 0
