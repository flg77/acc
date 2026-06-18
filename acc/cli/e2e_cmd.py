"""``acc-cli e2e …`` — golden-prompt suite runner (PR-K, D-005).

Verbs:

* ``acc-cli e2e list``                 — list known golden prompts (no
  network access; just walks ``examples/golden_prompts/``).
* ``acc-cli e2e validate``             — schema-validate every YAML
  under the golden-prompts root.  Exit code reflects pass/fail; used
  in CI to gate malformed prompts.
* ``acc-cli e2e run [name]``           — connect to NATS, send the
  named prompt (or all of them when no name given), apply the
  ``expects`` block to each reply, print a summary.  Exit code 0
  iff every prompt passes.
* ``acc-cli e2e show <name>``          — print one prompt's full YAML
  + expectations for review.

See ``acc.golden_prompts`` for the shared loader / assertion engine
used by all three runner modes (CLI, TUI Diagnostics, scheduled
maintenance agent).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("acc.cli.e2e")


# ---------------------------------------------------------------------------
# Registrar
# ---------------------------------------------------------------------------


def register(sub: argparse._SubParsersAction) -> None:
    """Attach the ``e2e`` command tree."""
    e2e = sub.add_parser(
        "e2e",
        help="Golden-prompt suite — list, validate, run, show.",
    )
    e2e_sub = e2e.add_subparsers(
        dest="e2e_command", required=True, metavar="ACTION",
    )

    # list
    list_p = e2e_sub.add_parser(
        "list", help="List known golden prompts (no network access).",
    )
    list_p.add_argument(
        "--root", default=None,
        help="Override the golden-prompts directory (default: "
             "examples/golden_prompts).",
    )
    list_p.set_defaults(func=_cmd_list)

    # validate
    val_p = e2e_sub.add_parser(
        "validate",
        help="Schema-check every YAML under the golden-prompts root.",
    )
    val_p.add_argument("--root", default=None)
    val_p.set_defaults(func=_cmd_validate)

    # show
    show_p = e2e_sub.add_parser(
        "show", help="Print one prompt's full definition.",
    )
    show_p.add_argument("name")
    show_p.add_argument("--root", default=None)
    show_p.add_argument(
        "--format", choices=("yaml", "json"), default="yaml",
    )
    show_p.set_defaults(func=_cmd_show)

    # run
    run_p = e2e_sub.add_parser(
        "run",
        help="Execute one or all prompts against a live NATS stack.",
    )
    run_p.add_argument(
        "name", nargs="?",
        help="Prompt name to run.  Omit to run every prompt.",
    )
    run_p.add_argument("--root", default=None)
    run_p.add_argument(
        "--collective-id", default=None,
        help="Override ACC_COLLECTIVE_ID for this run.",
    )
    run_p.add_argument(
        "--nats-url", default=None,
        help="Override ACC_NATS_URL for this run.",
    )
    run_p.add_argument(
        "--json", action="store_true",
        help="Emit results as JSON (one object per prompt) — useful "
             "for ingestion by the scheduled maintenance agent.",
    )
    run_p.add_argument(
        "--history", default=None, metavar="PATH",
        help="Append one JSONL row per result to PATH (e.g. "
             "test/history/golden.jsonl).  Builds a regression "
             "archive across scheduled runs.",
    )
    run_p.add_argument(
        "--loop", type=float, default=None, metavar="SECONDS",
        help="Re-run the suite every SECONDS (scheduled mode).  "
             "Runs forever until interrupted; combine with --history "
             "to accrue a trend.  Omit for a single run.",
    )
    run_p.set_defaults(func=_cmd_run)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_root(arg_root: str | None) -> Path:
    from acc.golden_prompts import _default_root  # noqa: PLC0415
    return Path(arg_root) if arg_root else _default_root()


# ---------------------------------------------------------------------------
# `list`
# ---------------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> int:
    from acc.golden_prompts import load_all  # noqa: PLC0415
    prompts = load_all(_resolve_root(args.root))
    if not prompts:
        print("(no prompts found)")
        return 0
    print(f"{len(prompts)} golden prompt(s):")
    for p in prompts:
        print(
            f"  {p.name:40s}  role={p.target_role:20s}  "
            f"{p.description or '—'}",
        )
    return 0


# ---------------------------------------------------------------------------
# `validate`
# ---------------------------------------------------------------------------


def _cmd_validate(args: argparse.Namespace) -> int:
    """Re-parse every YAML; non-zero exit iff any fails."""
    from acc.golden_prompts import load_one  # noqa: PLC0415
    root = _resolve_root(args.root)
    if not root.is_dir():
        print(f"ERROR: golden-prompts root not found: {root}", file=sys.stderr)
        return 2
    files = sorted(root.glob("*.yaml"))
    if not files:
        print("(no .yaml files in root)")
        return 0
    failed = 0
    for path in files:
        try:
            p = load_one(path)
            print(f"  OK   {path.name}  ({p.name})")
        except Exception as exc:
            failed += 1
            print(f"  FAIL {path.name}  {exc}")
    print(f"\ntotal: {len(files) - failed}/{len(files)} passed")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# `show`
# ---------------------------------------------------------------------------


def _cmd_show(args: argparse.Namespace) -> int:
    from acc.golden_prompts import load_all  # noqa: PLC0415
    prompts = load_all(_resolve_root(args.root))
    for p in prompts:
        if p.name == args.name:
            if args.format == "json":
                print(json.dumps(p.model_dump(), indent=2))
            else:
                import yaml  # noqa: PLC0415
                print(yaml.dump(p.model_dump(), sort_keys=False))
            return 0
    print(f"ERROR: prompt {args.name!r} not found", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# `run`
# ---------------------------------------------------------------------------


async def _run_suite_once(prompts, *, cid: str, nats_url: str):
    """Connect, run the prompts sequentially, disconnect; return the
    results list.  Raises on connect failure so the caller can decide
    whether to retry (loop mode) or exit (single-run mode)."""
    from acc.golden_prompts import run_all, run_one  # noqa: PLC0415
    from acc.tui.client import NATSObserver  # noqa: PLC0415
    import asyncio as _asyncio  # noqa: PLC0415

    queue = _asyncio.Queue(maxsize=50)
    observer = NATSObserver(
        nats_url=nats_url, collective_id=cid, update_queue=queue,
    )
    await observer.connect()
    # connect() opens the NATS connection but does NOT install the subject
    # subscription — without this the observer never routes TASK_COMPLETE and
    # every prompt times out with empty output (headless e2e false-negative).
    await observer.subscribe()
    try:
        async def _drain():
            while True:
                try:
                    await queue.get()
                except _asyncio.CancelledError:
                    return
                except Exception:
                    return
        drain_task = _asyncio.create_task(_drain(), name="e2e-drain")
        try:
            if len(prompts) == 1:
                return [await run_one(
                    prompts[0], observer=observer, collective_id=cid,
                )]
            return await run_all(
                prompts, observer=observer, collective_id=cid,
            )
        finally:
            drain_task.cancel()
            try:
                await drain_task
            except Exception:
                pass
    finally:
        try:
            await observer.close()
        except Exception:
            pass


async def _cmd_run(args: argparse.Namespace) -> int:
    from acc.golden_prompts import (  # noqa: PLC0415
        format_summary, load_all, persist_results,
    )
    import asyncio as _asyncio  # noqa: PLC0415

    prompts = load_all(_resolve_root(args.root))
    if not prompts:
        print("(no prompts found)", file=sys.stderr)
        return 1

    # Optional filter by name.
    if args.name:
        prompts = [p for p in prompts if p.name == args.name]
        if not prompts:
            print(f"ERROR: prompt {args.name!r} not found", file=sys.stderr)
            return 1

    cid = args.collective_id or os.environ.get("ACC_COLLECTIVE_ID", "sol-01")
    nats_url = args.nats_url or os.environ.get(
        "ACC_NATS_URL", "nats://localhost:4222",
    )

    async def _one_pass() -> int:
        try:
            results = await _run_suite_once(
                prompts, cid=cid, nats_url=nats_url,
            )
        except Exception as exc:
            print(
                f"ERROR: cannot connect to NATS {nats_url}: {exc}",
                file=sys.stderr,
            )
            return 2

        if args.history:
            persist_results(
                results, args.history,
                run_meta={"collective_id": cid, "nats_url": nats_url},
            )
        if args.json:
            print(json.dumps([r.model_dump() for r in results], indent=2))
        else:
            print(format_summary(results))
        failed = sum(1 for r in results if not r.passed)
        return 1 if failed else 0

    # Scheduled (loop) mode — re-run every --loop seconds until
    # interrupted.  Exit code reflects the LAST pass.
    if args.loop is not None and args.loop > 0:
        last_rc = 0
        try:
            while True:
                last_rc = await _one_pass()
                print(
                    f"[loop] next run in {args.loop:.0f}s "
                    f"(Ctrl-C to stop)",
                    file=sys.stderr,
                )
                await _asyncio.sleep(args.loop)
        except (KeyboardInterrupt, _asyncio.CancelledError):
            print("[loop] stopped", file=sys.stderr)
            return last_rc

    return await _one_pass()
