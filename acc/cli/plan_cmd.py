"""``acc-cli plan submit|watch`` — drive PLAN signals from the CLI.

* ``submit`` reads a PLAN JSON document from a file (or stdin) and
  publishes it on ``acc.{cid}.plan.submit`` for the arbiter's
  :class:`acc.plan.PlanExecutor` to ingest.  With ``--watch`` the CLI
  also streams the arbiter's PLAN re-broadcasts so the operator sees
  ``step_progress`` transitions live until the plan reaches a terminal
  state (every step COMPLETE or FAILED).
* ``watch`` is the read-only counterpart — useful when a plan was
  submitted by another tool (TUI, external orchestrator) and you want
  to follow it from the shell.

The wire format mirrors :mod:`acc.cli._common` (msgpack-of-json) so
contributors can mix-and-match ``acc-cli nats sub`` and
``acc-cli plan watch`` against the same plan.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from acc.cli._common import (
    connect_nats,
    decode_payload,
    default_collective,
    encode_payload,
)


_TERMINAL = {"COMPLETE", "FAILED"}


def register(sub: argparse._SubParsersAction) -> None:
    plan = sub.add_parser("plan", help="Submit and monitor PLAN signals.")
    plan_sub = plan.add_subparsers(
        dest="plan_command", required=True, metavar="ACTION"
    )

    submit_p = plan_sub.add_parser(
        "submit", help="Submit a PLAN payload from a JSON file (or stdin)."
    )
    submit_p.add_argument(
        "plan_file",
        help="Path to the JSON file.  Use '-' to read from stdin.",
    )
    submit_p.add_argument(
        "--collective", "-c",
        default=None,
        help="Override the plan's collective_id (default: payload value or $ACC_COLLECTIVE_ID).",
    )
    submit_p.add_argument(
        "--watch",
        action="store_true",
        help="After submit, stream PLAN re-broadcasts until terminal state.",
    )
    submit_p.add_argument(
        "--timeout-s",
        type=int,
        default=300,
        help="Watch timeout in seconds (only with --watch; default 300).",
    )
    submit_p.set_defaults(func=_cmd_submit)

    watch_p = plan_sub.add_parser(
        "watch", help="Watch the PLAN broadcast for a known plan_id."
    )
    watch_p.add_argument("plan_id")
    watch_p.add_argument(
        "--collective", "-c",
        default=None,
        help="Collective id (default: $ACC_COLLECTIVE_ID).",
    )
    watch_p.add_argument(
        "--timeout-s",
        type=int,
        default=600,
        help="Watch timeout in seconds (default 600 — Ctrl-C also stops).",
    )
    watch_p.set_defaults(func=_cmd_watch)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _cmd_submit(args: argparse.Namespace) -> int:
    raw = _read_plan(args.plan_file)
    if raw is None:
        return 1
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"plan: invalid JSON in {args.plan_file!r}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(payload, dict):
        print("plan: payload must be a JSON object", file=sys.stderr)
        return 1

    cid = args.collective or payload.get("collective_id") or default_collective()
    payload["collective_id"] = cid
    payload.setdefault("signal_type", "PLAN")
    plan_id = str(payload.get("plan_id", "")).strip()
    if not plan_id:
        print("plan: payload missing 'plan_id'", file=sys.stderr)
        return 1
    if not isinstance(payload.get("steps"), list) or not payload["steps"]:
        print("plan: payload missing non-empty 'steps' list", file=sys.stderr)
        return 1

    submit_subject = f"acc.{cid}.plan.submit"
    nc = await connect_nats()
    try:
        # If --watch, subscribe FIRST so we don't miss the first broadcast
        # that the arbiter emits immediately on register_plan().
        watcher_task: asyncio.Task | None = None
        if args.watch:
            watcher_task = asyncio.create_task(
                _stream_plan(nc, cid, plan_id, args.timeout_s)
            )

        await nc.publish(submit_subject, encode_payload(payload))
        await nc.flush(timeout=2.0)
        print(
            f"plan: submitted {plan_id!r} ({len(payload['steps'])} steps) → {submit_subject}"
        )

        if watcher_task is not None:
            return await watcher_task
    finally:
        await nc.drain()
    return 0


async def _cmd_watch(args: argparse.Namespace) -> int:
    cid = args.collective or default_collective()
    nc = await connect_nats()
    try:
        return await _stream_plan(nc, cid, args.plan_id, args.timeout_s)
    finally:
        await nc.drain()


# ---------------------------------------------------------------------------
# Watcher
# ---------------------------------------------------------------------------


async def _stream_plan(nc: Any, cid: str, plan_id: str, timeout_s: int) -> int:
    """Subscribe to the plan broadcast subject and pretty-print transitions.

    Exits with 0 when every step is COMPLETE, 1 when at least one step
    is FAILED at terminal time, and 2 when the watch times out without
    a terminal state (the plan may still be running — try again with
    a longer ``--timeout-s``).
    """
    subject = f"acc.{cid}.plan.{plan_id}"
    last_progress: dict[str, str] = {}
    terminal: asyncio.Event = asyncio.Event()
    final_status: dict[str, dict[str, str]] = {}  # bag for the final progress map

    def _format_progress(progress: dict[str, str]) -> str:
        # Ordered display: PENDING < RUNNING < COMPLETE/FAILED so the
        # eye tracks left-to-right progress through the DAG.
        items = sorted(progress.items())
        cells = []
        for sid, status in items:
            colour = {
                "PENDING":  "\033[90m",   # grey
                "RUNNING":  "\033[33m",   # yellow
                "COMPLETE": "\033[32m",   # green
                "FAILED":   "\033[31m",   # red
            }.get(status, "")
            reset = "\033[0m" if colour else ""
            cells.append(f"{colour}{sid}={status}{reset}")
        return "  ".join(cells)

    async def _handle(msg: Any) -> None:
        decoded = decode_payload(msg.data)
        if not isinstance(decoded, dict):
            return
        if decoded.get("plan_id") != plan_id:
            return
        progress = decoded.get("step_progress")
        if not isinstance(progress, dict):
            return

        # Skip if nothing actually changed (the executor re-broadcasts on
        # every dispatch round; some rounds touch only internal state).
        if progress == last_progress:
            return
        last_progress.clear()
        last_progress.update({str(k): str(v) for k, v in progress.items()})

        ts = time.strftime("%H:%M:%S")
        print(f"[{ts}] {plan_id}  {_format_progress(last_progress)}")
        sys.stdout.flush()

        if last_progress and all(s in _TERMINAL for s in last_progress.values()):
            final_status["progress"] = dict(last_progress)
            terminal.set()

    await nc.subscribe(subject, cb=_handle)
    print(
        f"plan: watching {subject} for up to {timeout_s}s (Ctrl-C to stop)…",
        file=sys.stderr,
    )
    try:
        await asyncio.wait_for(terminal.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        print(f"plan: watch timeout after {timeout_s}s", file=sys.stderr)
        return 2

    progress = final_status.get("progress", {})
    if any(s == "FAILED" for s in progress.values()):
        print("plan: TERMINAL with at least one FAILED step", file=sys.stderr)
        return 1
    print("plan: TERMINAL — all steps COMPLETE", file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_plan(plan_file: str) -> str | None:
    if plan_file == "-":
        return sys.stdin.read()
    path = Path(plan_file)
    if not path.is_file():
        print(f"plan: file not found: {plan_file!r}", file=sys.stderr)
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"plan: read failed: {exc}", file=sys.stderr)
        return None
