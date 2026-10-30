"""``acc-cli ingest session`` -- a coding session's summary into ACC (CS-05).

    acc-cli ingest session --executor claude-vertex --event -        # hook JSON on stdin
    acc-cli ingest session --executor codex --event '<notify JSON>'  # Codex notify argument
    acc-cli ingest session --executor cursor --transcript t.jsonl --cwd . --session S

Run by each coding tool's end-of-session hook (CS-06).  Writes one note per
session (replaced on re-runs) and closes the work items the session left
RUNNING; see :mod:`acc.session_ingest`.  The summary is a plain digest unless
``--model`` names a ``models.yaml`` entry -- name a local one, because the
model reads the conversation.  The hook never fails the tool: errors are
printed to stderr and the exit code is 0 unless ``--strict``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("ingest", help="Bring a coding agent's session into ACC (CS-05).")
    sp = p.add_subparsers(dest="ingest_command", required=True, metavar="WHAT")
    s = sp.add_parser("session", help="Summarise one coding session into ACC's notes.")
    s.add_argument("--executor", required=True, help="claude-vertex, codex, cursor, ...")
    s.add_argument("--event", default=None,
                   help="The hook's JSON event, or '-' to read it from stdin.")
    s.add_argument("--transcript", default=None, help="Transcript file (JSONL or text).")
    s.add_argument("--session", default=None, help="Session id (else from the event).")
    s.add_argument("--cwd", default=None, help="Working directory (else from the event, else here).")
    s.add_argument("--model", default=None,
                   help="models.yaml entry that writes the summary (a local one); default: a digest.")
    s.add_argument("--collective-id", default=None)
    s.add_argument("--redis-url", default=None,
                   help="ACC's Redis (default ACC_REDIS_URL, else redis://localhost:6379).")
    s.add_argument("--strict", action="store_true", help="Exit non-zero on any error.")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=_cmd_session)


def _event(arg: str | None) -> dict:
    if not arg:
        return {}
    raw = sys.stdin.read() if arg == "-" else arg
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _first(event: dict, *keys: str) -> str:
    for k in keys:
        v = event.get(k)
        if v:
            return str(v)
    return ""


def _summarizer(model_id: str):
    """A ``complete(system, prompt) -> text`` on the named model, through the
    CLI's one recorded LLM construction site."""
    from acc.cli.llm_cmd import _build_llm_only  # noqa: PLC0415
    from acc.config import load_config  # noqa: PLC0415
    from acc.models import get_model, model_env  # noqa: PLC0415

    entry = get_model(model_id)
    if entry is None:
        raise ValueError(f"no model {model_id!r} in models.yaml")
    os.environ.update(model_env(entry))
    llm = _build_llm_only(load_config(os.environ.get("ACC_CONFIG_PATH", "acc-config.yaml")))

    async def summarize(system: str, prompt: str) -> str:
        result = await llm.complete(system, prompt)
        if isinstance(result, dict):
            return str(result.get("content", "") or result.get("text", "") or "")
        return str(result or "")

    return summarize


async def _run(args: argparse.Namespace) -> dict:
    import redis as redis_lib  # noqa: PLC0415

    from acc.cli._common import default_collective  # noqa: PLC0415
    from acc.external_work import ExternalNoteStore, ExternalWorkStore  # noqa: PLC0415
    from acc.mcp.server import workspace_of  # noqa: PLC0415
    from acc import session_ingest as si  # noqa: PLC0415

    event = _event(args.event)
    session_id = args.session or _first(event, "session_id", "thread-id", "conversation_id", "turn-id")
    if not session_id:
        raise ValueError("no session id: pass --session or an event that carries one")
    cwd = args.cwd or _first(event, "cwd") or os.getcwd()
    transcript = args.transcript or _first(event, "transcript_path")
    if transcript and Path(transcript).is_file():
        turns = si.parse_transcript(Path(transcript).read_text(encoding="utf-8", errors="replace"))
    else:
        turns = si.turns_from_event(event)
    repo, _ = workspace_of(cwd)
    branch, diffstat = si.git_state(cwd)

    redis_url = args.redis_url or os.environ.get("ACC_REDIS_URL", "redis://localhost:6379")
    client = redis_lib.from_url(redis_url, password=os.environ.get("ACC_REDIS_PASSWORD") or None)
    client.ping()                           # no in-process fallback: the note would be lost
    cid = args.collective_id or default_collective()
    return await si.ingest_session(
        session_id=session_id, executor=args.executor.strip(), turns=turns,
        work=ExternalWorkStore(client, cid), notes=ExternalNoteStore(client, cid),
        repo=repo, branch=branch, diffstat=diffstat,
        summarize=_summarizer(args.model) if args.model else None,
    )


def _cmd_session(args: argparse.Namespace) -> int:
    try:
        out = asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 -- a hook must not break the tool
        print(f"acc ingest session: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1 if args.strict else 0
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        closed = ", ".join(out["closed_items"]) or "none"
        print(f"{out['note_id']} ({out['summarized_by']}); closed items: {closed}")
    return 0
