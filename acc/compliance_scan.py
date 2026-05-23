"""Scheduled compliance scan runner (PR-Z3f).

Runs the deterministic gap analysis against every loaded framework plus
a Cat-A self-challenge, writes the audit docs, and emits rule proposals
— on demand or on a loop.  Designed to be wired to a schedule exactly
like the golden-prompt runner (see ``docs/golden_prompts_scheduling.md``):

    python -m acc.compliance_scan                 # one-shot
    python -m acc.compliance_scan --loop 86400    # daily
    # or via systemd timer / k8s CronJob

Proposals respect the ``learned_rule_promotion`` setpoint (propose vs
auto).  All persistence goes to the same writable stores the TUI uses,
so a scheduled run's findings show up in the Compliance pane's Rule
Proposals table for review.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

logger = logging.getLogger("acc.compliance_scan")


def run_all_scans() -> dict:
    """Run gap analysis for every framework + a Cat-A self-challenge.

    Returns a summary dict; writes reports + proposals as side effects.
    """
    from acc.frameworks import load_all_frameworks  # noqa: PLC0415
    from acc.gap_analysis import analyze_gaps, dump_gap_report  # noqa: PLC0415
    from acc.governance_inventory import load_all_layers  # noqa: PLC0415
    from acc.rule_proposals import proposals_from_gap_report  # noqa: PLC0415
    from acc.self_challenge import (  # noqa: PLC0415
        challenge_cat_a, dump_challenge_report, proposals_from_challenge,
    )

    layers = load_all_layers()
    summary: dict = {"ts": time.time(), "frameworks": [], "self_challenge": None}

    for fw in load_all_frameworks():
        try:
            report = analyze_gaps(layers, fw)
            dump_gap_report(report)
            proposals = proposals_from_gap_report(report)
            summary["frameworks"].append({
                "framework_id": fw.framework_id,
                "coverage_pct": round(report.coverage_pct, 1),
                "gaps": report.gap_count,
                "proposals": len(proposals),
            })
        except Exception as exc:  # one bad framework must not abort the run
            logger.warning("compliance_scan: %s failed (%s)", fw.framework_id, exc)

    try:
        ch = challenge_cat_a(layers)
        dump_challenge_report(ch)
        ch_proposals = proposals_from_challenge(ch)
        summary["self_challenge"] = {
            "findings": ch.total, "proposals": len(ch_proposals),
        }
    except Exception as exc:
        logger.warning("compliance_scan: self-challenge failed (%s)", exc)

    return summary


def main(argv: Optional[list[str]] = None) -> int:
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="acc.compliance_scan",
        description="Run compliance gap analysis + self-challenge.",
    )
    parser.add_argument(
        "--loop", type=float, default=0.0,
        help="Repeat every N seconds (0 = one-shot).",
    )
    args = parser.parse_args(argv)

    while True:
        summary = run_all_scans()
        print(json.dumps(summary, ensure_ascii=False))
        if args.loop <= 0:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
