"""``acc-cli scan`` — known-vulnerable components, beside signature verification.

    acc-cli scan [--package MANIFEST] [--fail-on low|medium|high|critical]
                 [--json] [--propose]

Signature verification answers "is this from who it says". This answers "is what
is inside it known-vulnerable" — a correctly signed package can contain a
dependency with a published advisory.

**Exit 2 means the scan could not run.** That is deliberately distinct from
exit 0: a scanner reporting "no findings" because it could not read its advisory
data would let an operator conclude there is nothing to fix.

Blocking is opt-in via ``--fail-on``. Advisory data is noisy and severity is
contextual; a hard block by default turns a false positive into an outage.
"""

from __future__ import annotations

import argparse
import json
import sys

from acc import vulnscan


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("scan", help="Scan components for known vulnerabilities.")
    p.add_argument(
        "--package", default=None,
        help="Scan a package manifest's declared dependencies instead of the runtime.",
    )
    p.add_argument(
        "--fail-on", default="", choices=["", *vulnscan.SEVERITIES],
        help="Exit non-zero when a finding at or above this severity exists.",
    )
    p.add_argument(
        "--propose", action="store_true",
        help="Emit an oversight proposal with the evidence attached.",
    )
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_scan)


def _cmd_scan(args: argparse.Namespace) -> int:
    try:
        if args.package:
            components = vulnscan.package_components(args.package)
            result = vulnscan.scan(components, where=str(args.package))
        else:
            result = vulnscan.scan_runtime()
    except vulnscan.ScanError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.propose:
        print(json.dumps(vulnscan.as_oversight_proposal(result), indent=2))
        return vulnscan.exit_code(result, fail_on=args.fail_on)

    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
        return vulnscan.exit_code(result, fail_on=args.fail_on)

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):  # pragma: no cover
        pass

    if not result.usable:
        print(f"  SCAN DID NOT RUN: {result.unavailable}")
        print()
        print("  This is not a clean result — nothing was checked.")
        return 2

    print(f"  scanned {result.scanned} component(s) against "
          f"{result.advisory_count} advisor{'y' if result.advisory_count == 1 else 'ies'}")
    if result.stale:
        days = (result.advisory_age_s or 0) / 86400
        print(f"  WARNING: advisory data is {days:.0f} days old")

    if not result.findings:
        print()
        print("  no known-vulnerable components found")
        return 0

    print()
    width = max(len(f.package) for f in result.findings)
    for finding in sorted(
        result.findings, key=lambda f: -vulnscan._RANK.get(f.severity, 0)
    ):
        fix = f"  fixed in {finding.advisory.fixed_in}" if finding.advisory.fixed_in else ""
        print(
            f"  {finding.severity.upper():<9} {finding.package:<{width}} "
            f"{finding.version:<12} {finding.advisory.id}{fix}"
        )
        print(f"  {'':<9} {'':<{width}} {finding.advisory.summary}")

    print()
    print(f"  {len(result.findings)} finding(s), worst {result.worst()}")
    if not args.fail_on:
        print("  reported as a decision, not a block — `--propose` raises it for review,")
        print("  `--fail-on <severity>` fails this command for automation")
    return vulnscan.exit_code(result, fail_on=args.fail_on)
