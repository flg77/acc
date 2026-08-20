"""``acc-cli doctor`` — one read-only report on a deployment's health.

    acc-cli doctor [--json] [--probe] [--check NAME] [--quiet]

Exits non-zero when a **broken** check fails.  A degraded endpoint does not
set the exit code: it is usually a transient upstream blip, and a monitor that
pages on it teaches people to ignore the page.

The rendering lives here; the checks live in :mod:`acc.preflight` so the TUI
and the web GUI report the same answer rather than growing a second opinion.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import preflight

_MARK = {
    preflight.Severity.OK: "ok",
    preflight.Severity.DRIFTED: "DRIFT",
    preflight.Severity.DEGRADED: "DEGRADED",
    preflight.Severity.BROKEN: "BROKEN",
}


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("doctor", help="Report deployment health (read-only).")
    p.add_argument("--json", action="store_true", help="Emit a machine-readable report.")
    p.add_argument(
        "--probe",
        action="store_true",
        help="Also dial configured endpoints (off by default; makes network calls).",
    )
    p.add_argument("--check", default=None, help="Run only the named check.")
    p.add_argument(
        "--quiet", action="store_true", help="Print only checks that are not OK."
    )
    p.set_defaults(func=_cmd_doctor)


def _make_stdout_lossy() -> None:
    """Never let an un-encodable character take the health report down.

    Windows consoles default to cp1252, which has no arrow glyph. A diagnostic
    command that crashes while reporting a fault is worse than one that prints
    a replacement character, so degrade the stream rather than raise.
    """
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover - non-reconfigurable stream
        pass


def _cmd_doctor(args: argparse.Namespace) -> int:
    ctx = preflight.Context(probe_endpoints=args.probe)
    results = preflight.run(ctx, only=args.check)

    if args.check and not results:
        known = ", ".join(name for name, _ in preflight.registry())
        print(f"unknown check {args.check!r}; known: {known}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(preflight.report(results), indent=2))
        return preflight.exit_code(results)

    _make_stdout_lossy()
    shown = [r for r in results if not (args.quiet and r.ok)]
    width = max((len(r.name) for r in shown), default=0)
    for r in shown:
        where = f"[{r.subject}] " if r.subject and not r.ok else ""
        print(f"  {_MARK[r.severity]:<9} {r.name:<{width}}  {where}{r.summary}")
        # Only when it adds something: a detail identical to the summary is
        # noise, and this report is read when someone is already frustrated.
        if r.detail and not r.ok and r.detail.strip() != r.summary.strip():
            print(f"  {'':<9} {'':<{width}}  {r.detail}")

    broken = sum(1 for r in results if r.severity is preflight.Severity.BROKEN)
    degraded = sum(1 for r in results if r.severity is preflight.Severity.DEGRADED)
    drifted = sum(1 for r in results if r.severity is preflight.Severity.DRIFTED)
    print()
    if not (broken or degraded or drifted):
        print("healthy — no faults found")
    else:
        print(f"{broken} broken, {degraded} degraded, {drifted} drifted")
        if broken:
            print("Fix the broken checks first; the others may resolve with them.")
    return preflight.exit_code(results)
