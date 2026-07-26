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
